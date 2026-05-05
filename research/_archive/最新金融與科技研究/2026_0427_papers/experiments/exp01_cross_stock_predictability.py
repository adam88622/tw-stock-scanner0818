"""
論文 #1 跨股票可預測性 — 台股實證
Paper: arXiv:2604.19476 - Cross-Stock Predictability via LLM-Augmented Semantic Networks

替代實作：
- 我方無中文新聞 embedding，先用「日線收益相關性」作為「語意距離」代理
- 對每檔股票 i：取過去 60 日相關性最高的 top-K 鄰居 j
- 訊號：neighbor_lag_return = mean(rets[j, t-1])
- 驗證：IC(neighbor_lag_return, ret[i, t])
- 與單純 momentum、reversal 因子做對照
"""

import sqlite3
import json
import numpy as np
import pandas as pd
from pathlib import Path
from scipy.stats import spearmanr

DB = r"D:\claude\tw-stock-scanner\db\scanner.db"
OUT_DIR = Path(r"D:\claude\tw-stock-scanner\research\_archive\最新金融與科技研究\2026_0427_papers\experiments")


def load_returns(top_n=200):
    conn = sqlite3.connect(DB)
    rank_sql = """
        SELECT stock_id, AVG(trade_value) AS avg_tv FROM daily_prices
        WHERE date >= '2025-04-01' AND trade_value > 0
        GROUP BY stock_id HAVING COUNT(*) >= 200
        ORDER BY avg_tv DESC LIMIT ?
    """
    top = pd.read_sql(rank_sql, conn, params=(top_n,))
    ids = tuple(top['stock_id'].tolist())
    px = pd.read_sql(
        f"SELECT stock_id, date, close_price FROM daily_prices WHERE stock_id IN ({','.join('?'*len(ids))})",
        conn, params=ids
    )
    conn.close()
    px['date'] = pd.to_datetime(px['date'])
    panel = px.pivot(index='date', columns='stock_id', values='close_price').sort_index()
    rets = np.log(panel / panel.shift(1)).dropna(how='all')
    rets = rets.dropna(axis=1, thresh=int(len(rets) * 0.95)).fillna(0.0)
    return rets


def neighbor_lag_signal(rets, lookback=60, top_k=10):
    """
    對每檔股票 i, 每日 t：
    1. 用 [t-lookback, t-1] 算相關矩陣
    2. 取與 i 相關最高的 top_k 鄰居（排除自己）
    3. signal[i, t] = mean(rets[j, t-1] for j in neighbors)
    """
    n = len(rets)
    cols = rets.columns
    signal = pd.DataFrame(np.nan, index=rets.index, columns=cols)
    for t in range(lookback, n):
        win = rets.iloc[t - lookback:t]
        corr_arr = win.corr().to_numpy().copy()
        # 對角設為 -inf 避免選自己
        np.fill_diagonal(corr_arr, -np.inf)
        # 對每檔取 top-K 鄰居
        topk_idx = np.argpartition(-corr_arr, top_k, axis=1)[:, :top_k]
        # neighbor 上一日（即 t-1）的收益
        prev_ret = rets.iloc[t - 1].values  # (N,)
        # 對每檔 i，平均 neighbor j 的 prev_ret
        for i_idx in range(len(cols)):
            neighbors = topk_idx[i_idx]
            signal.iloc[t, i_idx] = np.nanmean(prev_ret[neighbors])
    return signal


def momentum_signal(rets, lookback=20):
    """簡單動量：過去 20 日累積收益"""
    return rets.rolling(lookback).sum().shift(1)


def reversal_signal(rets):
    """簡單反轉：昨日收益（負號）"""
    return -rets.shift(1)


def calc_ic(signal, future_ret):
    """每日橫斷面 Spearman IC，回傳 mean IC + IR"""
    daily_ic = []
    for t in signal.index:
        if t in future_ret.index:
            s = signal.loc[t].dropna()
            r = future_ret.loc[t].dropna()
            common = s.index.intersection(r.index)
            if len(common) >= 10:
                ic, _ = spearmanr(s.loc[common], r.loc[common])
                if not np.isnan(ic):
                    daily_ic.append({'date': t, 'ic': ic})
    df = pd.DataFrame(daily_ic).set_index('date') if daily_ic else pd.DataFrame()
    if df.empty:
        return {'mean_ic': 0, 'ir': 0, 'ic_t_stat': 0, 'n_days': 0}
    return {
        'mean_ic': float(df['ic'].mean()),
        'ic_std': float(df['ic'].std()),
        'ir': float(df['ic'].mean() / df['ic'].std()) if df['ic'].std() > 0 else 0,
        'ic_t_stat': float(df['ic'].mean() / df['ic'].std() * np.sqrt(len(df))) if df['ic'].std() > 0 else 0,
        'n_days': int(len(df)),
    }


def long_short_portfolio(signal, future_ret, top_pct=0.10):
    """top_pct 多頭 - bottom_pct 空頭，每日 rebalance"""
    daily_ret = []
    for t in signal.index:
        if t in future_ret.index:
            s = signal.loc[t].dropna()
            r = future_ret.loc[t].dropna()
            common = s.index.intersection(r.index)
            if len(common) >= 20:
                s_c = s.loc[common]
                r_c = r.loc[common]
                top_thr = s_c.quantile(1 - top_pct)
                bot_thr = s_c.quantile(top_pct)
                long_ret = r_c[s_c > top_thr].mean()
                short_ret = r_c[s_c < bot_thr].mean()
                daily_ret.append({'date': t, 'ls_ret': long_ret - short_ret})
    df = pd.DataFrame(daily_ret).set_index('date') if daily_ret else pd.DataFrame()
    if df.empty:
        return {'sharpe_ls': 0, 'mean_ann': 0, 'win_rate': 0, 'n_days': 0}
    return {
        'sharpe_ls': float(df['ls_ret'].mean() / df['ls_ret'].std() * np.sqrt(252)) if df['ls_ret'].std() > 0 else 0,
        'mean_ann': float(df['ls_ret'].mean() * 252),
        'win_rate': float((df['ls_ret'] > 0).mean()),
        'n_days': int(len(df)),
    }


def main():
    print("=" * 60)
    print("實驗 #1：跨股票可預測性 — 台股實證（相關性網路代理）")
    print("=" * 60)

    rets = load_returns(top_n=200)
    print(f"panel: {rets.shape}, dates: {rets.index[0].date()} ~ {rets.index[-1].date()}")

    # future return = 下一日收益
    future_1d = rets.shift(-1)
    future_5d = rets.shift(-1).rolling(5).sum().shift(-4)  # 未來 5 日累積

    print("\n計算 3 個訊號…")
    print("  1) neighbor_lag_signal (top-10 相關鄰居昨日收益)…")
    sig_neighbor = neighbor_lag_signal(rets, lookback=60, top_k=10)
    print("  2) momentum_signal (20 日累積收益)…")
    sig_mom = momentum_signal(rets, lookback=20)
    print("  3) reversal_signal (昨日收益反向)…")
    sig_rev = reversal_signal(rets)

    print("\n=== IC（橫斷面 Spearman）對照 ===")
    print("\n--- 預測 T+1 收益 ---")
    rows_1d = []
    for name, sig in [('neighbor_lag', sig_neighbor), ('momentum_20d', sig_mom), ('reversal_1d', sig_rev)]:
        r = calc_ic(sig, future_1d)
        r['signal'] = name
        r['horizon'] = '1d'
        rows_1d.append(r)
    df_1d = pd.DataFrame(rows_1d)
    print(df_1d.to_string(index=False))

    print("\n--- 預測 T+1~T+5 累積收益 ---")
    rows_5d = []
    for name, sig in [('neighbor_lag', sig_neighbor), ('momentum_20d', sig_mom), ('reversal_1d', sig_rev)]:
        r = calc_ic(sig, future_5d)
        r['signal'] = name
        r['horizon'] = '5d'
        rows_5d.append(r)
    df_5d = pd.DataFrame(rows_5d)
    print(df_5d.to_string(index=False))

    # Long-Short 組合
    print("\n=== Long-Short 組合績效（top10% - bottom10%, 1d holding） ===")
    rows_ls = []
    for name, sig in [('neighbor_lag', sig_neighbor), ('momentum_20d', sig_mom), ('reversal_1d', sig_rev)]:
        r = long_short_portfolio(sig, future_1d, top_pct=0.10)
        r['signal'] = name
        rows_ls.append(r)
    df_ls = pd.DataFrame(rows_ls)
    print(df_ls.to_string(index=False))

    # 論文預測檢驗
    print("\n=== 論文預測 vs 實測 ===")
    print("論文預測：'語意鄰居' (相關鄰居) 的 lagged return 對下一日收益有可預測性")
    nl_ic = next(r for r in rows_1d if r['signal'] == 'neighbor_lag')
    if nl_ic['mean_ic'] > 0.01 and abs(nl_ic['ic_t_stat']) > 2:
        print(f"  [V] 成立：IC={nl_ic['mean_ic']:.4f}, t={nl_ic['ic_t_stat']:.2f} (>2)")
        verdict = "PAPER_CONFIRMED"
    elif abs(nl_ic['mean_ic']) > 0.01:
        sign = "正" if nl_ic['mean_ic'] > 0 else "負"
        print(f"  [~] {sign}向訊號但 t={nl_ic['ic_t_stat']:.2f} < 2，未達顯著")
        verdict = "WEAK_SIGNAL"
    else:
        print(f"  [X] 不成立：IC={nl_ic['mean_ic']:.4f} 接近零")
        verdict = "PAPER_REJECTED"

    # 對照各訊號相對強度
    print("\n=== 訊號相對強度排名 ===")
    df_all = pd.concat([df_1d, df_5d], ignore_index=True)
    df_all['abs_ic'] = df_all['mean_ic'].abs()
    df_all_sorted = df_all.sort_values('abs_ic', ascending=False)
    print(df_all_sorted[['signal', 'horizon', 'mean_ic', 'ic_t_stat', 'n_days']].to_string(index=False))

    # 儲存
    df_1d.to_csv(OUT_DIR / "exp01_ic_1d.csv", index=False)
    df_5d.to_csv(OUT_DIR / "exp01_ic_5d.csv", index=False)
    df_ls.to_csv(OUT_DIR / "exp01_long_short.csv", index=False)

    summary = {
        'experiment': '#1 cross_stock_predictability (correlation-network proxy)',
        'note': 'Used correlation network as proxy for LLM semantic network (no Chinese news text available yet)',
        'n_stocks': int(rets.shape[1]),
        'date_range': [str(rets.index[0].date()), str(rets.index[-1].date())],
        'ic_1d': df_1d.to_dict('records'),
        'ic_5d': df_5d.to_dict('records'),
        'long_short': df_ls.to_dict('records'),
        'verdict': verdict,
        'next_step': 'Replace correlation with text-embedding-3-large on news headlines for full implementation',
    }
    with open(OUT_DIR / "exp01_summary.json", 'w', encoding='utf-8') as f:
        json.dump(summary, f, indent=2, ensure_ascii=False, default=str)
    print(f"\n結果已存至 {OUT_DIR}")


if __name__ == "__main__":
    main()
