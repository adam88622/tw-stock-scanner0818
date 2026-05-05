"""
exp03_higher_moment.py
======================
驗證 Wang/Niu/Sheshmani/Yau (2026) 高階矩組合最佳化在台股的增量價值。

原論文（arXiv:2604.25378）核心宣稱：
  在 mean-variance 之上加入 skew preference + kurt aversion，
  S&P500 OOS Sharpe +0.18、MaxDD 改善 3-5%。

我們做簡化版：
  - 標的：流動性 top 50 個股
  - IS: 2020-01 ~ 2024-12（5 年）
  - OOS: 2025-01 ~ 2026-04
  - 三組合對比：
      (a) Mean-Variance baseline (lambda_2=10)
      (b) MV + skew preference (lambda_3=1)
      (c) MV + skew + kurt aversion (lambda_3=1, lambda_4=0.5)

我們不重現 Yau 的 affine-normal descent；用 scipy SLSQP 直接解（N=50 還可以）。
"""
import sqlite3
import numpy as np
import pandas as pd
import json
from pathlib import Path
from scipy.optimize import minimize

DB = r"D:\claude\tw-stock-scanner\db\scanner.db"
HERE = Path(__file__).parent
OUT_JSON = HERE / "exp03_results.json"

IS_START, IS_END = "2020-01-01", "2024-12-31"
OOS_START, OOS_END = "2025-01-01", "2026-04-30"

# 高階矩項以 daily 報酬計算，量級 O(sigma^k)。
# 為了讓 skew/kurt 有實際影響，使用無單位 standardized moments 做為 objective。
# 直接放大 lambda：高階矩相對 mean-variance 需要 1e4~1e9 量級才能平衡。
LAMBDA2 = 10.0
LAMBDA3 = 5.0e5
LAMBDA4 = 5.0e8


def load_top_stocks(top_n: int = 50) -> list:
    conn = sqlite3.connect(DB)
    df = pd.read_sql(f"""
        SELECT stock_id, AVG(trade_value) AS avg_tv, COUNT(*) AS n
        FROM daily_prices
        WHERE date BETWEEN '{IS_START}' AND '{IS_END}'
        GROUP BY stock_id
        HAVING n > 1100
        ORDER BY avg_tv DESC
        LIMIT {top_n * 2}
    """, conn)
    conn.close()
    df = df[df["stock_id"].str.match(r"^\d{4}$")]
    return df["stock_id"].head(top_n).tolist()


def load_panel(stock_ids: list, start: str, end: str) -> pd.DataFrame:
    conn = sqlite3.connect(DB)
    placeholders = ",".join("?" * len(stock_ids))
    df = pd.read_sql(f"""
        SELECT date, stock_id, change_pct
        FROM daily_prices
        WHERE stock_id IN ({placeholders}) AND date BETWEEN ? AND ?
        ORDER BY date, stock_id
    """, conn, params=stock_ids + [start, end])
    conn.close()
    df["date"] = pd.to_datetime(df["date"])
    pivot = df.pivot(index="date", columns="stock_id", values="change_pct") / 100.0
    return pivot.dropna(axis=1, thresh=int(0.95 * len(pivot))).fillna(0.0)


def coskew_w3(w: np.ndarray, R_centered: np.ndarray) -> float:
    """組合三階共動矩 = E[(R w)^3] (centered)。"""
    p = R_centered @ w
    return float(np.mean(p ** 3))


def cokurt_w4(w: np.ndarray, R_centered: np.ndarray) -> float:
    p = R_centered @ w
    return float(np.mean(p ** 4))


def grad_coskew(w: np.ndarray, R_centered: np.ndarray) -> np.ndarray:
    p = R_centered @ w
    return 3.0 * (R_centered.T @ (p ** 2)) / len(p)


def grad_cokurt(w: np.ndarray, R_centered: np.ndarray) -> np.ndarray:
    p = R_centered @ w
    return 4.0 * (R_centered.T @ (p ** 3)) / len(p)


def optimize_portfolio(mu, Sigma, R_centered, lam2, lam3, lam4, w0=None):
    n = len(mu)
    if w0 is None:
        w0 = np.ones(n) / n

    def neg_obj(w):
        mv = mu @ w - 0.5 * lam2 * w @ Sigma @ w
        sk = (lam3 / 6.0) * coskew_w3(w, R_centered)
        ku = (lam4 / 24.0) * cokurt_w4(w, R_centered)
        return -(mv + sk - ku)

    def neg_grad(w):
        g_mv = mu - lam2 * (Sigma @ w)
        g_sk = (lam3 / 6.0) * grad_coskew(w, R_centered)
        g_ku = (lam4 / 24.0) * grad_cokurt(w, R_centered)
        return -(g_mv + g_sk - g_ku)

    cons = [{"type": "eq", "fun": lambda w: np.sum(w) - 1.0}]
    bounds = [(0.0, 0.10)] * n  # 單檔 0~10%
    res = minimize(neg_obj, w0, jac=neg_grad, method="SLSQP",
                   constraints=cons, bounds=bounds,
                   options={"maxiter": 300, "ftol": 1e-9})
    return res.x


def evaluate_portfolio(weights: np.ndarray, oos_returns: pd.DataFrame) -> dict:
    """OOS 績效：Sharpe / MaxDD / Calmar / 偏度。"""
    port = oos_returns.values @ weights
    port = port[~np.isnan(port)]
    if len(port) < 30:
        return {}
    mean_d, std_d = float(port.mean()), float(port.std(ddof=1))
    ann_ret = mean_d * 252
    ann_vol = std_d * np.sqrt(252)
    sharpe = ann_ret / ann_vol if ann_vol > 0 else 0.0
    cum = np.cumprod(1 + port)
    dd = cum / np.maximum.accumulate(cum) - 1.0
    max_dd = float(dd.min())
    calmar = ann_ret / abs(max_dd) if max_dd < 0 else float("nan")
    return {
        "ann_return": round(ann_ret, 4),
        "ann_vol": round(ann_vol, 4),
        "sharpe": round(sharpe, 4),
        "max_drawdown": round(max_dd, 4),
        "calmar": round(calmar, 4) if np.isfinite(calmar) else None,
        "skew": round(float(pd.Series(port).skew()), 4),
        "kurt_excess": round(float(pd.Series(port).kurt()), 4),
        "n_days": int(len(port)),
    }


def main():
    print("=" * 70)
    print("Exp 03: Higher-Moment Portfolio Optimization — Yau et al. (2026)")
    print("=" * 70)

    pool = load_top_stocks(50)
    print(f"\nPool: {pool[:10]} ... ({len(pool)} 檔)")

    R_is = load_panel(pool, IS_START, IS_END)
    R_oos = load_panel(pool, OOS_START, OOS_END)

    common = R_is.columns.intersection(R_oos.columns)
    R_is, R_oos = R_is[common], R_oos[common]
    print(f"IS: {R_is.shape}, OOS: {R_oos.shape}")

    # 估計 IS 統計量
    mu = R_is.mean().values * 252
    Sigma = R_is.cov().values * 252
    R_centered = (R_is - R_is.mean()).values

    configs = [
        ("MV-only baseline", LAMBDA2, 0.0, 0.0),
        ("+ skew pref", LAMBDA2, LAMBDA3, 0.0),
        ("+ skew + kurt", LAMBDA2, LAMBDA3, LAMBDA4),
    ]

    results = []
    for name, l2, l3, l4 in configs:
        w = optimize_portfolio(mu, Sigma, R_centered, l2, l3, l4)
        perf_is = evaluate_portfolio(w, R_is)
        perf_oos = evaluate_portfolio(w, R_oos)
        results.append({
            "config": name,
            "lambda2": l2, "lambda3": l3, "lambda4": l4,
            "weight_top5": [
                {"sid": str(common[i]), "w": round(float(w[i]), 4)}
                for i in np.argsort(w)[::-1][:5]
            ],
            "n_active": int(np.sum(w > 0.01)),
            "is_perf": perf_is,
            "oos_perf": perf_oos,
        })
        print(f"\n[{name}]  active={int(np.sum(w>0.01))}")
        print("  IS :", perf_is)
        print("  OOS:", perf_oos)

    # 對比表
    print("\n--- OOS Comparison ---")
    print(f"{'Config':<25}{'Sharpe':>8}{'MaxDD':>10}{'Calmar':>10}{'Skew':>8}")
    for r in results:
        p = r["oos_perf"]
        print(f"{r['config']:<25}{p.get('sharpe',0):>8.3f}"
              f"{p.get('max_drawdown',0):>10.3f}"
              f"{(p.get('calmar') or 0):>10.3f}"
              f"{p.get('skew',0):>8.3f}")

    # 計算 +skew vs baseline 的 Sharpe gain
    base = results[0]["oos_perf"]
    gain_sk = results[1]["oos_perf"].get("sharpe", 0) - base.get("sharpe", 0)
    gain_full = results[2]["oos_perf"].get("sharpe", 0) - base.get("sharpe", 0)
    # MaxDD 越接近 0 越好；improvement = new - base（new 較不負時為正）
    dd_imp_sk = results[1]["oos_perf"].get("max_drawdown", 0) - base.get("max_drawdown", 0)
    dd_imp_full = results[2]["oos_perf"].get("max_drawdown", 0) - base.get("max_drawdown", 0)

    paper_compare = {
        "paper_sharpe_gain": "+0.18 (S&P500)",
        "our_sharpe_gain_skew_only": round(gain_sk, 4),
        "our_sharpe_gain_full": round(gain_full, 4),
        "paper_dd_improvement": "3-5 ppt (S&P500)",
        "our_dd_improvement_skew_only_ppt": round(dd_imp_sk * 100, 2),
        "our_dd_improvement_full_ppt": round(dd_imp_full * 100, 2),
        "verdict": (
            "[OK] supports paper direction" if (gain_full > 0.005 and dd_imp_full > 0.03)
            else ("[partial — DD direction matches]" if (dd_imp_full > 0.01 or gain_full > 0)
                  else "[X] does not replicate")
        ),
        "interpretation": (
            f"DD improvement {dd_imp_full*100:.2f}ppt {'matches' if 0.02<=dd_imp_full<=0.07 else 'differs from'} paper's 3-5ppt; "
            f"Sharpe gain {gain_full:.3f} vs paper +0.18 — direction "
            f"{'consistent' if gain_full > 0 else 'opposite'}"
        ),
    }
    print("\n--- vs. paper ---")
    print(json.dumps(paper_compare, ensure_ascii=False, indent=2))

    OUT_JSON.write_text(
        json.dumps({"results": results, "paper_compare": paper_compare},
                   ensure_ascii=False, indent=2),
        encoding="utf-8")
    print(f"\nWrote: {OUT_JSON}")


if __name__ == "__main__":
    main()
