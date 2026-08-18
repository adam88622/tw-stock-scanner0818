"""
盤中爆量預估單元測試
跑：python -m pytest tests/test_volume_anomaly.py -v
"""
import os
import sys
import sqlite3
import pytest

# 確保可 import 專案模組
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from scanners.volume_anomaly import (  # noqa: E402
    _pct_done,
    _bayes_blend,
    _forecast_ci,
    compute_forecast,
    scan_volume_anomaly,
    INTRADAY_VOLUME_CURVE,
)


# -------- _pct_done 內插 --------

def test_pct_done_keypoints():
    # 來源：wantgoo r 陣列倒數
    # 9:00 = idx 0 → 1/14.99
    assert _pct_done(0) == pytest.approx(0.0667, abs=1e-3)
    # 9:30 = idx 30 → 1/3.99
    assert _pct_done(30) == pytest.approx(0.2506, abs=1e-3)
    # 10:00 = idx 60 → 1/2.7
    assert _pct_done(60) == pytest.approx(0.3704, abs=1e-3)
    # 13:00 = idx 240 → 1/1.17
    assert _pct_done(240) == pytest.approx(0.8547, abs=1e-3)
    # 13:30 = idx 270 → 1.000（集合競價撮合）
    assert _pct_done(270) == pytest.approx(1.0, abs=1e-6)


def test_pct_done_interpolation():
    # idx 45 對應 wantgoo r[9] = 3.18 → 1/3.18 = 0.3145（剛好在 anchor 上）
    assert _pct_done(45) == pytest.approx(0.3145, abs=1e-3)
    # idx 47 介於 45 (0.3145) 與 50 (0.3344) 之間，線性內插約 0.3225
    assert _pct_done(47) == pytest.approx(0.3225, abs=1e-3)


# -------- 集合競價平台（13:25~13:29 凍結在 0.9434，13:30 跳到 1.000） --------

def test_pct_done_close_auction_plateau():
    # 13:25 ~ 13:29（idx 265~269）量凍結，全部回 0.9434
    assert _pct_done(265) == pytest.approx(0.9434, abs=1e-4)
    assert _pct_done(267) == pytest.approx(0.9434, abs=1e-4)
    assert _pct_done(269) == pytest.approx(0.9434, abs=1e-4)


def test_pct_done_close_auction_jump():
    # 13:30（idx 270）集合競價撮合，從 0.9434 直接跳到 1.000
    assert _pct_done(270) == pytest.approx(1.000, abs=1e-6)


# -------- Bayesian Blend --------

def test_bayes_blend_early_session():
    # pct=0.02, cum=2e9, prior=1e12 → w_obs=0.10
    # obs_forecast = 2e9 / 0.02 = 1e11
    # blended = 0.10 * 1e11 + 0.90 * 1e12 = 1e10 + 9e11 = 9.1e11
    # 結果應接近 prior (1e12)，遠離 obs (1e11)
    result = _bayes_blend(2e9, 0.02, 1e12)
    assert result == pytest.approx(9.1e11, rel=1e-6)
    # 距離 prior 的差距遠小於距離 obs 的差距
    assert abs(result - 1e12) < abs(result - 1e11)


def test_bayes_blend_late_session():
    # pct=0.50, cum=5e11, prior=1e12 → w_obs=min(1.0, 0.50*5)=1.0（完全偏觀測）
    # obs_forecast = 5e11 / 0.50 = 1e12
    # blended = 1.0 * 1e12 + 0.0 * 1e12 = 1e12
    result = _bayes_blend(5e11, 0.50, 1e12)
    assert result == pytest.approx(1e12, rel=1e-6)


def test_bayes_blend_zero_pct():
    # pct=0 → return prior
    result = _bayes_blend(0.0, 0.0, 1e12)
    assert result == pytest.approx(1e12, abs=1e-6)


# -------- compute_forecast 三級燈號 --------

def test_compute_forecast_observe():
    # minute_idx=120 (11:00) → pct=0.5464 (wantgoo); cum_vol=7104, adv20=10000
    # forecast = 7104/0.5464 ≈ 13000, rvol ≈ 1.30 → OBSERVE
    r = compute_forecast('1101', '2026-05-11 11:00:00', 7104, 10000, 120)
    assert r is not None
    assert r['rvol_forecast'] == pytest.approx(1.30, abs=1e-2)
    assert r['level'] == 'OBSERVE'


def test_compute_forecast_warn():
    # minute_idx=120 → pct=0.5464; cum_vol=8742, adv20=10000
    # forecast≈16000, rvol≈1.60 → WARN
    r = compute_forecast('2330', '2026-05-11 11:00:00', 8742, 10000, 120)
    assert r is not None
    assert r['rvol_forecast'] == pytest.approx(1.60, abs=1e-2)
    assert r['level'] == 'WARN'


def test_compute_forecast_danger():
    # minute_idx=120 → pct=0.5464; cum_vol=10928, adv20=10000
    # forecast≈20000, rvol≈2.00 → DANGER
    r = compute_forecast('2454', '2026-05-11 11:00:00', 10928, 10000, 120)
    assert r is not None
    assert r['rvol_forecast'] == pytest.approx(2.00, abs=1e-2)
    assert r['level'] == 'DANGER'


def test_compute_forecast_too_small_skipped():
    # cum_vol < 1000 → 回傳 None
    r = compute_forecast('1101', '2026-05-11 11:00:00', 500, 10000, 120)
    assert r is None


# -------- scan_volume_anomaly empty case --------

def _build_inmem_db():
    """建一個 in-memory DB，schema 與 production 相符（最小版）"""
    conn = sqlite3.connect(':memory:')
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE stocks (
            stock_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            market TEXT NOT NULL
        );
        CREATE TABLE daily_prices (
            stock_id TEXT NOT NULL,
            date TEXT NOT NULL,
            open_price REAL, high_price REAL, low_price REAL, close_price REAL,
            volume INTEGER, trade_value INTEGER, change_pct REAL,
            PRIMARY KEY (stock_id, date)
        );
        CREATE TABLE intraday_snapshot (
            stock_id TEXT NOT NULL,
            snapshot_ts TIMESTAMP NOT NULL,
            cum_volume INTEGER NOT NULL,
            last_price REAL,
            PRIMARY KEY (stock_id, snapshot_ts)
        );
    """)
    return conn


def test_scan_volume_anomaly_empty():
    """空資料庫 → 回傳結構正確，stocks=[]"""
    from datetime import datetime
    conn = _build_inmem_db()
    try:
        # 強制傳入盤中時刻
        now_ts = datetime(2026, 5, 11, 11, 0, 0)
        result = scan_volume_anomaly(conn, now_ts=now_ts)
        assert 'stocks' in result
        assert result['stocks'] == []
        assert 'taiex' in result
        assert result['taiex']['level'] == 'NONE'
        assert result['minute_idx'] == 120
    finally:
        conn.close()


# -------- _forecast_ci 90% CI --------

def test_forecast_ci_zero_pct():
    low, high = _forecast_ci(1e12, 0)
    assert low == 0
    assert high == 2e12


def test_forecast_ci_full_session():
    low, high = _forecast_ci(1e12, 1.0)
    assert low == high == 1e12


def test_forecast_ci_midday():
    # pct=0.5: sigma_rel = sqrt(1) * 0.15 = 0.15, delta = 1.645*0.15*1e12 ≈ 2.47e11
    low, high = _forecast_ci(1e12, 0.5)
    assert 7e11 < low < 8e11
    assert 1.2e12 < high < 1.3e12


def test_forecast_ci_early_session_wide():
    # pct=0.05: sigma_rel = sqrt(0.95/0.05) * 0.15 ≈ 0.654, delta ≈ 1.08e12
    low, high = _forecast_ci(1e12, 0.05)
    assert low == 0  # clamped
    assert high > 2e12


def test_scan_volume_anomaly_offhours():
    """盤外 → level NONE、stocks 空、附 note"""
    from datetime import datetime
    conn = _build_inmem_db()
    try:
        now_ts = datetime(2026, 5, 11, 8, 30, 0)  # 8:30 盤前
        result = scan_volume_anomaly(conn, now_ts=now_ts)
        assert result['stocks'] == []
        assert result['taiex']['level'] == 'NONE'
        assert result.get('note') == '非盤中時段'
    finally:
        conn.close()
