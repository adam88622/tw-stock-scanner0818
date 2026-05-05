"""
exp07_context_features.py
=========================
Lazanas et al. (2026) Context-Integrated Adversarial Learning 的階段 A 簡化版。

原論文（arXiv:2604.22801）核心宣稱：
  純價格 LSTM baseline 加上 context features（macro/event/sector）後 OOS R² +15-25%；
  其中 adversarial training 提供 ~8% R²，其餘來自 context。

我們階段 A 只測「context 部分價值」：
  Baseline:  純動量/反轉/波動 (ret_5, ret_20, vol_20, rsi)
  + Context: + macro_indicators (T10Y3M zscore, etc.)
             + institutional foreign_buy_5d
             + regime_history.regime（one-hot）

模型用 GradientBoostingRegressor（CPU 即可），time-series CV，
比較兩組設定的 OOS R² / IC / 5日 long-short Sharpe。
"""
import sqlite3
import numpy as np
import pandas as pd
import json
from pathlib import Path
from sklearn.ensemble import GradientBoostingRegressor
from scipy.stats import spearmanr
import warnings
warnings.filterwarnings("ignore")

DB = r"D:\claude\tw-stock-scanner\db\scanner.db"
HERE = Path(__file__).parent
OUT_JSON = HERE / "exp07_results.json"

IS_START, IS_END = "2020-01-01", "2024-12-31"
OOS_START, OOS_END = "2025-01-01", "2026-04-30"
TOP_N = 60


def load_top_stocks(top_n: int = TOP_N) -> list:
    conn = sqlite3.connect(DB)
    df = pd.read_sql(f"""
        SELECT stock_id, AVG(trade_value) AS tv, COUNT(*) AS n
        FROM daily_prices WHERE date BETWEEN '{IS_START}' AND '{OOS_END}'
        GROUP BY stock_id HAVING n > 1400
        ORDER BY tv DESC LIMIT {top_n * 2}
    """, conn)
    conn.close()
    df = df[df["stock_id"].str.match(r"^\d{4}$")]
    return df["stock_id"].head(top_n).tolist()


def build_features(stock_id: str) -> pd.DataFrame:
    conn = sqlite3.connect(DB)
    df = pd.read_sql(f"""
        SELECT date, close_price, change_pct, volume, trade_value
        FROM daily_prices WHERE stock_id = ? AND date BETWEEN ? AND ?
        ORDER BY date
    """, conn, params=(stock_id, IS_START, OOS_END))
    inst = pd.read_sql(f"""
        SELECT date, foreign_buy, sitc_buy, dealer_buy, total_buy
        FROM institutional WHERE stock_id = ? AND date BETWEEN ? AND ?
        ORDER BY date
    """, conn, params=(stock_id, IS_START, OOS_END))
    conn.close()

    df["date"] = pd.to_datetime(df["date"])
    df = df.set_index("date").sort_index()
    df = df[df["change_pct"].abs() <= 11].copy()
    df["ret"] = df["change_pct"] / 100.0

    # Baseline features
    df["ret_5"] = df["ret"].rolling(5).sum()
    df["ret_20"] = df["ret"].rolling(20).sum()
    df["vol_20"] = df["ret"].rolling(20).std()
    # RSI 14
    delta = df["ret"]
    up = delta.where(delta > 0, 0.0)
    down = (-delta).where(delta < 0, 0.0)
    avg_up = up.rolling(14).mean()
    avg_dn = down.rolling(14).mean()
    rs = avg_up / (avg_dn + 1e-9)
    df["rsi_14"] = 100 - 100 / (1 + rs)
    df["log_volume_20"] = np.log(df["volume"].rolling(20).mean() + 1)

    # Context: institutional foreign buy zscore (20d)
    if not inst.empty:
        inst["date"] = pd.to_datetime(inst["date"])
        inst = inst.set_index("date").sort_index()
        df = df.join(inst[["foreign_buy"]], how="left").fillna({"foreign_buy": 0})
        df["fb_5d"] = df["foreign_buy"].rolling(5).sum()
        rolling_mean = df["fb_5d"].rolling(20).mean()
        rolling_std = df["fb_5d"].rolling(20).std() + 1e-9
        df["fb_zscore"] = (df["fb_5d"] - rolling_mean) / rolling_std
    else:
        df["fb_zscore"] = 0.0

    # Target: 未來 5 日報酬
    df["target"] = df["close_price"].pct_change(5).shift(-5)

    return df


def load_macro() -> pd.DataFrame:
    conn = sqlite3.connect(DB)
    df = pd.read_sql(
        f"SELECT date, indicator, value FROM macro_indicators WHERE indicator='T10Y3M' AND date BETWEEN '{IS_START}' AND '{OOS_END}'",
        conn)
    rg = pd.read_sql(
        f"SELECT date, regime FROM regime_history WHERE date BETWEEN '{IS_START}' AND '{OOS_END}'",
        conn)
    conn.close()
    df["date"] = pd.to_datetime(df["date"])
    df = df.pivot(index="date", columns="indicator", values="value")
    if not rg.empty:
        rg["date"] = pd.to_datetime(rg["date"])
        rg = rg.set_index("date")
        rg["regime_panic"] = (rg["regime"] == "panic").astype(int)
        rg["regime_stable"] = (rg["regime"] == "stable").astype(int)
        df = df.join(rg[["regime_panic", "regime_stable"]], how="outer")
    df = df.ffill().fillna(0.0)
    return df


def evaluate(model, X_train, y_train, X_test, y_test):
    model.fit(X_train, y_train)
    pred = model.predict(X_test)
    if y_test.std() < 1e-10 or pd.Series(pred).std() < 1e-10:
        ic = 0.0
    else:
        ic = float(spearmanr(pred, y_test).correlation or 0)
    ss_res = float(np.sum((y_test - pred) ** 2))
    ss_tot = float(np.sum((y_test - y_test.mean()) ** 2)) + 1e-12
    r2 = 1 - ss_res / ss_tot
    return {"r2": r2, "ic": ic, "n_test": int(len(y_test))}


def cross_section_long_short_sharpe(predictions_panel: pd.DataFrame, returns_panel: pd.DataFrame, q=5) -> dict:
    """每日截面分位 long-short。"""
    f, r = predictions_panel.align(returns_panel, join="inner")
    ls = []
    for d in f.index:
        x = f.loc[d].dropna()
        y = r.loc[d].reindex(x.index).dropna()
        x = x.reindex(y.index)
        if len(x) < 10:
            continue
        q_lo, q_hi = x.quantile(1 / q), x.quantile(1 - 1 / q)
        ls.append(float(y[x >= q_hi].mean() - y[x <= q_lo].mean()))
    if not ls:
        return {"sharpe": float("nan"), "n_days": 0}
    arr = np.array(ls)
    ann_ret = arr.mean() * 252 / 5
    ann_vol = arr.std() * np.sqrt(252 / 5)
    return {
        "sharpe": float(ann_ret / ann_vol) if ann_vol > 1e-9 else 0.0,
        "ann_ret": float(ann_ret),
        "ann_vol": float(ann_vol),
        "n_days": int(len(arr)),
    }


def main():
    print("=" * 70)
    print("Exp 07: Context Features (LLM-free / no-adversarial proxy)")
    print("=" * 70)

    pool = load_top_stocks()
    print(f"\nPool ({len(pool)}): {pool[:8]} ...")

    macro = load_macro()
    print(f"Macro features columns: {list(macro.columns)}")

    # 收集所有股的特徵 panel
    base_features = ["ret_5", "ret_20", "vol_20", "rsi_14", "log_volume_20"]
    context_features = ["fb_zscore"] + list(macro.columns)
    all_features = base_features + context_features

    rows = []
    for sid in pool:
        try:
            df = build_features(sid)
            df = df.join(macro, how="left").fillna(0.0)
            df["stock_id"] = sid
            rows.append(df.dropna(subset=["target"] + base_features))
        except Exception as e:
            continue
    big = pd.concat(rows)
    print(f"Feature panel rows: {len(big)}")

    is_mask = (big.index >= IS_START) & (big.index <= IS_END)
    oos_mask = (big.index >= OOS_START) & (big.index <= OOS_END)
    train = big.loc[is_mask].dropna(subset=all_features + ["target"])
    test = big.loc[oos_mask].dropna(subset=all_features + ["target"])
    print(f"Train rows: {len(train)}, Test rows: {len(test)}")

    # Model A: baseline only
    model_a = GradientBoostingRegressor(n_estimators=200, max_depth=3, learning_rate=0.05, random_state=42)
    perf_a = evaluate(model_a, train[base_features], train["target"], test[base_features], test["target"])
    print(f"\n[A] Baseline only:    R²={perf_a['r2']:.4f}, IC={perf_a['ic']:.4f}")

    # Model B: + context
    model_b = GradientBoostingRegressor(n_estimators=200, max_depth=3, learning_rate=0.05, random_state=42)
    perf_b = evaluate(model_b, train[all_features], train["target"], test[all_features], test["target"])
    print(f"[B] + Context:        R²={perf_b['r2']:.4f}, IC={perf_b['ic']:.4f}")

    # Long-short evaluation
    test_a = test.copy()
    test_a["pred_a"] = model_a.predict(test_a[base_features])
    test_a["pred_b"] = model_b.predict(test_a[all_features])

    pred_a_panel = test_a.reset_index().pivot(index="date", columns="stock_id", values="pred_a")
    pred_b_panel = test_a.reset_index().pivot(index="date", columns="stock_id", values="pred_b")
    ret_panel = test_a.reset_index().pivot(index="date", columns="stock_id", values="target")

    ls_a = cross_section_long_short_sharpe(pred_a_panel, ret_panel, q=5)
    ls_b = cross_section_long_short_sharpe(pred_b_panel, ret_panel, q=5)
    print(f"\n[A] OOS long-short Sharpe: {ls_a['sharpe']:.3f}")
    print(f"[B] OOS long-short Sharpe: {ls_b['sharpe']:.3f}")

    # Feature importance for B
    fi = pd.Series(model_b.feature_importances_, index=all_features).sort_values(ascending=False)
    print(f"\nTop 5 feature importance (Model B):")
    print(fi.head(5).to_string())

    r2_gain_pct = (perf_b["r2"] - perf_a["r2"]) / abs(perf_a["r2"] + 1e-12) * 100
    sharpe_gain = ls_b["sharpe"] - ls_a["sharpe"]

    paper_compare = {
        "paper_r2_gain_pct": "+15-25% (full adversarial+context)",
        "paper_context_only_estimate": "+8-15% (without adversarial)",
        "our_r2_baseline": round(perf_a["r2"], 5),
        "our_r2_with_context": round(perf_b["r2"], 5),
        "our_r2_gain_pct": round(r2_gain_pct, 2),
        "our_ic_baseline": round(perf_a["ic"], 4),
        "our_ic_with_context": round(perf_b["ic"], 4),
        "our_sharpe_baseline": round(ls_a["sharpe"], 3),
        "our_sharpe_with_context": round(ls_b["sharpe"], 3),
        "our_sharpe_gain": round(sharpe_gain, 3),
        "verdict": (
            "[OK] context features add value" if r2_gain_pct > 5 or sharpe_gain > 0.1
            else ("[partial]" if r2_gain_pct > 0 or sharpe_gain > 0
                  else "[X] no context value detected")
        ),
        "interpretation": (
            f"加入 macro/法人/regime 特徵讓 OOS R² {'+' if r2_gain_pct > 0 else ''}{r2_gain_pct:.1f}%, "
            f"long-short Sharpe 從 {ls_a['sharpe']:.2f} 變 {ls_b['sharpe']:.2f}; "
            f"距論文 +15-25% 仍有差距，可能 adversarial training 是必要的下一步"
        ),
        "top_features": fi.head(8).to_dict(),
    }
    print("\n--- vs paper ---")
    print(json.dumps(paper_compare, ensure_ascii=False, indent=2, default=float))

    OUT_JSON.write_text(json.dumps(paper_compare, ensure_ascii=False, indent=2, default=float),
                         encoding="utf-8")
    print(f"\nWrote: {OUT_JSON}")


if __name__ == "__main__":
    main()
