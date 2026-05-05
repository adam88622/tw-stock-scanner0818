"""
exp02_levered_etf.py
====================
驗證 Bianchi & Goldberg (2026) 槓桿 ETF 波動拖曳閉式解。

原論文（arXiv:2604.27287）核心宣稱：
  drag(k) ≈ 0.5 * k * (k - 1) * sigma^2  (年化)

我們用 0050 日報酬合成 daily-reset leveraged：
  - 2x:   r2x_t  = 2 * r_t
  - -1x:  rinv_t = -1 * r_t
  - 3x:   r3x_t  = 3 * r_t

然後比較：
  empirical_terminal      = prod(1 + r_levered_t)
  naive_compound_terminal = (prod(1 + r_t)) ** k
  drag = naive - empirical

並按年度切割計算 sigma_t、empirical_drag_t，畫 sigma vs drag 散布圖
驗證閉式解 0.5 * k * (k-1) * sigma^2。
"""
import sqlite3
import numpy as np
import pandas as pd
import json
from pathlib import Path

DB = r"D:\claude\tw-stock-scanner\db\scanner.db"
HERE = Path(__file__).parent
OUT_CSV = HERE / "exp02_results.csv"
OUT_JSON = HERE / "exp02_results.json"


def load_returns(stock_id: str = "0050") -> pd.DataFrame:
    """讀取個股日報酬。
    優先使用 change_pct（已校正股利/分割），剔除 |change_pct| > 11% 的明顯異常。
    """
    conn = sqlite3.connect(DB)
    df = pd.read_sql(
        "SELECT date, close_price, change_pct FROM daily_prices WHERE stock_id=? ORDER BY date",
        conn, params=(stock_id,))
    conn.close()
    df["date"] = pd.to_datetime(df["date"])
    df = df.dropna(subset=["change_pct"]).set_index("date").sort_index()
    df["ret"] = df["change_pct"] / 100.0
    df = df[df["ret"].abs() <= 0.11]  # 台股漲跌停 10%
    return df


def synthetic_levered(returns: pd.Series, k: float) -> pd.Series:
    """每日 reset leveraged ETF 報酬。"""
    return k * returns


def yearly_drag_analysis(df: pd.DataFrame, ks=(2.0, -1.0, 3.0)) -> pd.DataFrame:
    """按年度計算 sigma 與三組 leverage 的 drag。"""
    rows = []
    for year, sub in df.groupby(df.index.year):
        if len(sub) < 100:
            continue
        r = sub["ret"].values
        sigma_d = float(np.std(r, ddof=1))
        sigma_a = sigma_d * np.sqrt(252)
        n = len(r)
        period_factor = n / 252.0  # 年化
        # 1x baseline
        ret_1x = np.prod(1 + r) - 1
        for k in ks:
            r_lev = k * r
            ret_lev = np.prod(1 + r_lev) - 1
            naive = (1 + ret_1x) ** k - 1  # naive = 1x 累積後再 k 次方
            empirical_drag = naive - ret_lev
            theoretical_drag = 0.5 * k * (k - 1) * sigma_d ** 2 * n
            rows.append({
                "year": int(year),
                "k": k,
                "n_days": n,
                "sigma_daily": sigma_d,
                "sigma_annual": sigma_a,
                "ret_1x_period": ret_1x,
                "ret_levered_actual": ret_lev,
                "ret_naive_compound": naive,
                "drag_empirical": empirical_drag,
                "drag_theoretical": theoretical_drag,
                "abs_error": abs(empirical_drag - theoretical_drag),
            })
    return pd.DataFrame(rows)


def long_horizon_summary(df: pd.DataFrame, ks=(2.0, -1.0, 3.0)) -> dict:
    r = df["ret"].values
    n = len(r)
    sigma_d = float(np.std(r, ddof=1))
    years = n / 252.0
    sigma_a = sigma_d * np.sqrt(252)
    out = {
        "stock_id": "0050",
        "start": df.index[0].strftime("%Y-%m-%d"),
        "end": df.index[-1].strftime("%Y-%m-%d"),
        "n_days": int(n),
        "n_years": round(years, 2),
        "annual_sigma": round(sigma_a, 4),
        "scenarios": {},
    }
    base_terminal = float(np.prod(1 + r))
    for k in ks:
        lev_terminal = float(np.prod(1 + k * r))
        naive_terminal = base_terminal ** k if base_terminal > 0 else float("nan")
        # 用對數差比（避免複數）
        if lev_terminal > 0 and naive_terminal > 0:
            ann_drag = (np.log(naive_terminal) - np.log(lev_terminal)) / years
        else:
            ann_drag = float("nan")
        theoretical_ann_drag = 0.5 * k * (k - 1) * sigma_a ** 2
        out["scenarios"][f"k={k}"] = {
            "terminal_actual": round(lev_terminal, 4),
            "terminal_naive": round(naive_terminal, 4) if np.isfinite(naive_terminal) else None,
            "annual_drag_empirical_log": round(ann_drag, 6) if np.isfinite(ann_drag) else None,
            "annual_drag_theoretical": round(theoretical_ann_drag, 6),
            "annualized_error_bp": round((ann_drag - theoretical_ann_drag) * 10000, 2) if np.isfinite(ann_drag) else None,
        }
    return out


def main():
    print("=" * 70)
    print("Exp 02: Levered ETF Volatility Drag — Bianchi & Goldberg (2026)")
    print("=" * 70)

    df = load_returns("0050")
    print(f"\nLoaded 0050: {len(df)} days from {df.index[0].date()} to {df.index[-1].date()}")
    print(f"Daily sigma: {df['ret'].std():.4f}, Annual sigma: {df['ret'].std() * np.sqrt(252):.4f}")

    # 1) 全期間 long-horizon 對比
    summary = long_horizon_summary(df)
    print("\n--- Long-horizon (full sample) ---")
    print(json.dumps(summary, indent=2, ensure_ascii=False))

    # 2) 按年度
    yearly = yearly_drag_analysis(df)
    print("\n--- Year-by-year drag (k=2, -1, 3) ---")
    print(yearly.to_string(index=False))

    # 3) 驗證閉式解：相關性 / 線性回歸
    print("\n--- Closed-form validation: empirical_drag vs theoretical_drag ---")
    val_rows = []
    for k in (2.0, -1.0, 3.0):
        sub = yearly[yearly["k"] == k]
        x = sub["drag_theoretical"].values
        y = sub["drag_empirical"].values
        # OLS slope (no intercept enforces theory test)
        slope = float(np.dot(x, y) / np.dot(x, x)) if np.dot(x, x) > 0 else float("nan")
        corr = float(np.corrcoef(x, y)[0, 1])
        mae_bp = float(np.mean(np.abs(y - x)) * 10000)
        val_rows.append({
            "k": k,
            "slope_no_intercept": round(slope, 4),
            "corr": round(corr, 4),
            "mae_bp_period": round(mae_bp, 2),
            "verdict": "✓ supports paper" if (0.7 <= slope <= 1.3 and corr > 0.7) else "✗ deviates",
        })
        print(f"k={k}: slope={slope:.4f}, corr={corr:.4f}, MAE={mae_bp:.1f} bp/period")

    # 寫入結果
    yearly.to_csv(OUT_CSV, index=False, encoding="utf-8")
    out = {"summary_long_horizon": summary, "validation_per_k": val_rows}
    OUT_JSON.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nWrote: {OUT_CSV}")
    print(f"Wrote: {OUT_JSON}")


if __name__ == "__main__":
    main()
