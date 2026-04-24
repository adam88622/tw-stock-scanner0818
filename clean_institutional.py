"""
資料清洗 + Enrichment

輸入：data/institutional_full.parquet
輸出：
    data/institutional_clean.parquet   主資料(加衍生欄位)
    data/stocks_index.parquet          股票主索引(代號/名稱/市場/活躍區間/紀錄數)
    data/trading_calendar.parquet      交易日曆(date/year/month/quarter/weekday)
    data/data_quality_report.txt       資料品質報告
"""
import os
import sys
import pandas as pd
import numpy as np
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.chdir(os.path.dirname(os.path.abspath(__file__)))

OUT_DIR = 'data'

print("[1/5] 讀取主檔 ...")
df = pd.read_parquet(os.path.join(OUT_DIR, 'institutional_full.parquet'))
n0 = len(df)
print(f"     原始: {n0:,} 筆")

print("[2/5] 清洗 ...")
# 4 碼數字過濾(已是 4 碼，雙保險)
df = df[df['stock_id'].str.match(r'^\d{4}$')]
# 確保 dtype
df['date'] = pd.to_datetime(df['date'])
for c in ['foreign_net','sitc_net','dealer_net','total_net']:
    df[c] = df[c].astype('int32')
# 重新驗證 total
calc = df['foreign_net'] + df['sitc_net'] + df['dealer_net']
bad = (calc != df['total_net']).sum()
print(f"     清洗後: {len(df):,} 筆 (移除 {n0-len(df):,})")
print(f"     total 公式錯誤: {bad}")

print("[3/5] 加入衍生欄位 ...")
df['year'] = df['date'].dt.year.astype('int16')
df['month'] = df['date'].dt.month.astype('int8')
df['quarter'] = df['date'].dt.quarter.astype('int8')
df['weekday'] = df['date'].dt.weekday.astype('int8')  # 0=Mon
# 主導法人(誰買最多/賣最多)
abs_arr = df[['foreign_net','sitc_net','dealer_net']].abs().values
idx = abs_arr.argmax(axis=1)
df['dominant_inst'] = pd.Categorical(
    np.array(['foreign','sitc','dealer'])[idx],
    categories=['foreign','sitc','dealer']
)
# 排序
df = df.sort_values(['date','market','stock_id']).reset_index(drop=True)

clean_path = os.path.join(OUT_DIR, 'institutional_clean.parquet')
df.to_parquet(clean_path, index=False, compression='snappy')
sz_clean = os.path.getsize(clean_path) / (1024*1024)
print(f"     寫入 {clean_path} ({sz_clean:.1f} MB)")

print("[4/5] 建立股票主索引 ...")
stocks = df.groupby(['stock_id','name','market'], observed=True).agg(
    first_date=('date','min'),
    last_date=('date','max'),
    days=('date','nunique'),
    foreign_net_sum=('foreign_net','sum'),
    sitc_net_sum=('sitc_net','sum'),
    dealer_net_sum=('dealer_net','sum'),
).reset_index().sort_values(['market','stock_id'])
stocks_path = os.path.join(OUT_DIR, 'stocks_index.parquet')
stocks.to_parquet(stocks_path, index=False, compression='snappy')
print(f"     {len(stocks):,} 檔股票 -> {stocks_path}")

print("[5/5] 建立交易日曆 ...")
cal = (df.groupby('date')
         .agg(twse_count=('market', lambda s: (s=='twse').sum()),
              tpex_count=('market', lambda s: (s=='tpex').sum()))
         .reset_index())
cal['year'] = cal['date'].dt.year.astype('int16')
cal['month'] = cal['date'].dt.month.astype('int8')
cal['quarter'] = cal['date'].dt.quarter.astype('int8')
cal['weekday'] = cal['date'].dt.weekday.astype('int8')
cal_path = os.path.join(OUT_DIR, 'trading_calendar.parquet')
cal.to_parquet(cal_path, index=False, compression='snappy')
print(f"     {len(cal):,} 個交易日 -> {cal_path}")

# ---- 品質報告 ----
print("\n[report] 產生資料品質報告 ...")
report_path = os.path.join(OUT_DIR, 'data_quality_report.txt')
with open(report_path, 'w', encoding='utf-8') as f:
    w = lambda *a: print(*a, file=f)
    w("台股三大法人買賣超 — 資料品質報告")
    w(f"產生時間: {datetime.now():%Y-%m-%d %H:%M:%S}")
    w("="*70)
    w()
    w("【核心指標】")
    w(f"  總筆數      : {len(df):,}")
    w(f"  日期範圍    : {df['date'].min().date()}  ~  {df['date'].max().date()}")
    w(f"  涵蓋股票    : {df['stock_id'].nunique():,} 檔")
    w(f"  涵蓋交易日  : {df['date'].nunique():,} 天")
    w(f"  total 公式錯誤: {bad}")
    w(f"  重複 (date,stock_id): {df.duplicated(['date','stock_id']).sum()}")
    w(f"  NULL 計數: {df.isnull().sum().sum()}")
    w()
    w("【依市場分群】")
    by_m = df.groupby('market', observed=True).agg(
        rows=('stock_id','size'), days=('date','nunique'),
        stocks=('stock_id','nunique'),
        first=('date','min'), last=('date','max'))
    w(by_m.to_string())
    w()
    w("【每年涵蓋天數】(可對照農曆年差異)")
    yr_days = df.groupby([df['date'].dt.year,'market'], observed=True)['date'].nunique().unstack(level=1).fillna(0).astype(int)
    w(yr_days.to_string())
    w()
    w("【每年涵蓋股票數】")
    yr_st = df.groupby([df['date'].dt.year,'market'], observed=True)['stock_id'].nunique().unstack(level=1).fillna(0).astype(int)
    w(yr_st.to_string())
    w()
    w("【極值前 5 (絕對值)】")
    for c in ['foreign_net','sitc_net','dealer_net','total_net']:
        top = df.reindex(df[c].abs().sort_values(ascending=False).index).head(5)
        w(f"-- {c} --")
        w(top[['date','stock_id','name','foreign_net','sitc_net','dealer_net','total_net']].to_string(index=False))
        w()
    w("【欄位說明】")
    w("  date            交易日")
    w("  stock_id        證券代號 (4 碼)")
    w("  name            證券名稱 (最新)")
    w("  market          twse=上市 / tpex=上櫃")
    w("  foreign_net     外資買賣超(不含自營)，>0 買超 / <0 賣超，單位:張")
    w("  sitc_net        投信買賣超")
    w("  dealer_net      自營商買賣超(合計)")
    w("  total_net       三大法人合計 = foreign+sitc+dealer")
    w("  year/month/quarter/weekday  日期分量")
    w("  dominant_inst   當日絕對量最大的法人 ('foreign'|'sitc'|'dealer')")
    w()
    w("【讀檔範例】")
    w("import pandas as pd")
    w(f"df = pd.read_parquet(r'{os.path.abspath(clean_path)}')")
    w(f"stocks = pd.read_parquet(r'{os.path.abspath(stocks_path)}')")
    w(f"calendar = pd.read_parquet(r'{os.path.abspath(cal_path)}')")
    w()
    w("# 範例 1: 台積電近 30 日法人籌碼")
    w("tsmc = df[df['stock_id']=='2330'].tail(30)")
    w()
    w("# 範例 2: 找出 2024 年外資累積買超 Top 10")
    w("y24 = df[df['year']==2024]")
    w("top = y24.groupby(['stock_id','name'])['foreign_net'].sum().nlargest(10)")
    w()
    w("# 範例 3: 三大法人同步買超(共識買盤)的日子")
    w("consensus = df[(df['foreign_net']>0)&(df['sitc_net']>0)&(df['dealer_net']>0)]")

print(f"     -> {report_path}\n")
print("="*70)
with open(report_path, encoding='utf-8') as f:
    print(f.read())
