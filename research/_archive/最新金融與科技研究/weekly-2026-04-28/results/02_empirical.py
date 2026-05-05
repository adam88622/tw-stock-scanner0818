# -*- coding: utf-8 -*-
"""
02_empirical.py
=================================================
GiS Genesis International Capital — Quantitative Research
Empirical validation: Taiwan Humanoid Robot Supply Chain Basket

抓取真實股價（yfinance）驗證 02_humanoid_robot_basket.py 設計的 basket 表現。
- 期間：2025-01-01 ~ 2026-04-28
- 標的：2049, 2308, 2317, 1536, 4571, 2382, 6121, 2233（核心 8 檔）
- Benchmark：^TWII（台股加權指數）
- 事件研究：2026-04-19（北京 humanoid 半馬）T-5 ~ T+5
"""

from __future__ import annotations
import json
import sys
from pathlib import Path
from datetime import datetime
import pandas as pd
import numpy as np
import yfinance as yf

# -----------------------------------------------------------------------------
# 設定
# -----------------------------------------------------------------------------
START = "2025-01-01"
END = "2026-04-29"  # yfinance end is exclusive
EVENT_DATE = "2026-04-19"  # 北京半馬
RF = 0.02  # risk-free rate (annual)
TRADING_DAYS = 252
HERE = Path(__file__).parent

# Basket 8 檔（PoC 內定義）+ 權重（normalised within these 8）
# 原 PoC 共 10 檔，這裡只取核心 8 檔並重新 normalize
# core_score, purity_score, liquidity_score 來自 PoC
BASKET = [
    # ticker, suffix候選 (上市/上櫃), name, core, purity, liq
    ("2049", [".TW"],          "上銀科技",   1.00, 0.50, 1.00),
    ("2308", [".TW"],          "台達電",     0.85, 0.20, 1.00),
    ("2317", [".TW"],          "鴻海",       0.75, 0.15, 1.00),
    ("1536", [".TW"],          "和大工業",   0.85, 0.45, 0.85),
    ("4571", [".TWO", ".TW"],  "鈞興-KY",    0.95, 0.80, 0.50),
    ("2382", [".TW"],          "廣達",       0.75, 0.15, 1.00),
    ("6121", [".TWO", ".TW"],  "新普科技",   0.65, 0.25, 0.85),
    ("2233", [".TWO", ".TW"],  "宇隆科技",   0.80, 0.55, 0.45),
]

W_CORE, W_PURITY, W_LIQ = 0.40, 0.40, 0.20


def design_weights():
    """重新計算 8 檔籃子權重（normalize 100%）。"""
    raws = []
    for tkr, _, name, c, p, l in BASKET:
        raws.append((tkr, name, W_CORE * c + W_PURITY * p + W_LIQ * l))
    total = sum(r[2] for r in raws)
    return {tkr: raw / total for tkr, _, raw in raws}, {tkr: name for tkr, _, name in raws}


# -----------------------------------------------------------------------------
# 抓資料：嘗試多後綴，挑能成功下載的
# -----------------------------------------------------------------------------
def try_fetch(ticker_root: str, suffixes: list[str], start: str, end: str):
    """逐一嘗試後綴，返回 (yf_ticker, close_series) 或 (None, None)。"""
    for suf in suffixes:
        ytkr = ticker_root + suf
        try:
            df = yf.download(ytkr, start=start, end=end, auto_adjust=True,
                             progress=False, threads=False)
            if df is None or df.empty:
                continue
            close = df["Close"]
            if isinstance(close, pd.DataFrame):
                close = close.iloc[:, 0]
            close = close.dropna()
            if len(close) < 30:
                continue
            close.name = ticker_root
            return ytkr, close
        except Exception as e:
            print(f"  [warn] {ytkr} fetch failed: {e}")
            continue
    return None, None


def fetch_all():
    """抓 8 檔 + ^TWII，回傳 (price_df, yf_ticker_map, missing_list)。"""
    print(f"[info] downloading {START} ~ {END} ...")
    series_map = {}
    yf_map = {}
    missing = []

    for tkr, suffixes, name, *_ in BASKET:
        ytkr, close = try_fetch(tkr, suffixes, START, END)
        if close is None:
            print(f"  [MISS] {tkr} {name}: not found via any suffix")
            missing.append(tkr)
        else:
            print(f"  [ok]   {tkr} {name} -> {ytkr}  rows={len(close)}")
            series_map[tkr] = close
            yf_map[tkr] = ytkr

    # benchmark
    ytkr, twii = try_fetch("^TWII", [""], START, END)
    if twii is not None:
        series_map["^TWII"] = twii
        yf_map["^TWII"] = "^TWII"
        print(f"  [ok]   ^TWII -> rows={len(twii)}")
    else:
        print("  [MISS] ^TWII benchmark missing")
        missing.append("^TWII")

    df = pd.concat(series_map, axis=1)
    df.columns = list(series_map.keys())
    df = df.sort_index().ffill().dropna(how="all")
    return df, yf_map, missing


# -----------------------------------------------------------------------------
# 個股指標
# -----------------------------------------------------------------------------
def stock_metrics(prices: pd.DataFrame, benchmark_col: str = "^TWII") -> pd.DataFrame:
    """每檔股票：YTD / 1Y / 3M return、年化波動、beta、Sharpe。"""
    rets = prices.pct_change().dropna(how="all")
    bench = rets[benchmark_col]
    end_dt = prices.index[-1]

    rows = []
    for col in prices.columns:
        s = prices[col].dropna()
        r = rets[col].dropna()
        if len(s) < 5:
            continue
        # YTD = 從 2026-01-02 (or first 2026 trading day)
        first_2026 = s[s.index >= "2026-01-01"]
        ytd = (s.iloc[-1] / first_2026.iloc[0] - 1) if len(first_2026) > 0 else np.nan
        # 1Y
        first_year = s[s.index >= (end_dt - pd.Timedelta(days=365))]
        y1 = (s.iloc[-1] / first_year.iloc[0] - 1) if len(first_year) > 1 else np.nan
        # 3M
        first_3m = s[s.index >= (end_dt - pd.Timedelta(days=90))]
        m3 = (s.iloc[-1] / first_3m.iloc[0] - 1) if len(first_3m) > 1 else np.nan
        # vol annualized
        vol = r.std() * np.sqrt(TRADING_DAYS)
        # beta vs ^TWII
        if col == benchmark_col:
            beta = 1.0
        else:
            common = pd.concat([r, bench], axis=1).dropna()
            common.columns = ["x", "b"]
            if len(common) > 30 and common["b"].var() > 0:
                beta = common.cov().iloc[0, 1] / common["b"].var()
            else:
                beta = np.nan
        # Sharpe (rf annual=2%)
        ann_ret = r.mean() * TRADING_DAYS
        sharpe = (ann_ret - RF) / vol if vol > 0 else np.nan
        rows.append({
            "ticker": col,
            "ytd_return": ytd,
            "ret_1y": y1,
            "ret_3m": m3,
            "ann_vol": vol,
            "beta_vs_twii": beta,
            "sharpe_rf2pct": sharpe,
            "ann_return": ann_ret,
        })
    return pd.DataFrame(rows).set_index("ticker")


# -----------------------------------------------------------------------------
# Basket NAV
# -----------------------------------------------------------------------------
def basket_nav(prices: pd.DataFrame, weights: dict, name: str = "basket") -> pd.Series:
    cols = [c for c in weights.keys() if c in prices.columns]
    sub = prices[cols].copy()
    rets = sub.pct_change().fillna(0.0)
    w = pd.Series({c: weights[c] for c in cols})
    w = w / w.sum()  # re-normalize for missing
    daily = (rets * w.values).sum(axis=1)
    nav = (1 + daily).cumprod()
    nav.name = name
    return nav


def nav_metrics(nav: pd.Series, benchmark_nav: pd.Series, label: str) -> dict:
    rets = nav.pct_change().dropna()
    b_rets = benchmark_nav.pct_change().dropna()
    common = pd.concat([rets, b_rets], axis=1).dropna()
    common.columns = ["p", "b"]
    ann_ret = rets.mean() * TRADING_DAYS
    ann_vol = rets.std() * np.sqrt(TRADING_DAYS)
    sharpe = (ann_ret - RF) / ann_vol if ann_vol > 0 else np.nan
    total = nav.iloc[-1] - 1
    bench_total = benchmark_nav.iloc[-1] - 1
    excess = total - bench_total
    if common["b"].var() > 0:
        beta = common.cov().iloc[0, 1] / common["b"].var()
    else:
        beta = np.nan
    # max drawdown
    rolling_max = nav.cummax()
    dd = (nav / rolling_max - 1).min()
    return {
        "label": label,
        "total_return": total,
        "bench_total_return": bench_total,
        "excess_return": excess,
        "ann_return": ann_ret,
        "ann_vol": ann_vol,
        "sharpe_rf2pct": sharpe,
        "beta_vs_twii": beta,
        "max_drawdown": dd,
    }


# -----------------------------------------------------------------------------
# 事件研究 T-5 ~ T+5
# -----------------------------------------------------------------------------
def event_window(prices: pd.DataFrame, event: str, k: int = 5,
                 benchmark_col: str = "^TWII") -> pd.DataFrame:
    """T-k ~ T+k 視窗，每檔 cumulative return + abnormal vs ^TWII."""
    idx = prices.index
    ev = pd.Timestamp(event)
    # 找最近且 <= ev 的交易日做 T0
    avail = idx[idx <= ev]
    if len(avail) == 0:
        return pd.DataFrame()
    t0 = avail[-1]
    pos = idx.get_loc(t0)
    lo = max(pos - k, 0)
    hi = min(pos + k + 1, len(idx))
    win = prices.iloc[lo:hi].copy()
    rets = win.pct_change().fillna(0.0)
    # cumulative since first day
    cum = (1 + rets).cumprod() - 1
    cum.index = [f"T{int((idx.get_loc(d) - pos))}" for d in cum.index]
    # abnormal cumulative = cum - cum_bench (col-wise)
    if benchmark_col in cum.columns:
        bench_cum = cum[benchmark_col]
        abn = cum.sub(bench_cum, axis=0)
    else:
        abn = cum.copy()
    return cum, abn, t0


# -----------------------------------------------------------------------------
# Correlation
# -----------------------------------------------------------------------------
def corr_matrix(prices: pd.DataFrame) -> pd.DataFrame:
    rets = prices.pct_change().dropna()
    return rets.corr()


# -----------------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------------
def main():
    weights, names = design_weights()
    eq_weights = {tkr: 1.0 / len(weights) for tkr in weights.keys()}

    print("=" * 80)
    print("Empirical Validation — Taiwan Humanoid Robot Basket")
    print("=" * 80)
    print(f"  Period         : {START} ~ {END}")
    print(f"  Event date     : {EVENT_DATE} (Beijing humanoid half marathon)")
    print(f"  Risk-free      : {RF*100:.1f}% annual")
    print()
    print("Designed weights (8-stock basket, re-normalised):")
    for tkr, w in sorted(weights.items(), key=lambda x: -x[1]):
        print(f"  {tkr} {names[tkr]:<8} {w*100:6.2f}%")
    print()

    # 1. Fetch
    prices, yf_map, missing = fetch_all()
    print(f"\n[info] price df shape: {prices.shape}")
    print(f"[info] columns: {list(prices.columns)}")
    if missing:
        print(f"[info] missing tickers: {missing}")

    # 2. Stock metrics
    print("\n[1] Per-stock metrics")
    smetrics = stock_metrics(prices, benchmark_col="^TWII")
    smetrics_r = smetrics.round(4)
    print(smetrics_r.to_string())
    smetrics.to_csv(HERE / "02_stock_metrics.csv", encoding="utf-8-sig")

    # 3. Basket NAV (equal + designed)
    print("\n[2] Basket NAV")
    benchmark = prices["^TWII"] / prices["^TWII"].iloc[0]
    benchmark.name = "^TWII"
    eq_nav = basket_nav(prices, eq_weights, name="equal_weight")
    dw_nav = basket_nav(prices, weights, name="designed_weight")

    eq_metrics = nav_metrics(eq_nav, benchmark, "equal_weight")
    dw_metrics = nav_metrics(dw_nav, benchmark, "designed_weight")
    bench_metrics = nav_metrics(benchmark, benchmark, "^TWII")

    nav_df = pd.concat([eq_nav, dw_nav, benchmark], axis=1)
    nav_df.columns = ["equal_weight", "designed_weight", "^TWII"]
    nav_df.to_csv(HERE / "02_basket_nav.csv", encoding="utf-8-sig")

    summary = pd.DataFrame([eq_metrics, dw_metrics, bench_metrics]).set_index("label")
    print(summary.round(4).to_string())
    summary.to_csv(HERE / "02_basket_summary.csv", encoding="utf-8-sig")

    # 4. Correlation matrix (8 stocks only)
    print("\n[3] Correlation matrix (8 stocks)")
    stock_cols = [c for c in prices.columns if c != "^TWII"]
    cm = corr_matrix(prices[stock_cols])
    print(cm.round(3).to_string())
    cm.to_csv(HERE / "02_correlation.csv", encoding="utf-8-sig")
    avg_off_diag = (cm.values.sum() - np.trace(cm.values)) / (cm.size - len(cm))
    print(f"  Average off-diagonal correlation: {avg_off_diag:.3f}")

    # 5. Event study
    print(f"\n[4] Event study around {EVENT_DATE} (T-5 ~ T+5)")
    cum_full, abn_full, t0 = event_window(prices, EVENT_DATE, k=5)
    print(f"  Anchor T0 = {t0.date()}")
    print("\n  Cumulative return (raw):")
    print(cum_full.round(4).to_string())
    print("\n  Abnormal cumulative return (vs ^TWII):")
    print(abn_full.round(4).to_string())
    cum_full.to_csv(HERE / "02_event_cumret.csv", encoding="utf-8-sig")
    abn_full.to_csv(HERE / "02_event_abnret.csv", encoding="utf-8-sig")

    # 6. Save JSON bundle
    bundle = {
        "meta": {
            "period_start": START,
            "period_end": END,
            "event_date": EVENT_DATE,
            "event_anchor_t0": str(t0.date()),
            "rf": RF,
            "yf_tickers": yf_map,
            "missing": missing,
            "generated_at": datetime.now().isoformat(timespec="seconds"),
        },
        "weights_designed": weights,
        "weights_equal": eq_weights,
        "stock_metrics": json.loads(smetrics.reset_index().to_json(orient="records")),
        "basket_summary": json.loads(summary.reset_index().to_json(orient="records")),
        "correlation_avg_off_diag": float(avg_off_diag),
        "correlation_matrix": json.loads(cm.to_json(orient="index")),
        "event_cumret": json.loads(cum_full.to_json(orient="index")),
        "event_abnret": json.loads(abn_full.to_json(orient="index")),
    }
    out_json = HERE / "02_empirical_results.json"
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(bundle, f, ensure_ascii=False, indent=2, default=str)

    # 7. Pretty summary header for log
    print("\n" + "=" * 80)
    print("SUMMARY")
    print("=" * 80)
    print(f"  TWII total return       : {bench_metrics['total_return']*100:+6.2f}%")
    print(f"  Equal-weight basket     : {eq_metrics['total_return']*100:+6.2f}%  "
          f"(excess {eq_metrics['excess_return']*100:+.2f}pp, "
          f"Sharpe {eq_metrics['sharpe_rf2pct']:.2f})")
    print(f"  Designed-weight basket  : {dw_metrics['total_return']*100:+6.2f}%  "
          f"(excess {dw_metrics['excess_return']*100:+.2f}pp, "
          f"Sharpe {dw_metrics['sharpe_rf2pct']:.2f})")
    print(f"  Avg off-diag corr       : {avg_off_diag:.3f}")
    print(f"\n[OK] outputs:")
    for f in HERE.glob("02_*"):
        print(f"   - {f.name}")
    print("\n[Done] Empirical validation finished.")


if __name__ == "__main__":
    main()
