"""
exp08_gamma_laplace.py — Generalized Laplace fit + VaR coverage test
Paper: 2605.00196 "Modeling Stock Returns and Volatility Using Bivariate Gamma
        Generalized Laplace Law" (Kozubowski, Sarantsev, Spiker, May 4 2026)

Original claim:
  - Joint (R, V) follows BGGL: R marginal Generalized Laplace, V Gamma
  - OOS log-likelihood improvement 3-8% over Normal-Lognormal baseline
  - VaR 95% empirical coverage close to 5% (vs 7-9% for Normal)

Our simplified test:
  - Take 0050 / 2330 / 0050 daily returns 2010-2024 in-sample, 2025-04 ~ 2026-04 OOS
  - Fit two return distributions:
      (a) Normal: N(mu, sigma)
      (b) Generalized Laplace (a.k.a. Variance Gamma symmetric): scale family
        Approximated here as a 3-parameter Asymmetric Laplace (loc, scale, asym)
  - For OOS daily, predict next-day VaR 95% from fitted distribution
  - Empirical exceedance rate = #(realized < VaR) / N → ideal 5%
"""
import sqlite3, json
import numpy as np, pandas as pd
from pathlib import Path
from scipy import stats, optimize

DB = r"D:\claude\tw-stock-scanner\db\scanner.db"
OUT = Path(__file__).parent

con = sqlite3.connect(DB)


def load(stock_id):
    df = pd.read_sql_query(
        """SELECT date, close_price c FROM daily_prices
           WHERE stock_id = ? AND date BETWEEN '2010-01-01' AND '2026-04-30'
           ORDER BY date""",
        con, params=(stock_id,), parse_dates=['date'])
    df['r'] = df.c.pct_change().clip(-0.15, 0.15)
    return df.dropna()


def fit_normal(r):
    return dict(mu=float(r.mean()), sigma=float(r.std()))


def fit_laplace(r):
    """Asymmetric Laplace via MLE. PDF f(x; m, lam, kappa)."""
    # Use scipy's laplace_asymmetric for stable estimation
    params = stats.laplace_asymmetric.fit(r.values)
    return dict(kappa=float(params[0]), loc=float(params[1]), scale=float(params[2]))


def var_normal(p, mu, sigma):
    return mu + sigma * stats.norm.ppf(p)


def var_laplace(p, kappa, loc, scale):
    return stats.laplace_asymmetric.ppf(p, kappa, loc=loc, scale=scale)


def evaluate(r, p=0.05):
    """Return dict with normal-fit and laplace-fit OOS VaR coverage."""
    is_ = r[r.date <= '2024-12-31'].r.values
    oos = r[r.date >= '2025-04-01'].r.values
    if len(is_) < 1000 or len(oos) < 50:
        return None
    n_par = fit_normal(pd.Series(is_))
    l_par = fit_laplace(pd.Series(is_))
    var_n = var_normal(p, **n_par)
    var_l = var_laplace(p, **l_par)
    cov_n = (oos < var_n).mean()
    cov_l = (oos < var_l).mean()
    # log-likelihood OOS
    ll_n = stats.norm.logpdf(oos, n_par['mu'], n_par['sigma']).sum()
    ll_l = stats.laplace_asymmetric.logpdf(oos, l_par['kappa'], l_par['loc'], l_par['scale']).sum()
    return dict(
        n_is=len(is_), n_oos=len(oos),
        normal=dict(**n_par, var=float(var_n), oos_coverage=float(cov_n), oos_loglik=float(ll_n)),
        laplace=dict(**l_par, var=float(var_l), oos_coverage=float(cov_l), oos_loglik=float(ll_l)),
        ll_improvement_pct=float((ll_l - ll_n) / abs(ll_n) * 100) if ll_n != 0 else np.nan,
    )


stocks = ['0050', '2330', '2317', '2454']
out = {}
print(f"{'Stock':<6} {'IS_n':>5} {'OOS_n':>6} {'N.coverage':>10} {'L.coverage':>10} {'N.VaR':>8} {'L.VaR':>8} {'Δ_LL':>8}")
for sid in stocks:
    r = load(sid)
    res = evaluate(r)
    if res is None:
        continue
    out[sid] = res
    print(f"{sid:<6} {res['n_is']:>5} {res['n_oos']:>6}"
          f" {res['normal']['oos_coverage']:>9.2%} {res['laplace']['oos_coverage']:>9.2%}"
          f" {res['normal']['var']:>+8.3%} {res['laplace']['var']:>+8.3%}"
          f" {res['ll_improvement_pct']:>+7.2f}%")

con.close()

# Aggregate verdict
norm_cov = np.mean([v['normal']['oos_coverage'] for v in out.values()])
lap_cov = np.mean([v['laplace']['oos_coverage'] for v in out.values()])
ll_imp = np.mean([v['ll_improvement_pct'] for v in out.values()])
target = 0.05
verdict = abs(lap_cov - target) < abs(norm_cov - target)
print(f"\n=== Aggregate (4 stocks) ===")
print(f"Normal VaR 5% coverage: {norm_cov:.2%}  (target 5%)")
print(f"Laplace VaR 5% coverage: {lap_cov:.2%}  (target 5%)")
print(f"OOS LL improvement (Laplace vs Normal): {ll_imp:+.2f}%")
print(f"Verdict: {'Laplace better calibrated' if verdict else 'Normal still wins'}")

with open(OUT / 'exp08_summary.json', 'w', encoding='utf-8') as f:
    json.dump(dict(
        paper='2605.00196',
        per_stock=out,
        agg_normal_coverage=float(norm_cov),
        agg_laplace_coverage=float(lap_cov),
        agg_ll_improvement_pct=float(ll_imp),
        verdict='laplace_better' if verdict else 'normal_competitive',
    ), f, ensure_ascii=False, indent=2, default=float)
print("\nSaved exp08_summary.json")
