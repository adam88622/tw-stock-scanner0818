"""
exp07_kelly_sigmoidal.py — Multivariate Kelly with sigmoidal scaling
Paper reference: 2604.24723 "Efficient Multivariate Kelly Optimization
                  Reveals Sigmoidal Scaling Laws" (Tepelyan & Lam, Apr 28 2026)

Original claim:
  - For N assets, regularized Kelly position f* = (Σ + λI)^-1 μ
  - Best λ has analytic form
  - Sum |f_i*| grows sigmoidally with N (saturates around N≈30 for US market)

Our test on TW top-200:
  - 5 lookback periods (2020-2024 each year and full)
  - For each N in {5, 10, 20, 30, 50, 75, 100, 150, 200}: random sample 30 times
  - Compute regularized Kelly with λ = trace(Σ)/N (Ledoit-Wolf-style scalar)
  - Track sum |f|, max |f|, n_active (|f| > 0.01)
  - Plot scaling
"""
import sqlite3, json
import numpy as np, pandas as pd
from pathlib import Path

DB = r"D:\claude\tw-stock-scanner\db\scanner.db"
OUT = Path(__file__).parent
RNG = np.random.default_rng(42)

con = sqlite3.connect(DB)
top = pd.read_sql_query("""
    SELECT stock_id FROM daily_prices
    WHERE date BETWEEN '2024-01-01' AND '2025-12-31'
    GROUP BY stock_id HAVING COUNT(*) > 400
    ORDER BY AVG(trade_value) DESC LIMIT 200
""", con)
sids = tuple(top.stock_id.tolist())
px = pd.read_sql_query(f"""
    SELECT stock_id, date, close_price c FROM daily_prices
    WHERE stock_id IN {sids}
        AND date BETWEEN '2020-01-01' AND '2024-12-31'
""", con, parse_dates=['date'])
con.close()

wide = px.pivot(index='date', columns='stock_id', values='c').sort_index()
ret = wide.pct_change().clip(-0.15, 0.15).dropna(how='all').fillna(0)
print(f"Universe: {ret.shape[1]} stocks, {len(ret)} days")


def kelly_reg(R, lam_method='trace'):
    """Regularized Kelly: f* = (Σ + λI)^-1 μ. NOT normalized — raw leverage weights.
    R: daily return DataFrame (T x N).  Returns (f, lam_used).
    """
    mu = R.mean().values * 252
    Sigma = R.cov().values * 252
    n = Sigma.shape[0]
    if lam_method == 'trace':
        lam = np.trace(Sigma) / n
    elif lam_method == 'log':
        lam = np.trace(Sigma) * np.log(n) / n
    else:
        lam = 0.01
    A = Sigma + lam * np.eye(n)
    f = np.linalg.solve(A, mu)
    return f, lam


def sigmoid(x, L, k, x0):
    return L / (1 + np.exp(-k * (x - x0)))


N_grid = [5, 10, 20, 30, 50, 75, 100, 150, 200]
n_trials = 30
records = []

for N in N_grid:
    if N > ret.shape[1]:
        continue
    for trial in range(n_trials):
        cols = RNG.choice(ret.columns, size=N, replace=False)
        R = ret[cols]
        # exclude rows with all zero (very low coverage)
        f, lam = kelly_reg(R)
        records.append(dict(
            N=N,
            trial=trial,
            sum_abs_f=float(np.abs(f).sum()),
            max_abs_f=float(np.abs(f).max()),
            n_active=int((np.abs(f) > 0.01).sum()),
            mean_f=float(f.mean()),
            lam=float(lam),
        ))

df = pd.DataFrame(records)
agg = df.groupby('N').agg(
    sum_f_mean=('sum_abs_f', 'mean'),
    sum_f_std=('sum_abs_f', 'std'),
    max_f_mean=('max_abs_f', 'mean'),
    n_active_mean=('n_active', 'mean'),
).reset_index()
print("\nKelly scaling on TW top-200 universe:")
print(agg.round(3).to_string(index=False))

# Fit sigmoid
from scipy.optimize import curve_fit
try:
    popt, _ = curve_fit(sigmoid, agg.N, agg.sum_f_mean,
                        p0=[agg.sum_f_mean.max(), 0.05, 30],
                        maxfev=5000)
    L, k, x0 = popt
    print(f"\nSigmoid fit: L={L:.2f}  k={k:.4f}  x0={x0:.1f}")
    # half-saturation at x0
    sat_pred = L
    print(f"  Saturation level (max sum|f|): {sat_pred:.2f}")
    print(f"  Half-saturation N: {x0:.1f}")
    # Compare to linear baseline
    linear_pred = agg.sum_f_mean.iloc[0] * agg.N / agg.N.iloc[0]
    actual = agg.sum_f_mean
    sigmoid_pred = sigmoid(agg.N, *popt)
    rmse_lin = ((actual - linear_pred) ** 2).mean() ** 0.5
    rmse_sig = ((actual - sigmoid_pred) ** 2).mean() ** 0.5
    print(f"  RMSE linear extrapolation: {rmse_lin:.3f}")
    print(f"  RMSE sigmoid fit:          {rmse_sig:.3f}")
    is_sigmoidal = rmse_sig < rmse_lin
    print(f"  Verdict: {'SIGMOIDAL (paper confirmed)' if is_sigmoidal else 'NOT clearly sigmoidal'}")
except Exception as e:
    print(f"Sigmoid fit failed: {e}")
    L, k, x0 = (np.nan,) * 3

# Save
df.to_csv(OUT / 'exp07_kelly_raw.csv', index=False)
agg.to_csv(OUT / 'exp07_kelly_agg.csv', index=False)
with open(OUT / 'exp07_summary.json', 'w', encoding='utf-8') as f:
    json.dump(dict(
        paper='2604.24723',
        N_grid=N_grid,
        n_trials=n_trials,
        sigmoid_L=float(L),
        sigmoid_k=float(k),
        sigmoid_x0=float(x0),
        rmse_linear=float(rmse_lin),
        rmse_sigmoid=float(rmse_sig),
        is_sigmoidal=bool(is_sigmoidal),
        sum_abs_f_at_N={int(r.N): float(r.sum_f_mean) for r in agg.itertuples()},
    ), f, ensure_ascii=False, indent=2, default=float)
print("\nSaved exp07_kelly_*.csv + exp07_summary.json")
