"""
exp02_vpmacd.py — Volume-Price-Adjusted MACD on Taiwan stocks
Paper: 2604.26063, Apr 30, 2026 (q-fin.TR / q-fin.ST)

Original claim:
  Vanilla MACD has signal lag and false signals. Adding (volume, volatility,
  intraday structure) + a sensitivity parameter outperforms vanilla MACD on
  S&P 500 / Nasdaq-100 / DJIA out-of-sample 2023-Feb 2026.

Our test:
  - Universe: TW top-100 by trade value (2025 calendar year)
  - In-sample: 2020-01 ~ 2022-12 for sensitivity tuning
  - Out-of-sample: 2023-01 ~ 2026-04
  - Compare: (a) vanilla MACD long-only, (b) VP-MACD long-only, (c) buy-and-hold
"""
import sqlite3
import json
import numpy as np
import pandas as pd
from pathlib import Path

DB = r"D:\claude\tw-stock-scanner\db\scanner.db"
OUT = Path(__file__).parent
OUT.mkdir(exist_ok=True)

con = sqlite3.connect(DB)

# 1. Pick top-100 by avg trade value during 2025
top = pd.read_sql_query("""
    SELECT stock_id, AVG(trade_value) v
    FROM daily_prices
    WHERE date BETWEEN '2025-01-01' AND '2025-12-31'
    GROUP BY stock_id
    HAVING COUNT(*) > 200
    ORDER BY v DESC LIMIT 100
""", con)
sids = tuple(top.stock_id.tolist())

q = f"""
SELECT stock_id, date, open_price o, high_price h, low_price l, close_price c, volume v
FROM daily_prices
WHERE stock_id IN {sids} AND date >= '2018-01-01'
ORDER BY stock_id, date
"""
px = pd.read_sql_query(q, con, parse_dates=['date'])
con.close()
print(f"Loaded {len(px):,} rows for {px.stock_id.nunique()} stocks")


def macd_signal(close, fast=12, slow=26, sig=9):
    ema_f = close.ewm(span=fast, adjust=False).mean()
    ema_s = close.ewm(span=slow, adjust=False).mean()
    macd = ema_f - ema_s
    signal = macd.ewm(span=sig, adjust=False).mean()
    hist = macd - signal
    return hist


def vp_macd_signal(g, fast=12, slow=26, sig=9, sens=1.0):
    """Volume-Price-Adjusted MACD with true entry threshold semantics.

    Adjustments:
      - Replace plain close EMA with VWAP-like EMA: EMA(close * volume) / EMA(volume)
      - Multiply final histogram by intraday-range factor: 1 + (h-l)/c (volatility)
      - Returns (hist, threshold). Caller compares hist > threshold for entry.
        threshold = sens * rolling_std(hist, 60). sens < 1 = earlier entry.
    """
    c = g.c
    pv = (g.c * g.v).ewm(span=fast, adjust=False).mean() / g.v.ewm(span=fast, adjust=False).mean()
    pv_s = (g.c * g.v).ewm(span=slow, adjust=False).mean() / g.v.ewm(span=slow, adjust=False).mean()
    macd = pv - pv_s
    signal = macd.ewm(span=sig, adjust=False).mean()
    hist = (macd - signal) * (1 + (g.h - g.l) / c.clip(lower=1e-6))
    threshold = sens * hist.rolling(60, min_periods=10).std().fillna(0)
    return hist, threshold


def backtest_long_only(g, hist, name, threshold=None):
    """Long when hist > threshold, flat otherwise. Daily rebalance.

    threshold: scalar 0 (default vanilla) or pd.Series (vp threshold)
    """
    if threshold is None:
        pos = (hist > 0).astype(int).shift(1).fillna(0)
    else:
        pos = (hist > threshold).astype(int).shift(1).fillna(0)
    ret = g.c.pct_change().fillna(0)
    pnl = pos * ret
    # transaction cost: 30 bps round-trip
    turnover = pos.diff().abs().fillna(0)
    cost = turnover * 0.003
    pnl_net = pnl - cost
    cum = (1 + pnl_net).cumprod()
    n = len(pnl_net)
    if pnl_net.std() == 0 or n < 30:
        return None
    sharpe = pnl_net.mean() / pnl_net.std() * np.sqrt(252)
    cagr = cum.iloc[-1] ** (252 / n) - 1
    mdd = (cum / cum.cummax() - 1).min()
    trades = int(turnover.sum() / 2)
    return dict(name=name, sharpe=sharpe, cagr=cagr, mdd=mdd, trades=trades,
                final=cum.iloc[-1], n=n, returns=pnl_net)


def split_periods(df):
    is_ = df[(df.date >= '2020-01-01') & (df.date <= '2022-12-31')]
    oos = df[(df.date >= '2023-01-01') & (df.date <= '2026-04-30')]
    return is_, oos


# 2. In-sample tune sensitivity on top-20 (avoid IS overfit on full 100)
top20 = top.head(20).stock_id.tolist()
sens_grid = [0.0, 0.3, 0.7, 1.0, 1.5]  # 0 = no threshold (purest VP test)
is_results = {}
for sens in sens_grid:
    sharpes = []
    for sid in top20:
        g = px[px.stock_id == sid].set_index('date').sort_index()
        is_, _ = split_periods(g.reset_index())
        if len(is_) < 200:
            continue
        is_g = g.loc[is_.date.values]
        hist, thr = vp_macd_signal(is_g, sens=sens)
        r = backtest_long_only(is_g, hist, 'vp', threshold=thr)
        if r:
            sharpes.append(r['sharpe'])
    is_results[sens] = np.mean(sharpes) if sharpes else np.nan
print("\nIn-sample sensitivity tuning (mean Sharpe across top-20):")
for s, sh in is_results.items():
    print(f"  sens={s}: Sharpe={sh:.3f}")
best_sens = max(is_results, key=is_results.get)
print(f"  >> Best sens = {best_sens}")

# 3. OOS test on top-100 with best_sens
oos_rows = []
ret_panel_van = []
ret_panel_vp = []
ret_panel_bh = []
for sid in top.stock_id.tolist():
    g = px[px.stock_id == sid].set_index('date').sort_index()
    _, oos = split_periods(g.reset_index())
    if len(oos) < 200:
        continue
    oos_g = g.loc[oos.date.values]
    hv = macd_signal(oos_g.c)
    hp, thr = vp_macd_signal(oos_g, sens=best_sens)
    rv = backtest_long_only(oos_g, hv, 'vanilla')
    rp = backtest_long_only(oos_g, hp, 'vp', threshold=thr)
    if rv is None or rp is None:
        continue
    # buy-and-hold for OOS
    bh_ret = oos_g.c.pct_change().fillna(0)
    bh_cum = (1 + bh_ret).cumprod()
    bh_sharpe = bh_ret.mean() / bh_ret.std() * np.sqrt(252) if bh_ret.std() > 0 else np.nan
    bh_cagr = bh_cum.iloc[-1] ** (252 / len(bh_ret)) - 1
    oos_rows.append(dict(
        stock_id=sid,
        van_sharpe=rv['sharpe'], van_cagr=rv['cagr'], van_mdd=rv['mdd'], van_trades=rv['trades'],
        vp_sharpe=rp['sharpe'], vp_cagr=rp['cagr'], vp_mdd=rp['mdd'], vp_trades=rp['trades'],
        bh_sharpe=bh_sharpe, bh_cagr=bh_cagr,
    ))
    ret_panel_van.append(rv['returns'].rename(sid))
    ret_panel_vp.append(rp['returns'].rename(sid))
    ret_panel_bh.append(bh_ret.rename(sid))

oos_df = pd.DataFrame(oos_rows)
print(f"\nOOS results on {len(oos_df)} stocks:")
print(oos_df.describe()[['van_sharpe', 'vp_sharpe', 'bh_sharpe',
                         'van_cagr', 'vp_cagr', 'bh_cagr',
                         'van_mdd', 'vp_mdd']].round(3))

oos_df.to_csv(OUT / 'exp02_oos_per_stock.csv', index=False)

# 4. Aggregate equal-weight portfolio
def panel_cum(panels):
    df = pd.concat(panels, axis=1).fillna(0)
    avg = df.mean(axis=1)
    return avg, (1 + avg).cumprod()

avg_van, cum_van = panel_cum(ret_panel_van)
avg_vp, cum_vp = panel_cum(ret_panel_vp)
avg_bh, cum_bh = panel_cum(ret_panel_bh)

def stats(r):
    return dict(
        sharpe=r.mean() / r.std() * np.sqrt(252) if r.std() > 0 else np.nan,
        cagr=(1 + r).prod() ** (252 / len(r)) - 1,
        mdd=((1 + r).cumprod() / (1 + r).cumprod().cummax() - 1).min(),
    )

agg = {
    'vanilla_MACD_eq_weight': stats(avg_van),
    'VP_MACD_eq_weight': stats(avg_vp),
    'buy_hold_eq_weight': stats(avg_bh),
}
print("\nAggregate equal-weight portfolio (OOS 2023-01 ~ 2026-04):")
for k, v in agg.items():
    print(f"  {k}: Sharpe={v['sharpe']:.3f}  CAGR={v['cagr']:.3%}  MDD={v['mdd']:.3%}")

# Win rate
wins_van = (oos_df.van_sharpe > oos_df.bh_sharpe).mean()
wins_vp = (oos_df.vp_sharpe > oos_df.bh_sharpe).mean()
print(f"\n% of stocks where MACD beats buy-hold:  vanilla={wins_van:.1%}  VP={wins_vp:.1%}")

# 5. Save summary
summary = dict(
    paper_id='2604.26063',
    paper_claim='VP-MACD outperforms vanilla MACD on US indices',
    in_sample='2020-01 ~ 2022-12, top-20 TW stocks',
    oos='2023-01 ~ 2026-04, top-100 TW stocks',
    best_sensitivity=best_sens,
    in_sample_tuning={str(k): float(v) for k, v in is_results.items()},
    oos_aggregate=agg,
    win_rate_vs_bh={'vanilla': float(wins_van), 'vp': float(wins_vp)},
    n_stocks_oos=len(oos_df),
    median_sharpe={
        'vanilla': float(oos_df.van_sharpe.median()),
        'vp': float(oos_df.vp_sharpe.median()),
        'buy_hold': float(oos_df.bh_sharpe.median()),
    },
)
with open(OUT / 'exp02_summary.json', 'w', encoding='utf-8') as f:
    json.dump(summary, f, ensure_ascii=False, indent=2, default=float)
print("\nSaved exp02_summary.json + exp02_oos_per_stock.csv")
