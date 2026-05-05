"""
07_empirical.py — NVIDIA Isaac GR00T 籃子實證驗證
================================================
Date  : 2026-04-28
Owner : GiS Quant Research

任務:
  1. yfinance 抓 13 檔 (TW7 + US3 + JP3) 2025-01-01 ~ 2026-04-28 日線。
  2. 個股指標: YTD return / 1Y return / 年化波動 / Sharpe(rf=2%)
                / 60D rolling β-to-NVDA (取最新) / 與 NVDA 的相關係數.
  3. 驗證原 MD 假設「β > 0.6 且 R² > 0.3」實際達標檔數.
  4. 計算 07 GR00T 籃 vs 02 humanoid 籃 portfolio-level 相關係數.
  5. GTC 2026 事件研究: 2026-03-17 ±10 交易日 CAR (相對 NVDA-β 預期).
  6. 結果輸出 CSV + JSON, 並生 results/07_empirical_results.md.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf

# 確保 console 能輸出中文 / β 等 unicode (Windows cp950 預設會 crash)
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

# -----------------------------------------------------------------------------
# 設定
# -----------------------------------------------------------------------------
START = "2025-01-01"
END = "2026-04-28"
RF_ANNUAL = 0.02
GTC_2026 = pd.Timestamp("2026-03-17")
WIN = 60  # rolling beta window

OUT_DIR = Path(__file__).parent
OUT_DIR.mkdir(parents=True, exist_ok=True)

# 07 籃 (GR00T NVIDIA Ecosystem) — 13 檔 + weight
BASKET_07 = {
    "2395.TW":  ("研華", 0.18),
    "5289.TWO": ("宜鼎", 0.10),     # TPEx OTC
    "2382.TW":  ("廣達", 0.15),
    "2359.TW":  ("所羅門", 0.08),
    "6245.TWO": ("立端", 0.05),     # TPEx OTC
    "8234.TWO": ("新漢", 0.04),     # TPEx OTC
    "2308.TW":  ("台達電", 0.06),
    "NVDA":     ("NVIDIA", 0.20),
    "SYM":      ("Symbotic", 0.04),
    "SERV":     ("Serve Robotics", 0.00),  # MD 列為美股錨點但未配權重 → 觀察用
    "6857.T":   ("Advantest", 0.04),
    "6981.T":   ("Murata", 0.03),
    "6594.T":   ("Nidec", 0.03),
}
# Note: SERV 在原 PoC 籃內權重未列, 但 MD §美股錨點 提及, 故納入觀察(權重 0).

# 02 籃 (humanoid robot — 從 PoC 引)
BASKET_02 = {
    "2308.TW":   0.10,
    "1597.TW":   0.08,
    "4533.TWO":  0.06,    # 全鋒 TPEx OTC
    "6230.TW":   0.05,
    "002472.SZ": 0.15,
    "300124.SZ": 0.15,
    "688041.SS": 0.10,    # 海光信息 — 上交所 .SS
    "NVDA":      0.10,
    "6594.T":    0.06,
    "6981.T":    0.05,
    # UBTECH (HK 9880) 略 — yfinance 代碼為 9880.HK, 為了保留 portfolio 完整性納入
    "9880.HK":   0.10,
}

ALL_TICKERS = sorted(set(BASKET_07) | set(BASKET_02))


# -----------------------------------------------------------------------------
# 1) 抓價
# -----------------------------------------------------------------------------
def download_prices(tickers: list[str], start: str, end: str) -> pd.DataFrame:
    print(f"[download] {len(tickers)} tickers, {start} ~ {end}")
    raw = yf.download(
        tickers,
        start=start,
        end=end,
        auto_adjust=True,
        progress=False,
        group_by="ticker",
        threads=True,
    )
    # 重新組成 wide df: columns = ticker, value = Close (auto_adjusted)
    closes = {}
    for t in tickers:
        try:
            if (t, "Close") in raw.columns:
                closes[t] = raw[(t, "Close")]
            elif "Close" in raw.columns:
                closes[t] = raw["Close"]
        except Exception as e:
            print(f"  ! {t} skipped: {e}")
    px = pd.DataFrame(closes).sort_index()
    px = px.ffill().dropna(how="all")
    return px


# -----------------------------------------------------------------------------
# 2) 指標
# -----------------------------------------------------------------------------
def compute_per_stock_stats(px: pd.DataFrame, bench: str = "NVDA") -> pd.DataFrame:
    rets = px.pct_change().dropna(how="all")
    bench_r = rets[bench]

    rows = []
    for t in px.columns:
        s = px[t].dropna()
        r = rets[t].dropna()
        if len(s) < 20:
            continue
        # YTD = 從 2026-01-02 起累積
        ytd_idx = s.index[s.index >= "2026-01-01"]
        if len(ytd_idx) > 0:
            ytd = s.loc[ytd_idx[-1]] / s.loc[ytd_idx[0]] - 1
        else:
            ytd = np.nan
        # 1Y return (約 252 交易日, 取最後一年)
        if len(s) >= 252:
            r1y = s.iloc[-1] / s.iloc[-252] - 1
        else:
            r1y = s.iloc[-1] / s.iloc[0] - 1
        # 年化波動
        vol = r.std() * np.sqrt(252)
        # Sharpe (excess over rf)
        ann_ret = (1 + r.mean()) ** 252 - 1
        sharpe = (ann_ret - RF_ANNUAL) / vol if vol > 0 else np.nan
        # 60D rolling beta vs NVDA (取最新)
        # align
        joint = pd.concat([r, bench_r], axis=1, keys=["s", "b"]).dropna()
        if len(joint) >= WIN and t != bench:
            tail = joint.tail(WIN)
            cov = np.cov(tail["s"], tail["b"])[0, 1]
            var_b = np.var(tail["b"], ddof=1)
            beta = cov / var_b if var_b > 0 else np.nan
            # R²
            corr_full = joint["s"].corr(joint["b"])
            r2_full = corr_full ** 2 if pd.notna(corr_full) else np.nan
            # rolling 60D R² (取最新)
            corr_tail = tail["s"].corr(tail["b"])
            r2_tail = corr_tail ** 2 if pd.notna(corr_tail) else np.nan
        elif t == bench:
            beta = 1.0
            r2_full = 1.0
            r2_tail = 1.0
            corr_full = 1.0
        else:
            beta = r2_full = r2_tail = corr_full = np.nan

        rows.append({
            "ticker": t,
            "ytd_return": ytd,
            "ret_1y": r1y,
            "ann_vol": vol,
            "sharpe": sharpe,
            "beta_60d_vs_NVDA": beta,
            "r2_full_vs_NVDA": r2_full,
            "r2_60d_vs_NVDA": r2_tail,
            "corr_full_vs_NVDA": corr_full,
            "n_obs": len(r),
        })
    df = pd.DataFrame(rows).set_index("ticker")
    return df


# -----------------------------------------------------------------------------
# 3) Portfolio 報酬序列
# -----------------------------------------------------------------------------
def portfolio_returns(rets: pd.DataFrame, weights: dict[str, float]) -> pd.Series:
    """以權重組 portfolio daily return; 缺值該日按可用權重再標準化."""
    w = pd.Series(weights, dtype=float)
    w = w / w.sum()  # 標準化, 確保總和=1
    cols = [c for c in w.index if c in rets.columns]
    sub = rets[cols].copy()
    w_sub = w[cols]
    # 對齊: 缺值日按該日有報價的成分股 re-normalize
    valid = ~sub.isna()
    daily_w = valid.mul(w_sub, axis=1)
    daily_w_sum = daily_w.sum(axis=1).replace(0, np.nan)
    daily_w = daily_w.div(daily_w_sum, axis=0)
    pr = (sub * daily_w).sum(axis=1)
    pr = pr.where(daily_w_sum.notna())
    return pr.dropna()


# -----------------------------------------------------------------------------
# 4) GTC 2026 事件研究
# -----------------------------------------------------------------------------
def gtc_event_study(rets: pd.DataFrame, basket_ret: pd.Series, bench: str = "NVDA",
                     event_dt: pd.Timestamp = GTC_2026, win: int = 10) -> dict:
    """以 NVDA 為 market proxy, 用事件前 estimation window [-120, -21] 估 α/β,
       事件窗 [-10, +10] 計算 abnormal & cumulative abnormal returns."""
    bench_r = rets[bench]
    # 估計窗
    est_end = event_dt - pd.Timedelta(days=30)
    est_start = est_end - pd.Timedelta(days=180)
    est = pd.concat([basket_ret, bench_r], axis=1, keys=["p", "m"]).dropna()
    est_win = est.loc[est_start:est_end]
    if len(est_win) < 30:
        return {"error": "insufficient estimation window", "n_est": len(est_win)}
    # OLS: p = α + β * m
    X = np.column_stack([np.ones(len(est_win)), est_win["m"].values])
    y = est_win["p"].values
    coef, *_ = np.linalg.lstsq(X, y, rcond=None)
    alpha, beta = coef[0], coef[1]
    # 事件窗
    all_dates = est.index
    # 找最接近 event_dt 的交易日
    nearest_idx = all_dates.searchsorted(event_dt)
    if nearest_idx >= len(all_dates):
        nearest_idx = len(all_dates) - 1
    ev_center = all_dates[nearest_idx]
    lo = max(0, nearest_idx - win)
    hi = min(len(all_dates) - 1, nearest_idx + win)
    ev_dates = all_dates[lo:hi + 1]
    ev = est.loc[ev_dates].copy()
    ev["expected"] = alpha + beta * ev["m"]
    ev["AR"] = ev["p"] - ev["expected"]
    ev["CAR"] = ev["AR"].cumsum()
    return {
        "event_date_actual": str(ev_center.date()),
        "alpha_est": float(alpha),
        "beta_est": float(beta),
        "n_est_obs": int(len(est_win)),
        "AR_event_day": float(ev.loc[ev_center, "AR"]) if ev_center in ev.index else None,
        "CAR_pm10": float(ev["CAR"].iloc[-1]),
        "CAR_path": ev["CAR"].to_dict(),
        "AR_path": ev["AR"].to_dict(),
    }


# -----------------------------------------------------------------------------
# 5) 主流程
# -----------------------------------------------------------------------------
def main():
    px = download_prices(ALL_TICKERS, START, END)
    print(f"[done] price matrix: {px.shape}")
    print(f"  date range: {px.index.min().date()} ~ {px.index.max().date()}")
    print(f"  cols: {list(px.columns)}")

    # 個股統計
    stats = compute_per_stock_stats(px, bench="NVDA")
    # 加上原權重
    stats["weight_07"] = pd.Series({k: v[1] for k, v in BASKET_07.items()})
    stats["weight_02"] = pd.Series(BASKET_02)
    stats["in_07"] = stats.index.isin(BASKET_07.keys())
    stats["in_02"] = stats.index.isin(BASKET_02.keys())
    stats = stats.sort_values("ret_1y", ascending=False)

    # 假設驗證: β>0.6 且 R²>0.3 (用 60D rolling)
    stats["pass_beta_filter"] = (stats["beta_60d_vs_NVDA"] > 0.6) & (stats["r2_60d_vs_NVDA"] > 0.3)

    # Portfolio
    rets = px.pct_change().dropna(how="all")
    pr_07 = portfolio_returns(rets, {k: v[1] for k, v in BASKET_07.items() if v[1] > 0})
    pr_02 = portfolio_returns(rets, BASKET_02)

    joint = pd.concat([pr_07, pr_02], axis=1, keys=["p07", "p02"]).dropna()
    portfolio_corr = joint["p07"].corr(joint["p02"])

    cum_07 = (1 + pr_07).cumprod()
    cum_02 = (1 + pr_02).cumprod()

    portfolio_summary = {
        "07_total_return": float(cum_07.iloc[-1] - 1),
        "02_total_return": float(cum_02.iloc[-1] - 1),
        "07_ann_vol": float(pr_07.std() * np.sqrt(252)),
        "02_ann_vol": float(pr_02.std() * np.sqrt(252)),
        "07_sharpe": float(((1 + pr_07.mean()) ** 252 - 1 - RF_ANNUAL) / (pr_07.std() * np.sqrt(252))),
        "02_sharpe": float(((1 + pr_02.mean()) ** 252 - 1 - RF_ANNUAL) / (pr_02.std() * np.sqrt(252))),
        "portfolio_return_correlation": float(portfolio_corr),
        "n_joint_days": int(len(joint)),
    }

    # GTC event study
    gtc_07 = gtc_event_study(rets, pr_07, bench="NVDA", event_dt=GTC_2026, win=10)
    gtc_02 = gtc_event_study(rets, pr_02, bench="NVDA", event_dt=GTC_2026, win=10)

    # ----- 輸出 -----
    csv_path = OUT_DIR / "07_empirical_stats.csv"
    stats.to_csv(csv_path, encoding="utf-8-sig")
    print(f"[write] {csv_path}")

    json_payload = {
        "meta": {
            "start": START, "end": END, "rf_annual": RF_ANNUAL,
            "n_tickers": len(stats),
            "as_of": str(px.index.max().date()),
        },
        "portfolio": portfolio_summary,
        "gtc_event_study_07": {k: v for k, v in gtc_07.items() if k not in ("CAR_path", "AR_path")},
        "gtc_event_study_02": {k: v for k, v in gtc_02.items() if k not in ("CAR_path", "AR_path")},
        "beta_filter_pass": stats[stats["pass_beta_filter"]].index.tolist(),
        "beta_filter_fail": stats[~stats["pass_beta_filter"]].index.tolist(),
    }
    json_path = OUT_DIR / "07_empirical_results.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(json_payload, f, ensure_ascii=False, indent=2, default=str)
    print(f"[write] {json_path}")

    # ----- console 摘要 -----
    print("\n=== 個股指標 (sort by 1Y return) ===")
    show = stats[["ytd_return", "ret_1y", "ann_vol", "sharpe",
                  "beta_60d_vs_NVDA", "r2_60d_vs_NVDA",
                  "pass_beta_filter", "in_07", "in_02"]].copy()
    print(show.round(3).to_string())

    print("\n=== Portfolio (07 vs 02) ===")
    for k, v in portfolio_summary.items():
        print(f"  {k:<35} {v}")

    print("\n=== GTC 2026 Event Study (±10D) ===")
    print(f"  07 籃: alpha={gtc_07.get('alpha_est'):.5f}, beta={gtc_07.get('beta_est'):.3f}, "
          f"CAR(±10)={gtc_07.get('CAR_pm10'):.4f}")
    print(f"  02 籃: alpha={gtc_02.get('alpha_est'):.5f}, beta={gtc_02.get('beta_est'):.3f}, "
          f"CAR(±10)={gtc_02.get('CAR_pm10'):.4f}")

    print("\n=== β-filter 達標 (β>0.6 & R²>0.3, 60D) ===")
    pass_df = stats[stats["pass_beta_filter"]][["beta_60d_vs_NVDA", "r2_60d_vs_NVDA", "in_07"]]
    print(pass_df.round(3).to_string())

    return stats, portfolio_summary, gtc_07, gtc_02


if __name__ == "__main__":
    main()
