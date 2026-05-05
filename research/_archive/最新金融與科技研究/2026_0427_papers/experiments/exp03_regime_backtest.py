"""
論文 #3 結構化策略回測評估 — 台股實證
Paper: arXiv:2604.18821 - Evaluating Structured Strategy Backtests

實作要點：
- 用 macro_indicators + 大盤波動 建立 4 制度標籤（bull/bear/choppy/panic）
- 跑簡易動量策略：60 日 momentum top 20 等權，週度 rebalance
- 按制度拆解績效，看是否符合論文「制度依賴 alpha」的假設
- 對照 ETF 0050 baseline（peer benchmark）
"""

import sqlite3
import json
import numpy as np
import pandas as pd
from pathlib import Path

DB = r"D:\claude\tw-stock-scanner\db\scanner.db"
OUT_DIR = Path(r"D:\claude\tw-stock-scanner\research\_archive\最新金融與科技研究\2026_0427_papers\experiments")


def load_data():
    conn = sqlite3.connect(DB)
    # 取流動性最好的 200 檔
    rank_sql = """
        SELECT stock_id, AVG(trade_value) AS avg_tv FROM daily_prices
        WHERE date >= '2025-04-01' AND trade_value > 0
        GROUP BY stock_id HAVING COUNT(*) >= 200
        ORDER BY avg_tv DESC LIMIT 200
    """
    top = pd.read_sql(rank_sql, conn)
    ids = tuple(top['stock_id'].tolist())
    px = pd.read_sql(
        f"SELECT stock_id, date, close_price FROM daily_prices WHERE stock_id IN ({','.join('?'*len(ids))})",
        conn, params=ids
    )
    # 0050 ETF（peer benchmark）— 不在 stocks 中可能就抓不到，改用大盤平均
    px['date'] = pd.to_datetime(px['date'])
    panel = px.pivot(index='date', columns='stock_id', values='close_price').sort_index()
    rets = np.log(panel / panel.shift(1)).dropna(how='all')
    rets = rets.dropna(axis=1, thresh=int(len(rets) * 0.95)).fillna(0.0)
    conn.close()
    return rets


def build_regime(rets):
    """
    4 制度：bull / bear / choppy / panic
    用大盤代理 = 全 panel 等權收益
    指標：
      - 趨勢：60 日累積收益（正/負）
      - 波動：20 日 std（高/低）
      - panic：5 日內出現 -3% 以上單日跌幅
    """
    market = rets.median(axis=1)
    trend60 = market.rolling(60).sum()
    vol20 = market.rolling(20).std() * np.sqrt(252)
    rolling_min5 = market.rolling(5).min()

    regime = pd.Series(index=market.index, dtype=object)
    vol_threshold = vol20.median()  # 用樣本中位數定義「高低波動」
    for i in range(len(market)):
        t = trend60.iloc[i]
        v = vol20.iloc[i]
        m = rolling_min5.iloc[i]
        if pd.isna(t) or pd.isna(v):
            regime.iloc[i] = 'unknown'
        elif m < -0.02:
            regime.iloc[i] = 'panic'
        elif t > 0 and v < vol_threshold:
            regime.iloc[i] = 'bull'
        elif t < 0 and v >= vol_threshold:
            regime.iloc[i] = 'bear'
        else:
            regime.iloc[i] = 'choppy'
    return regime, market


def momentum_strategy(rets, lookback=60, top_pct=0.10, rebalance_days=5):
    """
    每 5 日 rebalance：取過去 60 日累積收益前 10% 等權持有
    """
    mom = rets.rolling(lookback).sum().shift(1)
    n = len(rets)
    weights = pd.DataFrame(0.0, index=rets.index, columns=rets.columns)
    last_w = pd.Series(0.0, index=rets.columns)
    for i in range(lookback, n):
        if (i - lookback) % rebalance_days == 0:
            today_mom = mom.iloc[i].dropna()
            if len(today_mom) > 0:
                threshold = today_mom.quantile(1 - top_pct)
                selected = today_mom[today_mom > threshold].index
                last_w = pd.Series(0.0, index=rets.columns)
                if len(selected) > 0:
                    last_w[selected] = 1.0 / len(selected)
        weights.iloc[i] = last_w
    port_ret = (weights.shift(1) * rets).sum(axis=1)
    return port_ret


def stats(returns):
    if len(returns) == 0 or returns.std() == 0:
        return {'sharpe': 0, 'mean_ann': 0, 'vol_ann': 0, 'mdd': 0, 'win_rate': 0}
    cum = (1 + returns).cumprod()
    mdd = ((cum / cum.cummax()) - 1).min()
    return {
        'sharpe': float(returns.mean() / returns.std() * np.sqrt(252)),
        'mean_ann': float(returns.mean() * 252),
        'vol_ann': float(returns.std() * np.sqrt(252)),
        'mdd': float(mdd),
        'win_rate': float((returns > 0).mean()),
        'n_days': int(len(returns)),
    }


def main():
    print("=" * 60)
    print("實驗 #3：結構化回測評估 — 台股實證")
    print("=" * 60)

    rets = load_data()
    print(f"panel: {rets.shape}, dates: {rets.index[0].date()} ~ {rets.index[-1].date()}")

    regime, market = build_regime(rets)
    regime_counts = regime.value_counts()
    print(f"\n制度分布：")
    for r, n in regime_counts.items():
        print(f"  {r}: {n} 日 ({n/len(regime)*100:.1f}%)")

    # 跑策略
    print(f"\n跑動量策略（60d lookback, top 10%, 5d rebalance）...")
    strat_ret = momentum_strategy(rets, lookback=60, top_pct=0.10, rebalance_days=5)
    market_ret = market

    # 對照 baseline：peer benchmark = 等權市場
    eq_ret = rets.mean(axis=1)

    # 整體績效
    print("\n=== 整體績效 ===")
    overall = pd.DataFrame({
        'momentum': stats(strat_ret.dropna()),
        'equal_weight (peer)': stats(eq_ret.dropna()),
        'market (median)': stats(market_ret.dropna()),
    }).T
    print(overall.round(4))

    # 按制度拆解
    print("\n=== 按制度拆解（動量策略）===")
    per_regime = []
    for r in ['bull', 'bear', 'choppy', 'panic']:
        idx = regime[regime == r].index
        in_regime = strat_ret.loc[strat_ret.index.intersection(idx)].dropna()
        if len(in_regime) > 0:
            s = stats(in_regime)
            s['regime'] = r
            per_regime.append(s)
    per_regime_df = pd.DataFrame(per_regime).set_index('regime')
    cols = ['n_days', 'sharpe', 'mean_ann', 'vol_ann', 'mdd', 'win_rate']
    print(per_regime_df[cols].round(4))

    # 對照 peer 在不同制度下
    print("\n=== 按制度拆解（peer baseline）===")
    per_regime_peer = []
    for r in ['bull', 'bear', 'choppy', 'panic']:
        idx = regime[regime == r].index
        in_regime = eq_ret.loc[eq_ret.index.intersection(idx)].dropna()
        if len(in_regime) > 0:
            s = stats(in_regime)
            s['regime'] = r
            per_regime_peer.append(s)
    per_regime_peer_df = pd.DataFrame(per_regime_peer).set_index('regime')
    print(per_regime_peer_df[cols].round(4))

    # 動量 vs peer 的 alpha（每制度）
    print("\n=== 動量策略 vs peer 的超額（α）===")
    alpha_table = []
    for r in ['bull', 'bear', 'choppy', 'panic']:
        if r in per_regime_df.index and r in per_regime_peer_df.index:
            alpha_sharpe = per_regime_df.loc[r, 'sharpe'] - per_regime_peer_df.loc[r, 'sharpe']
            alpha_ret = per_regime_df.loc[r, 'mean_ann'] - per_regime_peer_df.loc[r, 'mean_ann']
            alpha_table.append({
                'regime': r,
                'momentum_sharpe': per_regime_df.loc[r, 'sharpe'],
                'peer_sharpe': per_regime_peer_df.loc[r, 'sharpe'],
                'alpha_sharpe': alpha_sharpe,
                'alpha_return_ann': alpha_ret,
            })
    alpha_df = pd.DataFrame(alpha_table).set_index('regime')
    print(alpha_df.round(4))

    # 論文預測檢驗：動量在 bull > bear / panic
    print("\n=== 論文預測 vs 實測 ===")
    print("論文預測：動量策略在 bull regime 表現最好，panic 期間應顯著較差")
    if 'bull' in per_regime_df.index and 'panic' in per_regime_df.index:
        bull_sh = per_regime_df.loc['bull', 'sharpe']
        panic_sh = per_regime_df.loc['panic', 'sharpe']
        if bull_sh > panic_sh:
            print(f"  [V] 成立：bull sharpe ({bull_sh:.2f}) > panic sharpe ({panic_sh:.2f})")
            verdict = "PAPER_CONFIRMED"
        else:
            print(f"  [X] 不成立：bull ({bull_sh:.2f}) <= panic ({panic_sh:.2f})")
            verdict = "PAPER_REJECTED"
    else:
        verdict = "INSUFFICIENT_DATA"

    # 儲存
    overall.to_csv(OUT_DIR / "exp03_overall.csv")
    per_regime_df.to_csv(OUT_DIR / "exp03_by_regime_momentum.csv")
    per_regime_peer_df.to_csv(OUT_DIR / "exp03_by_regime_peer.csv")
    alpha_df.to_csv(OUT_DIR / "exp03_alpha_by_regime.csv")
    pd.DataFrame({'date': regime.index, 'regime': regime.values}).to_csv(
        OUT_DIR / "exp03_regime_labels.csv", index=False
    )

    summary = {
        'experiment': '#3 structured_backtest_eval',
        'n_stocks': int(rets.shape[1]),
        'date_range': [str(rets.index[0].date()), str(rets.index[-1].date())],
        'regime_distribution': {r: int(n) for r, n in regime_counts.items()},
        'overall': overall.to_dict('index'),
        'momentum_by_regime': per_regime_df[cols].to_dict('index'),
        'alpha_by_regime': alpha_df.to_dict('index'),
        'verdict': verdict,
    }
    with open(OUT_DIR / "exp03_summary.json", 'w', encoding='utf-8') as f:
        json.dump(summary, f, indent=2, ensure_ascii=False, default=str)
    print(f"\n結果已存至 {OUT_DIR}")


if __name__ == "__main__":
    main()
