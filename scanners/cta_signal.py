"""
SPY CTA Signal scanner — Lasso 中頻日 K 策略

方法:借用 MXF_LINEAR_CTA_DAY_V1 的線性 CTA 設計,移植到 SPY 日 K
- yfinance 抓 SPY 日 K(同信用利差頁的資料管線)
- 14 個特徵(滯後報酬、動能、波動率比、bar 結構、量能,週期日 K 化)
- Rolling Lasso(3 年訓練窗)預測下 20 日累積報酬 → 中頻訊號
- threshold ±0.025、SMA200 regime filter、雙邊交易
- T+1 進場、5bps 單邊成本、vol targeting 0.16

訊號歷史寫入 DB(cta_signal_history),路由(/credit-spread)從 DB 讀取。
"""

from __future__ import annotations

import logging
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf
from sklearn.linear_model import Lasso

logger = logging.getLogger(__name__)
warnings.filterwarnings("ignore")

# ── 中頻參數(於 sandbox 已 tune)──
TICKER = "SPY"
START = "1995-01-01"

LASSO_ALPHA = 0.001
THRESHOLD = 0.025
VOL_TARGET = 0.16
VOL_LOOKBACK = 90
TRAIN_WINDOW = 756           # 3 年
FORWARD = 20                 # 預測下 20 日累積報酬
RETURN_LOOKBACKS = [5, 10, 20, 60, 120]
MOMENTUM_LOOKBACKS = [20, 60, 120, 250]
VOL_RATIO_SHORT = 20
VOL_RATIO_LONG = 60
VOL_MA_WINDOW = 60

USE_REGIME_FILTER = True
SMA_REGIME = 200
USE_SHORT = True
COST_BPS = 5.0

# Lasso signal 檔案 cache(避免每次 daily_check 都要 2 分鐘重訓)
CACHE_DIR = Path(__file__).resolve().parent.parent / "db" / "cta_cache"


def _signal_cache_path() -> Path:
    import hashlib
    key = (
        f"{TICKER}|{START}|alpha={LASSO_ALPHA}|win={TRAIN_WINDOW}|fwd={FORWARD}"
        f"|ret={RETURN_LOOKBACKS}|mom={MOMENTUM_LOOKBACKS}"
        f"|vr={VOL_RATIO_SHORT}_{VOL_RATIO_LONG}|vm={VOL_MA_WINDOW}"
    )
    h = hashlib.md5(key.encode()).hexdigest()[:10]
    safe = TICKER.replace('^', '').replace('=', '_')
    return CACHE_DIR / f"signal_{safe}_{h}.csv"


# ── Data ─────────────────────────────────────────────────
def _yf_download(start_date) -> pd.DataFrame:
    df = yf.download(TICKER, start=start_date, progress=False, auto_adjust=True)
    if df is None or len(df) == 0:
        return pd.DataFrame()
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df.rename(columns={"Open": "open", "High": "high", "Low": "low",
                             "Close": "close", "Volume": "volume"})
    df = df[["open", "high", "low", "close", "volume"]].dropna()
    df.index = pd.to_datetime(df.index).tz_localize(None)
    return df


def fetch_bars() -> pd.DataFrame:
    """yfinance + 本地 CSV cache(增量更新)。"""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path = CACHE_DIR / f"{TICKER.replace('^','').replace('=','_')}_daily.csv"
    if cache_path.exists():
        cached = pd.read_csv(cache_path, index_col=0, parse_dates=True)
        last_date = cached.index[-1]
        today = pd.Timestamp.now().normalize()
        if last_date >= today - pd.Timedelta(days=1):
            return cached
        incr_start = (last_date + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
        new = _yf_download(incr_start)
        if len(new) == 0:
            return cached
        combined = pd.concat([cached, new[~new.index.isin(cached.index)]]).sort_index()
        combined.to_csv(cache_path)
        return combined
    df = _yf_download(START)
    if len(df) == 0:
        raise RuntimeError(f"yfinance got nothing for {TICKER}")
    df.to_csv(cache_path)
    return df


# ── Features ─────────────────────────────────────────────
def build_features(bars: pd.DataFrame) -> pd.DataFrame:
    c = bars["close"].astype(float)
    h = bars["high"].astype(float)
    l = bars["low"].astype(float)
    v = bars["volume"].astype(float)
    o = bars["open"].astype(float)
    ret = c.pct_change()
    f = pd.DataFrame(index=bars.index)
    for lb in RETURN_LOOKBACKS:
        f[f"ret_{lb}"] = c.pct_change(lb)
    for lb in MOMENTUM_LOOKBACKS:
        ma = c.rolling(lb).mean()
        f[f"mom_{lb}"] = (c - ma) / ma
    f["vol_ratio"] = ret.rolling(VOL_RATIO_SHORT).std() / ret.rolling(VOL_RATIO_LONG).std().replace(0, np.nan)
    f["bar_range"] = (h - l) / c
    f["upper_shadow"] = (h - np.maximum(o, c)) / c
    f["lower_shadow"] = (np.minimum(o, c) - l) / c
    f["vol_ma_ratio"] = v / v.rolling(VOL_MA_WINDOW).mean().replace(0, np.nan)
    return f.replace([np.inf, -np.inf], np.nan)


# ── Lasso ────────────────────────────────────────────────
def rolling_lasso(bars: pd.DataFrame, feats: pd.DataFrame) -> pd.Series:
    c = bars["close"].astype(float)
    fwd = (c.shift(-FORWARD) / c - 1)
    aligned_train = feats.join(fwd.rename("target")).dropna()
    X_train_all = aligned_train.drop(columns=["target"])
    y_train_all = aligned_train["target"]
    X_full = feats.dropna()
    signal = pd.Series(0.0, index=bars.index)
    model = Lasso(alpha=LASSO_ALPHA, max_iter=5000, warm_start=True)
    mu = sd = None
    for i in range(TRAIN_WINDOW, len(X_train_all)):
        Xtr = X_train_all.iloc[i - TRAIN_WINDOW:i]
        ytr = y_train_all.iloc[i - TRAIN_WINDOW:i]
        mu, sd = Xtr.mean(), Xtr.std().replace(0, 1)
        model.fit((Xtr - mu) / sd, ytr)
        Xp = X_train_all.iloc[i:i+1]
        signal.loc[X_train_all.index[i]] = float(model.predict((Xp - mu) / sd)[0])
    if mu is not None:
        last_train_idx = X_train_all.index[-1]
        newer = X_full.loc[X_full.index > last_train_idx]
        for idx, row in newer.iterrows():
            Xp = pd.DataFrame([row])
            signal.loc[idx] = float(model.predict((Xp - mu) / sd)[0])
    return signal


def rolling_lasso_cached(bars: pd.DataFrame, feats: pd.DataFrame) -> pd.Series:
    """signal 檔案 cache(同 sandbox)。同參數 + bars 對齊就直接用。"""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    p = _signal_cache_path()
    if p.exists():
        cached = pd.read_csv(p, index_col=0, parse_dates=True).iloc[:, 0]
        if cached.index.tz is not None:
            cached.index = cached.index.tz_localize(None)
        if len(cached) == len(bars) and cached.index[-1] == bars.index[-1]:
            logger.info(f"CTA signal cache hit ({len(cached)} bars)")
            return cached
        logger.info(f"CTA signal cache stale (cache {len(cached)} vs bars {len(bars)}) — 重訓")
    logger.info(f"CTA Lasso 重訓中(rolling {TRAIN_WINDOW} window × {len(bars)} bars)...")
    signal = rolling_lasso(bars, feats)
    signal.to_frame("signal").to_csv(p)
    logger.info(f"CTA signal computed + cached -> {p.name}")
    return signal


# ── Position ─────────────────────────────────────────────
def to_position(signal: pd.Series, bars: pd.DataFrame) -> pd.DataFrame:
    close = bars["close"].astype(float)
    ret = close.pct_change()
    rv = ret.rolling(VOL_LOOKBACK).std() * np.sqrt(252)
    rv = rv.replace(0, np.nan).ffill()
    raw = pd.Series(0.0, index=signal.index)
    raw[signal > THRESHOLD] = 1.0
    raw[signal < -THRESHOLD] = -1.0
    sma = close.rolling(SMA_REGIME).mean()
    if USE_REGIME_FILTER:
        raw[(close < sma) & (raw > 0)] = 0
        raw[(close > sma) & (raw < 0)] = 0
    if not USE_SHORT:
        raw[raw < 0] = 0
    pos = raw * (VOL_TARGET / rv.reindex(signal.index).ffill()).clip(0, 3)
    return pd.DataFrame({
        "close": close, "sma200": sma,
        "signal_raw": signal, "raw_pos": raw, "position": pos,
    })


# ── Backtest & per-trade ─────────────────────────────────
def compute_backtest(df: pd.DataFrame, cost_bps: float = COST_BPS) -> dict:
    x = df.copy()
    x["ret"] = x["close"].pct_change()
    c = cost_bps / 10000.0
    pos_chg_d = x["raw_pos"].diff().abs().fillna(x["raw_pos"].abs())
    pos_chg_v = x["position"].diff().abs().fillna(x["position"].abs())
    x["strat_ret"] = x["raw_pos"].shift(1).fillna(0) * x["ret"] - pos_chg_d * c
    x["strat_volret"] = x["position"].shift(1).fillna(0) * x["ret"] - pos_chg_v * c
    x = x.loc[x["raw_pos"].shift(1).notna()].copy().dropna(subset=["ret"])
    if len(x) < 50:
        return {}
    eq_strat = (1 + x["strat_ret"]).cumprod()
    eq_vol = (1 + x["strat_volret"]).cumprod()
    eq_bh = (1 + x["ret"]).cumprod()

    def metrics(eq, daily):
        n = len(daily)
        years = n / 252.0 if n else 1.0
        total = float(eq.iloc[-1]) if len(eq) else 1.0
        cagr = total ** (1 / years) - 1 if total > 0 else -1
        vol = float(daily.std() * np.sqrt(252)) if daily.std() > 0 else 0.0
        sharpe = float(daily.mean() / daily.std() * np.sqrt(252)) if daily.std() > 0 else 0.0
        dd = (eq / eq.cummax() - 1).min()
        maxdd = float(dd) if pd.notna(dd) else 0.0
        calmar = cagr / abs(maxdd) if maxdd < 0 else 0.0
        win = float((daily > 0).sum() / (daily != 0).sum()) if (daily != 0).any() else 0.0
        return {"cagr": cagr, "vol": vol, "sharpe": sharpe, "maxdd": maxdd,
                "calmar": calmar, "win_rate": win, "days": n}

    pos = x["raw_pos"].fillna(0)
    switches = int(((pos != pos.shift(1)) & (pos != 0)).sum())
    time_in_market = float((pos != 0).sum() / len(pos))

    return {
        "strat": metrics(eq_strat, x["strat_ret"]),
        "vol":   metrics(eq_vol,   x["strat_volret"]),
        "bh":    metrics(eq_bh,    x["ret"]),
        "switches": switches,
        "time_in_market": time_in_market,
        "cost_bps": cost_bps,
        "start": str(x.index[0].date()),
        "end":   str(x.index[-1].date()),
        "curve": [{"date": pd.Timestamp(dt).strftime("%Y-%m-%d"),
                   "strat": float(eq_strat.loc[dt]),
                   "vol":   float(eq_vol.loc[dt]),
                   "bh":    float(eq_bh.loc[dt])} for dt in eq_strat.index],
    }


def compute_trades(df: pd.DataFrame, cost_bps: float = COST_BPS) -> dict:
    pos = df["raw_pos"].fillna(0).astype(int).values
    close = df["close"].values
    dates = df.index
    cc = cost_bps / 10000.0
    trades = []
    in_trade = False
    entry_i, entry_pos = 0, 0
    for i in range(len(pos)):
        if not in_trade and pos[i] != 0:
            entry_i, entry_pos = i, pos[i]; in_trade = True
        elif in_trade and pos[i] != entry_pos:
            pnl = (close[i] - close[entry_i]) / close[entry_i] * entry_pos - 2 * cc
            trades.append({"pos": entry_pos, "pnl": float(pnl), "days": int(i - entry_i)})
            in_trade = False
            if pos[i] != 0:
                entry_i, entry_pos = i, pos[i]; in_trade = True
    if not trades:
        return {"n": 0}
    pnls = [t["pnl"] for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    long_t = [t for t in trades if t["pos"] > 0]
    short_t = [t for t in trades if t["pos"] < 0]
    long_w = [t for t in long_t if t["pnl"] > 0]
    short_w = [t for t in short_t if t["pnl"] > 0]
    avg_win = float(np.mean(wins)) if wins else 0.0
    avg_loss = float(np.mean(losses)) if losses else 0.0
    payoff = abs(avg_win / avg_loss) if avg_loss else 0.0
    win_rate = len(wins) / (len(wins) + len(losses)) if (wins or losses) else 0.0
    expectancy = win_rate * avg_win + (1 - win_rate) * avg_loss
    return {
        "n": len(trades), "wins": len(wins), "losses": len(losses),
        "win_rate": win_rate, "avg_win": avg_win, "avg_loss": avg_loss,
        "payoff": payoff, "expectancy": expectancy,
        "avg_hold_days": float(np.mean([t["days"] for t in trades])),
        "best": float(max(pnls)), "worst": float(min(pnls)),
        "long_n": len(long_t), "long_win_rate": (len(long_w)/len(long_t) if long_t else 0.0),
        "short_n": len(short_t), "short_win_rate": (len(short_w)/len(short_t) if short_t else 0.0),
    }


# ── DB updater(daily_check 用)───────────────────────────
def update_cta_signal_db(conn) -> dict:
    """完整跑一次 + 將整段訊號 upsert 到 cta_signal_history。"""
    from models.database import upsert_cta_signal

    bars = fetch_bars()
    feats = build_features(bars)
    sig = rolling_lasso_cached(bars, feats)
    df = to_position(sig, bars)

    n = 0
    for dt, row in df.iterrows():
        if pd.isna(row["signal_raw"]) or pd.isna(row["raw_pos"]):
            continue
        upsert_cta_signal(
            conn,
            dt.strftime("%Y-%m-%d"),
            float(row["close"]),
            float(row["signal_raw"]),
            float(row["raw_pos"]),
            float(row["position"]) if pd.notna(row["position"]) else 0.0,
        )
        n += 1
    conn.commit()

    last = df.iloc[-1]
    pos = float(last["raw_pos"]) if pd.notna(last["raw_pos"]) else 0.0
    action = "BUY" if pos > 0 else ("SELL" if pos < 0 else "HOLD")
    logger.info(f"CTA signal: latest {action} signal {last['signal_raw']:+.6f} ({n} rows upserted)")
    return {
        "action": action,
        "signal": float(last["signal_raw"]) if pd.notna(last["signal_raw"]) else 0.0,
        "close": float(last["close"]),
        "rows_upserted": n,
    }
