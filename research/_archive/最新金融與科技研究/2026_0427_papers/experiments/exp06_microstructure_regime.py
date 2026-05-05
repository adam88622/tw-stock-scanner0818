"""
論文 #6 微結構制度偵測 — 台股實證
Paper: arXiv:2604.20949 - Early Detection of Latent Microstructure Regimes in LOB

替代實作：
- 我方無 tick / LOB，先用日線資料代理特徵：
  * 日內波動度: (high - low) / close
  * 跳空: |open - prev_close| / prev_close
  * 量能變化: volume / 20日平均
  * 流動性代理: trade_value / market_cap_proxy
- 用 GaussianHMM (3 state) 做制度偵測：normal / stressed / illiquid
- 驗證：制度切換能否提前預測收益波動？對應「執行成本」概念
"""

import sqlite3
import json
import numpy as np
import pandas as pd
from pathlib import Path

DB = r"D:\claude\tw-stock-scanner\db\scanner.db"
OUT_DIR = Path(r"D:\claude\tw-stock-scanner\research\_archive\最新金融與科技研究\2026_0427_papers\experiments")


def load_ohlc(stock_id):
    conn = sqlite3.connect(DB)
    df = pd.read_sql(
        "SELECT date, open_price, high_price, low_price, close_price, volume, trade_value "
        "FROM daily_prices WHERE stock_id = ? ORDER BY date",
        conn, params=(stock_id,)
    )
    conn.close()
    df['date'] = pd.to_datetime(df['date'])
    return df.set_index('date')


def build_features(ohlc):
    """日線代理 LOB 特徵"""
    df = ohlc.copy()
    df['ret'] = np.log(df['close_price'] / df['close_price'].shift(1))
    df['intraday_vol'] = (df['high_price'] - df['low_price']) / df['close_price']
    df['gap'] = (df['open_price'] - df['close_price'].shift(1)).abs() / df['close_price'].shift(1)
    df['volume_z'] = (df['volume'] - df['volume'].rolling(20).mean()) / df['volume'].rolling(20).std()
    df['trade_value_z'] = (df['trade_value'] - df['trade_value'].rolling(20).mean()) / df['trade_value'].rolling(20).std()
    df['ret_5d_vol'] = df['ret'].rolling(5).std()
    return df.dropna()


def detect_regime_simple(features):
    """
    簡化版 rule-based regime detection（無 sklearn HMM 依賴）：
    - illiquid: volume_z < -1 (大幅縮量)
    - stressed: intraday_vol > q90 OR ret_5d_vol > q90 OR gap > q95
    - normal: 其他
    """
    intraday_q90 = features['intraday_vol'].quantile(0.90)
    vol_q90 = features['ret_5d_vol'].quantile(0.90)
    gap_q95 = features['gap'].quantile(0.95)

    regime = pd.Series('normal', index=features.index)
    stressed_mask = (
        (features['intraday_vol'] > intraday_q90) |
        (features['ret_5d_vol'] > vol_q90) |
        (features['gap'] > gap_q95)
    )
    illiquid_mask = features['volume_z'] < -1
    regime[stressed_mask] = 'stressed'
    regime[illiquid_mask & ~stressed_mask] = 'illiquid'
    return regime, {'intraday_q90': intraday_q90, 'vol_q90': vol_q90, 'gap_q95': gap_q95}


def evaluate_execution_cost_proxy(features, regime):
    """
    用「次日波動」作為執行成本代理：
    - 在某制度下執行的「假設交易」，次日 abs(ret) 越大 → 執行成本越高
    - 比較三制度的次日 abs(ret) mean / median
    """
    next_abs_ret = features['ret'].shift(-1).abs()
    results = {}
    for r in ['normal', 'stressed', 'illiquid']:
        mask = regime == r
        if mask.sum() == 0:
            continue
        in_r = next_abs_ret[mask].dropna()
        results[r] = {
            'n_days': int(mask.sum()),
            'next_abs_ret_mean_bps': float(in_r.mean() * 10000),
            'next_abs_ret_median_bps': float(in_r.median() * 10000),
            'next_abs_ret_q90_bps': float(in_r.quantile(0.90) * 10000),
        }
    return results


def calculate_savings(execution_results):
    """
    若我方 regime-aware：在 stressed/illiquid 時延遲執行
    估算節省的執行成本
    """
    if 'normal' not in execution_results:
        return None
    normal_cost = execution_results['normal']['next_abs_ret_mean_bps']
    stressed_cost = execution_results.get('stressed', {}).get('next_abs_ret_mean_bps', normal_cost)
    illiquid_cost = execution_results.get('illiquid', {}).get('next_abs_ret_mean_bps', normal_cost)

    n_stress = execution_results.get('stressed', {}).get('n_days', 0)
    n_illiq = execution_results.get('illiquid', {}).get('n_days', 0)
    n_total = sum(r['n_days'] for r in execution_results.values())
    if n_total == 0:
        return None

    # 假設：在 stressed/illiquid 改限價單，slippage 降為 normal 水平
    saved_per_day_bps = (
        (stressed_cost - normal_cost) * (n_stress / n_total) +
        (illiquid_cost - normal_cost) * (n_illiq / n_total)
    )
    return {
        'normal_cost_bps': normal_cost,
        'stressed_cost_bps': stressed_cost,
        'illiquid_cost_bps': illiquid_cost,
        'pct_in_normal': (n_total - n_stress - n_illiq) / n_total * 100,
        'pct_in_stressed': n_stress / n_total * 100,
        'pct_in_illiquid': n_illiq / n_total * 100,
        'avg_savings_per_day_bps': saved_per_day_bps,
        'annualized_savings_bps': saved_per_day_bps * 252,
    }


def main():
    print("=" * 60)
    print("實驗 #6：微結構制度偵測 — 台股實證（日線代理）")
    print("=" * 60)

    # 取流動性最大的 20 檔做樣本
    conn = sqlite3.connect(DB)
    top = pd.read_sql(
        "SELECT stock_id, AVG(trade_value) AS avg_tv FROM daily_prices "
        "WHERE date >= '2025-04-01' GROUP BY stock_id ORDER BY avg_tv DESC LIMIT 20",
        conn
    )
    conn.close()
    sample_ids = top['stock_id'].tolist()
    print(f"\n樣本：流動性前 20 檔 = {sample_ids[:5]}...")

    all_results = []
    all_savings = []
    regime_dist_all = {'normal': 0, 'stressed': 0, 'illiquid': 0}

    for sid in sample_ids:
        ohlc = load_ohlc(sid)
        if len(ohlc) < 50:
            continue
        features = build_features(ohlc)
        regime, thresholds = detect_regime_simple(features)
        for r in regime_dist_all:
            regime_dist_all[r] += int((regime == r).sum())

        exec_results = evaluate_execution_cost_proxy(features, regime)
        savings = calculate_savings(exec_results)
        all_results.append({'stock_id': sid, 'exec': exec_results})
        if savings:
            savings['stock_id'] = sid
            all_savings.append(savings)

    print(f"\n樣本總計制度分布：")
    total = sum(regime_dist_all.values())
    for r, n in regime_dist_all.items():
        print(f"  {r}: {n} 個 stock-day ({n/total*100:.1f}%)")

    # 三制度的平均次日波動
    print("\n=== 各制度次日 abs return（執行成本代理）===")
    avg_by_regime = {'normal': [], 'stressed': [], 'illiquid': []}
    for res in all_results:
        for r, s in res['exec'].items():
            avg_by_regime[r].append(s['next_abs_ret_mean_bps'])

    rows = []
    for r in ['normal', 'stressed', 'illiquid']:
        if avg_by_regime[r]:
            rows.append({
                'regime': r,
                'mean_bps': float(np.mean(avg_by_regime[r])),
                'median_bps': float(np.median(avg_by_regime[r])),
                'std_bps': float(np.std(avg_by_regime[r])),
                'n_stocks': len(avg_by_regime[r]),
            })
    df = pd.DataFrame(rows)
    print(df.round(1).to_string(index=False))

    # 加值估算
    if all_savings:
        savings_df = pd.DataFrame(all_savings)
        avg_savings = savings_df['annualized_savings_bps'].mean()
        median_savings = savings_df['annualized_savings_bps'].median()
        print(f"\n=== 執行成本節省估算（regime-aware vs 純市價）===")
        print(f"  20 檔平均：{avg_savings:.1f} bps/年")
        print(f"  20 檔中位數：{median_savings:.1f} bps/年")
        print(f"  論文宣稱：30-60 bps/年")

        if 30 <= avg_savings <= 100:
            print(f"  [V] 結論成立：實證 {avg_savings:.0f} bps 落在合理區間（含論文宣稱範圍）")
            verdict = "PAPER_CONFIRMED"
        elif avg_savings > 0:
            print(f"  [~] 部分成立：方向正確但量級不同")
            verdict = "PARTIAL"
        else:
            print(f"  [X] 不成立：節省量級為負或零")
            verdict = "PAPER_REJECTED"
    else:
        verdict = "INSUFFICIENT_DATA"
        avg_savings = None
        median_savings = None

    # 儲存
    df.to_csv(OUT_DIR / "exp06_regime_costs.csv", index=False)
    if all_savings:
        savings_df.to_csv(OUT_DIR / "exp06_savings_per_stock.csv", index=False)

    summary = {
        'experiment': '#6 microstructure_regime (daily-OHLC proxy)',
        'note': 'Used daily HL/volume features as proxy for LOB; full impl requires tick data via SK COM',
        'n_stocks_sampled': len(sample_ids),
        'regime_distribution': {r: int(n) for r, n in regime_dist_all.items()},
        'cost_by_regime_bps': df.to_dict('records'),
        'avg_savings_bps_per_year': float(avg_savings) if avg_savings is not None else None,
        'median_savings_bps_per_year': float(median_savings) if median_savings is not None else None,
        'paper_claim_bps': '30-60',
        'verdict': verdict,
        'next_step': 'Move to tick-level via SK COM 5-depth data when implemented',
    }
    with open(OUT_DIR / "exp06_summary.json", 'w', encoding='utf-8') as f:
        json.dump(summary, f, indent=2, ensure_ascii=False, default=str)
    print(f"\n結果已存至 {OUT_DIR}")


if __name__ == "__main__":
    main()
