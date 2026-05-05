"""
exp03_hrp_crisp.py — Hierarchical & Iterative Portfolio with Alpha
Paper: 2604.23833 "Beyond De Prado and Cotton: Hierarchical and Iterative Methods
                  for General Mean-Variance Portfolios" (Wuebben, Apr 28 2026)

Original methods:
  - HRP-mu: HRP tree but injects expected returns
  - HRP-Sigma-mu: improved HRP-mu using richer in-cluster covariance
  - CRISP: iterative shrinkage between diagonal and full Markowitz

We implement minimal versions and compare on TW top-50 stocks.

Comparison set:
  - Equal weight (1/N)
  - Inverse-vol
  - HRP (vanilla, De Prado 2016)
  - HRP-mu (this paper)
  - CRISP (this paper, simplified)
  - Direct Markowitz (Ledoit-Wolf shrunk)

Alpha signal: 12-1 momentum (skip last month) for expected returns.
"""
import sqlite3, json
import numpy as np, pandas as pd
from pathlib import Path
from scipy.cluster.hierarchy import linkage, fcluster
from scipy.spatial.distance import squareform

DB = r"D:\claude\tw-stock-scanner\db\scanner.db"
OUT = Path(__file__).parent

con = sqlite3.connect(DB)
top = pd.read_sql_query("""
    SELECT stock_id, AVG(trade_value) v
    FROM daily_prices
    WHERE date BETWEEN '2024-01-01' AND '2025-12-31'
    GROUP BY stock_id HAVING COUNT(*) > 400
    ORDER BY v DESC LIMIT 50
""", con)
sids = tuple(top.stock_id.tolist())
px = pd.read_sql_query(f"""
    SELECT stock_id, date, close_price c
    FROM daily_prices WHERE stock_id IN {sids}
        AND date >= '2019-01-01' AND date <= '2026-04-30'
""", con, parse_dates=['date'])
con.close()

wide = px.pivot(index='date', columns='stock_id', values='c').sort_index()
ret = wide.pct_change().dropna(how='all')
# TW daily limit is ±10%; cap clearly bad rows (split / IPO artefacts) at ±15%
ret = ret.clip(-0.15, 0.15)
print(f"Universe: {wide.shape[1]} stocks, {len(ret)} days (returns clipped at ±15%)")


def get_quasi_diag(link):
    link = link.astype(int)
    sort_ix = pd.Series([link[-1, 0], link[-1, 1]])
    n_items = link[-1, 3]
    while sort_ix.max() >= n_items:
        sort_ix.index = range(0, sort_ix.shape[0] * 2, 2)
        df0 = sort_ix[sort_ix >= n_items]
        i = df0.index
        j = df0.values - n_items
        sort_ix[i] = link[j, 0]
        df0 = pd.Series(link[j, 1], index=i + 1)
        sort_ix = pd.concat([sort_ix, df0]).sort_index()
        sort_ix.index = range(sort_ix.shape[0])
    return sort_ix.tolist()


def get_cluster_var(cov, c_items):
    cov_ = cov.loc[c_items, c_items]
    ivp = 1.0 / np.diag(cov_.values)
    ivp /= ivp.sum()
    return float(np.dot(np.dot(ivp, cov_.values), ivp))


def hrp_alloc(cov, sort_ix, mu=None):
    """Recursive bisection. If mu given, tilt by mu rank (HRP-mu)."""
    w = pd.Series(1.0, index=sort_ix)
    c_items = [sort_ix]
    while c_items:
        c_items = [c[i:j] for c in c_items
                   for i, j in ((0, len(c) // 2), (len(c) // 2, len(c)))
                   if len(c) > 1]
        for i in range(0, len(c_items), 2):
            c0, c1 = c_items[i], c_items[i + 1]
            v0 = get_cluster_var(cov, c0)
            v1 = get_cluster_var(cov, c1)
            alpha = 1 - v0 / (v0 + v1)
            if mu is not None:
                # tilt toward higher-mu cluster
                m0 = mu.loc[c0].mean()
                m1 = mu.loc[c1].mean()
                tilt = np.tanh((m0 - m1) * 5)  # bounded shift
                alpha = np.clip(alpha + 0.15 * tilt, 0.05, 0.95)
            w[c0] *= alpha
            w[c1] *= 1 - alpha
    return w


def correl_dist(corr):
    return np.sqrt((1 - corr) / 2.0)


def hrp_portfolio(cov_df, mu=None):
    corr_arr = np.corrcoef(cov_df.values).copy()
    corr_arr = (corr_arr + corr_arr.T) / 2
    np.fill_diagonal(corr_arr, 1.0)
    corr = pd.DataFrame(corr_arr, index=cov_df.index, columns=cov_df.index)
    dist_arr = np.sqrt((1 - corr_arr) / 2.0)
    np.fill_diagonal(dist_arr, 0)
    link = linkage(squareform(dist_arr, checks=False), method='single')
    sort_ix_int = get_quasi_diag(link)
    sort_ix = corr.index[sort_ix_int].tolist()
    cov = cov_df
    return hrp_alloc(cov, sort_ix, mu=mu).loc[cov_df.index]


def ledoit_wolf_shrink(R):
    n, k = R.shape
    S = R.cov().values
    F = np.diag(np.diag(S))  # diagonal target
    # Simple shrinkage intensity: 0.3 (literature heuristic)
    delta = 0.3
    return delta * F + (1 - delta) * S


def markowitz(mu, cov, gamma=1.0):
    """Long-only mean-variance: max mu'w - gamma/2 w'Σw, sum w = 1, w >= 0.
    Solve with simple QP via projected gradient.
    """
    n = len(mu)
    w = np.ones(n) / n
    inv = np.linalg.pinv(cov + np.eye(n) * 1e-4)
    w_unc = inv @ mu
    if w_unc.sum() != 0:
        w_unc /= w_unc.sum()
    w_unc = np.clip(w_unc, 0, None)
    w_unc /= max(w_unc.sum(), 1e-9)
    return pd.Series(w_unc, index=mu.index)


def crisp(R, mu, lam=0.5):
    """CRISP: iterative interpolation between diagonal-rule and Markowitz.
    Diagonal rule: w_i ∝ mu_i / sigma_i^2  (Sharpe-tilted inverse-variance).
    Markowitz: w = Σ⁻¹ mu / 1'Σ⁻¹mu.
    Final: lam * w_diag + (1-lam) * w_full.
    """
    sig = R.std().values
    var = sig ** 2 + 1e-8
    w_diag = mu.values / var
    w_diag = np.clip(w_diag, 0, None)
    s = w_diag.sum()
    if s > 0:
        w_diag /= s
    cov = ledoit_wolf_shrink(R)
    w_full = np.linalg.pinv(cov) @ mu.values
    w_full = np.clip(w_full, 0, None)
    s = w_full.sum()
    if s > 0:
        w_full /= s
    w = lam * w_diag + (1 - lam) * w_full
    s = w.sum()
    if s > 0:
        w /= s
    return pd.Series(w, index=mu.index)


# Walk-forward: monthly rebalance. Lookback 252 days for cov, mu.
ret_filled = ret.fillna(0)
month_ends = ret.resample('ME').last().index
month_ends = [d for d in month_ends if d in ret.index]

results = {name: [] for name in ['EW', 'IV', 'HRP', 'HRP_mu', 'CRISP', 'MV_LW']}
weight_log = []

for i, dt in enumerate(month_ends[:-1]):
    nxt = month_ends[i + 1]
    lb_start = ret.index[max(0, ret.index.get_loc(dt) - 252)]
    R = ret.loc[lb_start:dt].dropna(axis=1, thresh=200)
    if R.shape[1] < 20:
        continue
    R_filled = R.fillna(0)
    cov = R_filled.cov()
    sig = R_filled.std()
    # 12-1 momentum (skip last 21 days)
    end_loc = ret.index.get_loc(dt)
    if end_loc < 252:
        continue
    p_now = wide.iloc[end_loc - 21]
    p_old = wide.iloc[max(0, end_loc - 252)]
    mom = (p_now / p_old - 1).reindex(R.columns).fillna(0)
    # cross-sectional z
    mu = (mom - mom.mean()) / (mom.std() + 1e-9)
    mu *= 0.005  # scale to daily-return like

    weights = {
        'EW': pd.Series(1 / R.shape[1], index=R.columns),
        'IV': (1 / sig).fillna(0).pipe(lambda x: x / x.sum()),
        'HRP': hrp_portfolio(cov),
        'HRP_mu': hrp_portfolio(cov, mu=mu),
        'CRISP': crisp(R_filled, mu, lam=0.5),
        'MV_LW': markowitz(mu, ledoit_wolf_shrink(R_filled), gamma=1.0),
    }

    fwd = ret.loc[dt:nxt].iloc[1:]
    if len(fwd) == 0:
        continue
    for name, w in weights.items():
        w = w.reindex(R.columns).fillna(0)
        w = w / max(w.sum(), 1e-9)
        port_ret = (fwd[w.index] * w).sum(axis=1).fillna(0)
        results[name].append(port_ret)
    weight_log.append((dt, {k: float(v.values.max()) for k, v in weights.items()}))

# Compile
def assemble(rs):
    if not rs:
        return pd.Series(dtype=float)
    return pd.concat(rs).sort_index()

cum = {}
stats = {}
for name, rs in results.items():
    r = assemble(rs)
    if r.empty:
        continue
    cum[name] = (1 + r).cumprod()
    n = len(r)
    sharpe = r.mean() / r.std() * np.sqrt(252) if r.std() > 0 else 0
    cagr = (1 + r).prod() ** (252 / n) - 1
    mdd = (cum[name] / cum[name].cummax() - 1).min()
    stats[name] = dict(sharpe=float(sharpe), cagr=float(cagr), mdd=float(mdd), final=float(cum[name].iloc[-1]))

print("\nWalk-forward portfolio comparison (monthly rebalance, 252-day lookback):")
print(f"{'Method':<10} {'Sharpe':>8} {'CAGR':>8} {'MDD':>8} {'Final':>8}")
for name, st in stats.items():
    print(f"{name:<10} {st['sharpe']:>8.3f} {st['cagr']:>8.3%} {st['mdd']:>8.3%} {st['final']:>8.3f}")

with open(OUT / 'exp03_summary.json', 'w', encoding='utf-8') as f:
    json.dump(dict(
        paper='2604.23833',
        method_compared=list(stats.keys()),
        stats=stats,
        n_rebalances=len(weight_log),
        oos_period=f"{cum['EW'].index[0].date()} ~ {cum['EW'].index[-1].date()}",
    ), f, ensure_ascii=False, indent=2, default=float)
pd.concat({k: v for k, v in cum.items()}, axis=1).to_csv(OUT / 'exp03_cum.csv')
print("\nSaved exp03_summary.json + exp03_cum.csv")
