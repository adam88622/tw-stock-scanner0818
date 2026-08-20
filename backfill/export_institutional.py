"""
將 institutional 表 + stocks 表 join 後，清理並輸出成單一資料包。

產出:
    data/institutional_full.parquet    主檔（推薦使用）
    data/institutional_full.csv        備份/人類可讀
    data/institutional_summary.txt     資料概況（日期範圍、覆蓋率、欄位說明）

用法:
    python export_institutional.py              # 產出 parquet + csv + summary
    python export_institutional.py --no-csv     # 只產 parquet（CSV 較大可省）
"""
import sys
import os
import sqlite3
from datetime import datetime
import pandas as pd

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _PROJECT_ROOT)
os.chdir(_PROJECT_ROOT)

DB_PATH = 'db/scanner.db'
OUT_DIR = 'data'
os.makedirs(OUT_DIR, exist_ok=True)


def main():
    no_csv = '--no-csv' in sys.argv

    print(f"[export] 連線 {DB_PATH}")
    conn = sqlite3.connect(DB_PATH)

    print("[export] 讀取 institutional + stocks（JOIN）...")
    sql = """
        SELECT
            i.date           AS date,
            i.stock_id       AS stock_id,
            s.name           AS name,
            s.market         AS market,
            i.foreign_buy    AS foreign_net,
            i.sitc_buy       AS sitc_net,
            i.dealer_buy     AS dealer_net,
            i.total_buy      AS total_net
        FROM institutional i
        LEFT JOIN stocks s ON s.stock_id = i.stock_id
        ORDER BY i.date, i.stock_id
    """
    df = pd.read_sql_query(sql, conn, parse_dates=['date'])
    conn.close()

    print(f"[export] 原始筆數: {len(df):,}")

    # --- 清理 ---
    # 1) 缺 stock 名/市場的丟掉（極少數，通常是已下市股票）
    before = len(df)
    df = df.dropna(subset=['name', 'market'])
    if len(df) < before:
        print(f"[export] 移除 stocks JOIN 失敗 {before - len(df):,} 筆")

    # 2) 全 0 的紀錄丟掉（無意義 — 沒人買也沒人賣）
    before = len(df)
    df = df[(df['foreign_net'] != 0) | (df['sitc_net'] != 0) | (df['dealer_net'] != 0)]
    print(f"[export] 移除全 0 紀錄 {before - len(df):,} 筆，剩 {len(df):,} 筆")

    # 3) 確保整數型別（買賣超單位：張）
    for col in ['foreign_net', 'sitc_net', 'dealer_net', 'total_net']:
        df[col] = df[col].astype('int32')

    # 4) market 用 category 省空間
    df['market'] = df['market'].astype('category')
    df['stock_id'] = df['stock_id'].astype('string')
    df['name'] = df['name'].astype('string')

    # 5) 排序
    df = df.sort_values(['date', 'market', 'stock_id']).reset_index(drop=True)

    # --- 輸出 ---
    pq_path = os.path.join(OUT_DIR, 'institutional_full.parquet')
    df.to_parquet(pq_path, index=False, compression='snappy')
    pq_size_mb = os.path.getsize(pq_path) / (1024 * 1024)
    print(f"[export] Parquet -> {pq_path} ({pq_size_mb:.1f} MB)")

    if not no_csv:
        csv_path = os.path.join(OUT_DIR, 'institutional_full.csv')
        df.to_csv(csv_path, index=False, encoding='utf-8-sig')
        csv_size_mb = os.path.getsize(csv_path) / (1024 * 1024)
        print(f"[export] CSV     -> {csv_path} ({csv_size_mb:.1f} MB)")

    # --- summary ---
    summary_path = os.path.join(OUT_DIR, 'institutional_summary.txt')
    by_market = df.groupby('market', observed=True).agg(
        rows=('stock_id', 'size'),
        first_date=('date', 'min'),
        last_date=('date', 'max'),
        unique_stocks=('stock_id', 'nunique'),
        unique_dates=('date', 'nunique'),
    )
    with open(summary_path, 'w', encoding='utf-8') as f:
        f.write("台股三大法人買賣超 — 完整資料包\n")
        f.write(f"產生時間: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write("=" * 60 + "\n\n")
        f.write(f"總筆數: {len(df):,}\n")
        f.write(f"日期範圍: {df['date'].min().date()}  ~  {df['date'].max().date()}\n")
        f.write(f"涵蓋股票: {df['stock_id'].nunique():,} 檔\n")
        f.write(f"涵蓋交易日: {df['date'].nunique():,} 天\n\n")
        f.write("依市場分群:\n")
        f.write(by_market.to_string() + "\n\n")
        f.write("=" * 60 + "\n")
        f.write("欄位說明 (單位：張)\n")
        f.write("=" * 60 + "\n")
        f.write("date         交易日 (datetime)\n")
        f.write("stock_id     證券代號 (4 碼)\n")
        f.write("name         證券名稱\n")
        f.write("market       twse=上市 / tpex=上櫃\n")
        f.write("foreign_net  外資買賣超（不含自營），>0 買超 / <0 賣超\n")
        f.write("sitc_net     投信買賣超\n")
        f.write("dealer_net   自營商買賣超（合計，含自行+避險）\n")
        f.write("total_net    三大法人合計 = foreign + sitc + dealer\n\n")
        f.write("=" * 60 + "\n")
        f.write("讀檔範例 (Python)\n")
        f.write("=" * 60 + "\n")
        f.write("import pandas as pd\n")
        f.write(f"df = pd.read_parquet(r'{os.path.abspath(pq_path)}')\n")
        f.write("# 篩近一年外資買超累積前 20\n")
        f.write("recent = df[df['date'] >= df['date'].max() - pd.Timedelta('365D')]\n")
        f.write("top = recent.groupby(['stock_id','name'])['foreign_net'].sum().nlargest(20)\n")
        f.write("print(top)\n")

    print(f"[export] Summary -> {summary_path}")
    print()
    print("=" * 60)
    with open(summary_path, encoding='utf-8') as f:
        print(f.read())


if __name__ == '__main__':
    main()
