"""
exp06_levered_etf.py — Levered ETF anomaly explained on 0050 synthetic 2x/-1x
Paper: 2604.27287 Bianchi & Goldberg (UC Berkeley) "A Levered ETF Anomaly Explained"

Original claim:
  - 2x and 3x SPY ETFs lost money 2022-01 ~ 2023-12 even though SPY went up
  - ~2/3 of gap explained by volatility drag: drag ≈ 0.5 * k * (k-1) * sigma^2
  - ~1/3 from leverage-deviation covariance

Our test on TW 0050 (since 2003-01-01):
  - Synthesize daily-reset 2x and -1x return series
  - Compare actual cumulative return vs naive k * cum return of underlying
  - Verify drag formula
  - Roll 252-day window: empirical drag vs theoretical 0.5*k*(k-1)*sigma^2
"""
import sqlite3, json
import numpy as np, pandas as pd
from pathlib import Path

DB = r"D:\claude\tw-stock-scanner\db\scanner.db"
OUT = Path(__file__).parent

con = sqlite3.connect(DB)
df = pd.read_sql_query(
    """SELECT date, close_price c FROM daily_prices
       WHERE stock_id = '0050' ORDER BY date""",
    con, parse_dates=['date'])
con.close()
df = df.sort_values('date').reset_index(drop=True)
df['r'] = df.c.pct_change()
# clip extreme (likely dividend-adj artifacts)
df['r'] = df.r.clip(-0.15, 0.15)
df = df.dropna()
print(f"0050 sample: {df.date.min().date()} ~ {df.date.max().date()}, {len(df)} days")
print(f"  annual sigma = {df.r.std()*np.sqrt(252):.3%}")
print(f"  cumulative return = {(1+df.r).prod()-1:.3%}")


def levered_cum(r, k):
    """Daily-reset levered return: each day return = k * underlying."""
    rk = (k * r).clip(-0.99, None)  # avoid wipeout
    return (1 + rk).cumprod()


underlying_cum = (1 + df.r).cumprod()
final_under = underlying_cum.iloc[-1]

results = {}
for k in [-2, -1, 2, 3]:
    cum = levered_cum(df.r, k)
    final_actual = cum.iloc[-1]
    final_naive = final_under ** k if final_under > 0 else np.nan
    sigma_d = df.r.std()
    n = len(df)
    # theoretical drag formula (per period): 0.5 * k * (k-1) * sigma^2 daily
    theory_drag_daily = 0.5 * k * (k - 1) * sigma_d ** 2
    theory_drag_total = theory_drag_daily * n
    log_actual = np.log(final_actual)
    log_naive = np.log(final_naive) if final_naive > 0 else np.nan
    empirical_drag = log_naive - log_actual
    results[k] = dict(
        actual_cum=float(final_actual),
        naive_cum=float(final_naive),
        empirical_drag=float(empirical_drag),
        theoretical_drag=float(theory_drag_total),
        annual_drag=float(empirical_drag * 252 / n),
        annual_theory=float(theory_drag_daily * 252),
    )

print("\n=== 0050 leveraged ETF anomaly: actual vs naive vs theory ===")
print(f"{'k':>4} {'actual':>10} {'naive_cum':>12} {'empir.drag':>11} {'theory.drag':>11} {'ann.drag':>9}")
for k, r in results.items():
    print(f"{k:>4} {r['actual_cum']:>10.3f} {r['naive_cum']:>12.3f} "
          f"{r['empirical_drag']:>11.3f} {r['theoretical_drag']:>11.3f} {r['annual_drag']:>+9.3%}")

# Rolling 252-day: empirical annual drag vs theory
df['under_log'] = np.log1p(df.r)
df['k2_log'] = np.log1p((2 * df.r).clip(-0.99, None))
df['kn_log'] = np.log1p((-1 * df.r).clip(-0.99, None))
df['sigma_252'] = df.r.rolling(252).std()
df['ann_emp_drag_2x'] = (2 * df.under_log.rolling(252).sum() - df.k2_log.rolling(252).sum()) * (252 / 252)
df['ann_emp_drag_inv'] = (-1 * df.under_log.rolling(252).sum() - df.kn_log.rolling(252).sum()) * (252 / 252)
df['ann_theory_drag_2x'] = 0.5 * 2 * 1 * df.sigma_252 ** 2 * 252
df['ann_theory_drag_inv'] = 0.5 * (-1) * (-2) * df.sigma_252 ** 2 * 252  # = sigma^2 * 252

valid = df.dropna(subset=['ann_emp_drag_2x', 'ann_theory_drag_2x'])
corr_2x = valid.ann_emp_drag_2x.corr(valid.ann_theory_drag_2x)
err_2x = (valid.ann_emp_drag_2x - valid.ann_theory_drag_2x).abs().mean()
err_inv = (valid.ann_emp_drag_inv - valid.ann_theory_drag_inv).abs().mean()

print(f"\nRolling 252-day check ({len(valid)} windows):")
print(f"  2x: corr(empirical, theory) = {corr_2x:.3f}, mean |error| = {err_2x:.4%}")
print(f"  -1x: mean |error| = {err_inv:.4%}")

# Save outputs
with open(OUT / 'exp06_summary.json', 'w', encoding='utf-8') as f:
    json.dump(dict(
        paper='2604.27287',
        sample=f"{df.date.min().date()} ~ {df.date.max().date()}",
        n_days=int(len(df)),
        annual_sigma=float(df.r.std() * np.sqrt(252)),
        full_period=results,
        rolling_corr_2x=float(corr_2x),
        rolling_err_2x_pct=float(err_2x),
        rolling_err_inv_pct=float(err_inv),
    ), f, ensure_ascii=False, indent=2, default=float)

valid[['date', 'sigma_252',
       'ann_emp_drag_2x', 'ann_theory_drag_2x',
       'ann_emp_drag_inv', 'ann_theory_drag_inv']].to_csv(
    OUT / 'exp06_rolling.csv', index=False)
print("\nSaved exp06_summary.json + exp06_rolling.csv")
