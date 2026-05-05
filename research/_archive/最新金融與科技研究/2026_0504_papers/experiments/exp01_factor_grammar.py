"""
exp01_factor_grammar.py
========================
Huang et al. (2026) "Constrained LLM agents in cryptocurrency markets" 的 LLM-free 移植版。

原論文（arXiv:2604.26747）核心宣稱：
  使用 LLM 從假設出發、約束在合法因子文法內生成因子表達式，
  IC 篩選後組合 OOS Sharpe ~1.6（vs alpha101 baseline 0.9）。

我們**去掉 LLM 部分**，純用 grammar-based 隨機抽樣：
  - Atom: open, high, low, close, volume, amount
  - Op:   ts_mean(N), ts_std(N), ts_rank(N), delta(N), log, sign, abs, zscore_xs
  - Depth ≤ 3
  - 隨機抽 1000 個 expression
  - IS (2020-2023) IC 篩選 (|IC| > 0.03 + |t-stat| > 2)
  - OOS (2025-04 ~ 2026-04) 測試 long-short Q1-Q5 累積報酬

如果結論是「即使去掉 LLM，純 grammar+IC 篩選 OOS Sharpe > 0.6」，
就證明 LLM 不是論文價值的核心，可以省下 API 成本。
"""
import sqlite3
import numpy as np
import pandas as pd
import json
import random
from pathlib import Path
from scipy.stats import spearmanr

DB = r"D:\claude\tw-stock-scanner\db\scanner.db"
HERE = Path(__file__).parent
OUT_JSON = HERE / "exp01_results.json"
OUT_CSV = HERE / "exp01_factor_summary.csv"

IS_START, IS_END = "2020-01-01", "2023-12-31"
OOS_START, OOS_END = "2025-04-01", "2026-04-30"
N_SAMPLES = 800
MAX_DEPTH = 2  # 降深度避免退化巢狀
IC_THRESHOLD = 0.025
IR_THRESHOLD = 0.20
RNG = random.Random(20260504)
NPRNG = np.random.default_rng(20260504)
TOP_N_STOCKS = 100


def load_top_stocks(top_n: int = TOP_N_STOCKS) -> list:
    conn = sqlite3.connect(DB)
    df = pd.read_sql(f"""
        SELECT stock_id, AVG(trade_value) AS tv, COUNT(*) AS n
        FROM daily_prices WHERE date BETWEEN '{IS_START}' AND '{OOS_END}'
        GROUP BY stock_id HAVING n > 1300
        ORDER BY tv DESC LIMIT {top_n * 2}
    """, conn)
    conn.close()
    df = df[df["stock_id"].str.match(r"^\d{4}$")]
    return df["stock_id"].head(top_n).tolist()


def load_panels(stock_ids: list) -> dict:
    conn = sqlite3.connect(DB)
    placeholders = ",".join("?" * len(stock_ids))
    df = pd.read_sql(f"""
        SELECT date, stock_id, open_price, high_price, low_price, close_price, volume, trade_value, change_pct
        FROM daily_prices WHERE stock_id IN ({placeholders})
          AND date BETWEEN ? AND ? ORDER BY date, stock_id
    """, conn, params=stock_ids + [IS_START, OOS_END])
    conn.close()
    df["date"] = pd.to_datetime(df["date"])
    panels = {}
    for col, alias in [("open_price", "open"), ("high_price", "high"),
                        ("low_price", "low"), ("close_price", "close"),
                        ("volume", "volume"), ("trade_value", "amount"),
                        ("change_pct", "ret")]:
        p = df.pivot(index="date", columns="stock_id", values=col)
        panels[alias] = p.ffill().fillna(0.0) if alias != "ret" else (p / 100.0).fillna(0.0)
    return panels


def safe_log(x):
    return np.log(np.where(x > 0, x, np.nan))


def safe_sign(x):
    return np.sign(x)


def ts_mean(x, n):
    return x.rolling(n, min_periods=max(2, n // 2)).mean()


def ts_std(x, n):
    return x.rolling(n, min_periods=max(2, n // 2)).std()


def ts_rank(x, n):
    return x.rolling(n, min_periods=max(2, n // 2)).rank(pct=True)


def delta(x, n):
    return x - x.shift(n)


def zscore_xs(x):
    """cross-sectional zscore（每個 date 跨股票）。"""
    return x.sub(x.mean(axis=1), axis=0).div(x.std(axis=1) + 1e-9, axis=0)


# Grammar
ATOMS = ["open", "high", "low", "close", "volume", "amount", "ret"]
TS_OPS = [("ts_mean", [5, 10, 20]),
          ("ts_std", [5, 10, 20]),
          ("ts_rank", [10, 20]),
          ("delta", [1, 5, 10])]
ELEM_OPS = ["log", "sign", "zscore_xs"]


def random_expr(depth=0, parent_op=None):
    if depth >= MAX_DEPTH or (depth > 0 and RNG.random() < 0.5):
        return ("atom", RNG.choice(ATOMS))
    if RNG.random() < 0.65:
        op, ns = RNG.choice(TS_OPS)
        n = RNG.choice(ns)
        # 避免退化：sign(sign), log(log)
        return ("ts", op, n, random_expr(depth + 1, op))
    while True:
        op = RNG.choice(ELEM_OPS)
        if op != parent_op:  # 不重複套相同 elem
            break
    return ("elem", op, random_expr(depth + 1, op))


def expr_str(e):
    if e[0] == "atom":
        return e[1]
    if e[0] == "ts":
        return f"{e[1]}({expr_str(e[3])}, {e[2]})"
    if e[0] == "elem":
        return f"{e[1]}({expr_str(e[2])})"
    return "?"


def evaluate_expr(e, panels):
    if e[0] == "atom":
        return panels[e[1]].copy()
    inner = evaluate_expr(e[-1] if e[0] == "elem" else e[3], panels)
    if e[0] == "ts":
        op = e[1]
        n = e[2]
        if op == "ts_mean":
            return ts_mean(inner, n)
        if op == "ts_std":
            return ts_std(inner, n)
        if op == "ts_rank":
            return ts_rank(inner, n)
        if op == "delta":
            return delta(inner, n)
    if e[0] == "elem":
        op = e[1]
        if op == "log":
            return pd.DataFrame(safe_log(inner.values), index=inner.index, columns=inner.columns)
        if op == "sign":
            return pd.DataFrame(safe_sign(inner.values), index=inner.index, columns=inner.columns)
        if op == "zscore_xs":
            return zscore_xs(inner)
    return inner


def calc_ic_ts(factor: pd.DataFrame, fwd_ret: pd.DataFrame) -> tuple:
    """逐日截面 Spearman IC。回傳 mean IC, IC IR, n_days, frac_valid_days."""
    f, r = factor.align(fwd_ret, join="inner")
    ics = []
    n_total = 0
    for d in f.index:
        x = f.loc[d].dropna()
        y = r.loc[d].reindex(x.index).dropna()
        x = x.reindex(y.index)
        if len(x) < 20:
            continue
        n_total += 1
        if x.std() < 1e-10 or x.nunique() < 5:  # 至少 5 個不同值
            continue
        ic, _ = spearmanr(x.values, y.values)
        if not np.isnan(ic):
            ics.append(ic)
    if len(ics) < 50:  # 至少要有 50 個有效日
        return float("nan"), float("nan"), 0, 0.0
    ics = np.array(ics)
    valid_frac = len(ics) / max(n_total, 1)
    return float(ics.mean()), float(ics.mean() / (ics.std() + 1e-9)), len(ics), float(valid_frac)


def long_short_returns(factor: pd.DataFrame, fwd_ret: pd.DataFrame, n_quantile: int = 5) -> pd.Series:
    """每日依 factor 排序，買最高分位、賣最低分位。"""
    daily_ls = []
    f, r = factor.align(fwd_ret, join="inner")
    for d in f.index:
        x = f.loc[d].dropna()
        y = r.loc[d].reindex(x.index).dropna()
        x = x.reindex(y.index)
        if len(x) < 20:
            daily_ls.append(0.0)
            continue
        q_lo, q_hi = x.quantile(1 / n_quantile), x.quantile(1 - 1 / n_quantile)
        long_set = y[x >= q_hi]
        short_set = y[x <= q_lo]
        if len(long_set) == 0 or len(short_set) == 0:
            daily_ls.append(0.0)
            continue
        daily_ls.append(float(long_set.mean() - short_set.mean()))
    return pd.Series(daily_ls, index=f.index)


def main():
    print("=" * 70)
    print("Exp 01: Grammar-Based Factor Mining (LLM-free port)")
    print("=" * 70)

    pool = load_top_stocks()
    print(f"\nPool ({len(pool)}): {pool[:8]} ...")
    panels = load_panels(pool)
    print(f"Panel: close shape = {panels['close'].shape}")

    # 5 日 forward return
    fwd_ret = panels["close"].pct_change(5).shift(-5)

    is_dates = (fwd_ret.index >= IS_START) & (fwd_ret.index <= IS_END)
    oos_dates = (fwd_ret.index >= OOS_START) & (fwd_ret.index <= OOS_END)

    # 1) 隨機抽 N 個 expression
    print(f"\nSampling {N_SAMPLES} random expressions...")
    candidates = []
    seen = set()
    while len(candidates) < N_SAMPLES:
        e = random_expr()
        s = expr_str(e)
        if s not in seen:
            seen.add(s)
            candidates.append(e)

    # 2) IS IC 篩選
    print("Computing IS IC for all candidates...")
    rows = []
    for i, e in enumerate(candidates):
        try:
            f = evaluate_expr(e, panels)
            f_is = f.loc[is_dates]
            r_is = fwd_ret.loc[is_dates]
            ic_mean, ic_ir, n_used, valid_frac = calc_ic_ts(f_is, r_is)
            rows.append({"expr": expr_str(e), "ic_mean_is": ic_mean,
                          "ic_ir_is": ic_ir, "n_days_is": n_used,
                          "valid_frac_is": valid_frac,
                          "_e": e})
            if i % 100 == 99:
                print(f"  ... {i+1}/{len(candidates)}")
        except Exception:
            continue

    df = pd.DataFrame([r for r in rows]).drop(columns=["_e"], errors="ignore")
    df = df.dropna(subset=["ic_mean_is"])
    print(f"\nValid factors: {len(df)}")
    if len(df) == 0:
        print("No valid factors generated.")
        return

    # 3) 篩選：排除退化（valid_frac 太低）後依 IC|IR 取 top 15
    df["abs_ic"] = df["ic_mean_is"].abs()
    df["abs_ir"] = df["ic_ir_is"].abs()
    df_clean = df[(df["valid_frac_is"] > 0.7) & (df["abs_ic"] > IC_THRESHOLD)].copy()
    selected_df = df_clean.sort_values("abs_ic", ascending=False).head(15)
    print(f"\nSelected (valid_frac>0.7, |IC|>{IC_THRESHOLD}, top 15): {len(selected_df)}")
    print(f"IS IC distribution: |IC| 75%/90%/max = "
          f"{df['abs_ic'].quantile([0.75, 0.9]).round(4).tolist() + [round(df['abs_ic'].max(), 4)]}")
    print(selected_df[["expr", "ic_mean_is", "ic_ir_is"]].to_string(index=False))

    # 4) OOS 測試 top 5 個別 + 等權組合
    print("\n--- OOS evaluation ---")
    expr_lookup = {expr_str(e): e for e in candidates}
    oos_results = []
    ls_returns_combined = []
    for _, row in selected_df.head(10).iterrows():
        e = expr_lookup.get(row["expr"])
        if e is None:
            continue
        try:
            f_full = evaluate_expr(e, panels)
            f_oos = f_full.loc[oos_dates]
            r_oos = fwd_ret.loc[oos_dates]
            ic_mean_oos, ic_ir_oos, _, _ = calc_ic_ts(f_oos, r_oos)
            ls = long_short_returns(f_oos, r_oos, 5)
            sign = np.sign(row["ic_mean_is"])  # 統一方向
            ls_signed = ls * sign
            ann_ret = ls_signed.mean() * 252 / 5  # 5 日 fwd 重疊修正
            ann_vol = ls_signed.std() * np.sqrt(252 / 5)
            sharpe = ann_ret / ann_vol if ann_vol > 1e-9 else 0.0
            ls_returns_combined.append(ls_signed)
            oos_results.append({
                "expr": row["expr"],
                "ic_is": row["ic_mean_is"],
                "ic_oos": ic_mean_oos,
                "ic_oos_consistent_sign": int(np.sign(ic_mean_oos) == np.sign(row["ic_mean_is"])),
                "ann_ret_long_short": float(ann_ret),
                "ann_vol_long_short": float(ann_vol),
                "sharpe_oos_long_short": float(sharpe),
            })
            print(f"  {row['expr'][:50]:<50}  IC_IS={row['ic_mean_is']:+.3f} IC_OOS={ic_mean_oos:+.3f}  Sharpe_OOS={sharpe:.3f}")
        except Exception as e2:
            print(f"  [error] {row['expr']}: {e2}")

    # 5) 等權組合
    if ls_returns_combined:
        combined = pd.concat(ls_returns_combined, axis=1).mean(axis=1)
        ann_ret_c = combined.mean() * 252 / 5
        ann_vol_c = combined.std() * np.sqrt(252 / 5)
        sharpe_c = ann_ret_c / ann_vol_c if ann_vol_c > 1e-9 else 0.0
        print(f"\nCombined (equal-weight top {len(ls_returns_combined)}): Sharpe_OOS = {sharpe_c:.3f}")
    else:
        sharpe_c = float("nan")

    n_consistent = sum(r["ic_oos_consistent_sign"] for r in oos_results)
    paper_compare = {
        "paper_oos_sharpe_combined": "1.6 (BTC/ETH, LLM-generated)",
        "paper_baseline_sharpe": "0.9 (alpha101)",
        "our_n_factors_selected": int(len(selected_df)),
        "our_oos_combined_sharpe": round(sharpe_c, 3) if np.isfinite(sharpe_c) else None,
        "our_n_factors_with_consistent_sign_oos": int(n_consistent),
        "our_pct_consistent_sign": round(n_consistent / max(len(oos_results), 1), 2),
        "verdict": (
            "[OK] grammar+IC alone yields exploitable OOS signal"
            if (np.isfinite(sharpe_c) and sharpe_c > 0.6 and
                n_consistent / max(len(oos_results), 1) > 0.5)
            else ("[partial]" if np.isfinite(sharpe_c) and sharpe_c > 0
                  else "[X] grammar alone insufficient")
        ),
        "interpretation": (
            f"LLM-free grammar+IC 在台股 OOS Sharpe = {sharpe_c:.2f}; "
            f"{n_consistent}/{len(oos_results)} factors maintain consistent IC sign — "
            f"{'meaningful baseline before LLM' if sharpe_c > 0.5 else 'LLM 仍可能必要'}"
        ),
    }

    print("\n--- vs paper ---")
    print(json.dumps(paper_compare, ensure_ascii=False, indent=2))

    df.to_csv(OUT_CSV, index=False)
    OUT_JSON.write_text(
        json.dumps({
            "selected_factors": selected_df[["expr", "ic_mean_is", "ic_ir_is"]].to_dict(orient="records"),
            "oos_per_factor": oos_results,
            "paper_compare": paper_compare,
        }, ensure_ascii=False, indent=2),
        encoding="utf-8")
    print(f"\nWrote: {OUT_JSON}")


if __name__ == "__main__":
    main()
