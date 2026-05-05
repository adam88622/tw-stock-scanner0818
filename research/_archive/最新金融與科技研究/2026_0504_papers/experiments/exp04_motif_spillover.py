"""
exp04_motif_spillover.py — Motif-based risk spillover (simplified)
Paper: 2604.25406 "A Motif-Based Framework for Decomposing Risk Spillovers"

Original method:
  - Build quantile-connectedness networks across asset universe (commodity/equity futures)
  - Extract directed triadic motifs (3-asset patterns)
  - Use orbit positions to identify net risk transmitters

Our simplified test:
  - Universe: TW top-30 by trade value (sectoral mix)
  - Window: 60-day rolling, weekly recompute
  - Spillover proxy: lead-lag (rolling Granger-like) max |corr(r_i,t, r_j,t-k)| for k=1..5
  - Triadic motif: count chains i→j→k vs feedback loops i→j→i
  - Hypothesis: stocks with high "out-degree centrality" (more downstream) are net transmitters
                and their crashes precede broader index drawdowns

Output:
  - per-week stocks ranked by transmitter score
  - validation: does top-quartile transmitter weighted shock predict TWII drawdown next 5d?
"""
import sqlite3, json
import numpy as np, pandas as pd
from pathlib import Path

DB = r"D:\claude\tw-stock-scanner\db\scanner.db"
OUT = Path(__file__).parent

con = sqlite3.connect(DB)
top = pd.read_sql_query("""
    SELECT stock_id FROM daily_prices
    WHERE date BETWEEN '2024-01-01' AND '2025-12-31'
    GROUP BY stock_id HAVING COUNT(*) > 400
    ORDER BY AVG(trade_value) DESC LIMIT 30
""", con)
sids = tuple(top.stock_id.tolist())
px = pd.read_sql_query(f"""
    SELECT stock_id, date, close_price c
    FROM daily_prices WHERE stock_id IN {sids}
        AND date BETWEEN '2021-01-01' AND '2026-04-30'
""", con, parse_dates=['date'])
# index proxy: equal-weight of top-30
con.close()

wide = px.pivot(index='date', columns='stock_id', values='c').sort_index()
ret = wide.pct_change().clip(-0.15, 0.15).dropna(how='all').fillna(0)
idx_ret = ret.mean(axis=1)
print(f"Universe: {ret.shape[1]} stocks, {len(ret)} days")


def lead_lag_max(R, max_k=5):
    """For each ordered pair (i,j), return max over k=1..max_k of |corr(r_j,t, r_i,t-k)|.
    Interpretation: how much past i predicts future j (i is upstream of j).
    """
    n = R.shape[1]
    out = np.zeros((n, n))
    cols = R.columns
    for k in range(1, max_k + 1):
        lagged = R.shift(k).dropna()
        cur = R.loc[lagged.index]
        # centered
        L = lagged - lagged.mean()
        C = cur - cur.mean()
        Lstd = L.std() + 1e-9
        Cstd = C.std() + 1e-9
        # corr_kij = sum_t L[t,i] * C[t,j] / (n * Lstd_i * Cstd_j)
        cov = (L.values.T @ C.values) / (len(L) - 1)
        corr_k = cov / np.outer(Lstd.values, Cstd.values)
        out = np.maximum(out, np.abs(corr_k))
    np.fill_diagonal(out, 0)
    return pd.DataFrame(out, index=cols, columns=cols)


def motif_transmitter_score(W):
    """Out-degree centrality after thresholding edges at the 75th percentile.
    Plus triadic chain count: sum over j,k of W[i,j]*W[j,k] * (1 - W[k,i]) (chains, not loops).
    """
    thr = np.quantile(W.values, 0.75)
    A = (W > thr).astype(float).values.copy()
    np.fill_diagonal(A, 0)
    out_deg = A.sum(axis=1)
    # chain count: for each i, sum over j,k of A[i,j]*A[j,k]*(1-A[k,i])
    chains = np.zeros(A.shape[0])
    for i in range(A.shape[0]):
        for j in range(A.shape[0]):
            if A[i, j] == 0 or i == j:
                continue
            for k in range(A.shape[0]):
                if k == i or k == j:
                    continue
                if A[j, k] > 0 and A[k, i] == 0:
                    chains[i] += 1
    score = 0.5 * (out_deg / max(out_deg.max(), 1)) + 0.5 * (chains / max(chains.max(), 1))
    return pd.Series(score, index=W.index)


# Walk-forward: every 5 days, compute scores from past 60 days
window = 60
step = 5
forward = 5
rows = []
dates = ret.index
i0 = window
while i0 + forward < len(dates):
    win = ret.iloc[i0 - window:i0]
    if win.shape[0] < window - 5:
        i0 += step
        continue
    W = lead_lag_max(win)
    score = motif_transmitter_score(W)
    # forward index drawdown
    fwd_idx = idx_ret.iloc[i0:i0 + forward]
    fwd_idx_min = fwd_idx.min()  # worst day in next 5
    fwd_idx_cum = (1 + fwd_idx).prod() - 1  # cumulative
    # transmitter-weighted shock (today's index ret)
    today_idx = idx_ret.iloc[i0 - 1]
    # top-quartile transmitters: avg recent return
    top_q = score.nlargest(int(len(score) * 0.25)).index
    top_q_ret = win[top_q].iloc[-5:].mean(axis=1).mean()
    # bottom-quartile
    bot_q = score.nsmallest(int(len(score) * 0.25)).index
    bot_q_ret = win[bot_q].iloc[-5:].mean(axis=1).mean()
    rows.append(dict(
        date=dates[i0],
        top_score_stocks=','.join(top_q[:5].astype(str)),
        top_q_recent_5d_ret=top_q_ret,
        bot_q_recent_5d_ret=bot_q_ret,
        fwd_idx_min=fwd_idx_min,
        fwd_idx_cum=fwd_idx_cum,
    ))
    i0 += step

df = pd.DataFrame(rows)
print(f"Generated {len(df)} weekly snapshots")

# Validation: when top-q transmitters dropped, did index drop more next 5d?
df['transmitter_drop_signal'] = df.top_q_recent_5d_ret < df.top_q_recent_5d_ret.quantile(0.20)
df['no_signal'] = df.top_q_recent_5d_ret > df.top_q_recent_5d_ret.quantile(0.80)

sig = df[df.transmitter_drop_signal]
no = df[df.no_signal]
print(f"\nSignal triggered: {len(sig)} times, no-signal control: {len(no)}")
print(f"  Avg fwd 5d index cum return when transmitters dropped:    {sig.fwd_idx_cum.mean():.4%}  (median {sig.fwd_idx_cum.median():.4%})")
print(f"  Avg fwd 5d index cum return when transmitters rose:       {no.fwd_idx_cum.mean():.4%}  (median {no.fwd_idx_cum.median():.4%})")
print(f"  P(fwd 5d worst day < -2%) | transmitter drop:             {(sig.fwd_idx_min < -0.02).mean():.2%}")
print(f"  P(fwd 5d worst day < -2%) | transmitter rise:             {(no.fwd_idx_min < -0.02).mean():.2%}")
print(f"  Base rate P(fwd 5d worst day < -2%):                      {(df.fwd_idx_min < -0.02).mean():.2%}")

# Naive baseline: just use today's index drop as signal
df['idx_drop_signal'] = df.fwd_idx_min.shift(forward) < -0.01  # lazy proxy
# Comparison test
print("\n--- Direct comparison ---")
print(f"Transmitter signal precision (worst day < -2%):  {(sig.fwd_idx_min < -0.02).mean():.2%}")
print(f"Transmitter signal lift over base rate:          {(sig.fwd_idx_min < -0.02).mean() - (df.fwd_idx_min < -0.02).mean():+.2%}")

df.to_csv(OUT / 'exp04_motif_signals.csv', index=False)
summary = dict(
    paper='2604.25406',
    n_signals=int(len(sig)),
    base_rate_2pct_drop=float((df.fwd_idx_min < -0.02).mean()),
    signal_precision_2pct_drop=float((sig.fwd_idx_min < -0.02).mean()),
    no_signal_precision_2pct_drop=float((no.fwd_idx_min < -0.02).mean()),
    avg_fwd_cum_ret_signal=float(sig.fwd_idx_cum.mean()),
    avg_fwd_cum_ret_nosig=float(no.fwd_idx_cum.mean()),
)
with open(OUT / 'exp04_summary.json', 'w', encoding='utf-8') as f:
    json.dump(summary, f, ensure_ascii=False, indent=2, default=float)
print("\nSaved exp04_summary.json + exp04_motif_signals.csv")
