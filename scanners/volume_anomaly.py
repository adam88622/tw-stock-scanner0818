"""
盤中爆量預估 scanner
- 用 J 型曲線估算當下完成度
- 由累計成交量推算當日 EOD 預估量
- 與 ADV20 相對量能比較，分四級燈號
"""
import logging
import math
import os
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


def _force_intraday() -> bool:
    return os.environ.get('VOLUME_ALERT_FORCE_INTRADAY', '').strip() not in ('', '0', 'false', 'False')

# KGI portal 即時行情 endpoint（盤中加權指數 1mK）
_KGI_PORTAL_BASE = 'http://127.0.0.1:8890'
_KGI_TAIEX_LOGGED_FIELDS = False  # 只在首次拿到時 log raw fields，後續不再 spam


def _fetch_tse001_from_portal():
    """從 KGI portal 拿 TSE001 即時 1mK；失敗回 None（不拋例外）。"""
    try:
        import requests
        r = requests.get(f'{_KGI_PORTAL_BASE}/api/quote/kbar/TSE001', timeout=3)
        if r.status_code == 200:
            return r.json()
    except Exception:
        pass
    return None


def _forecast_ci(forecast_eod: float, pct_done: float, base_noise: float = 0.15) -> tuple:
    """
    90% CI for EOD forecast based on remaining session.
    σ_relative = sqrt((1 - pct_done) / pct_done) * base_noise
    Returns (low, high) as 1.645σ bounds (clamped to non-negative).
    """
    if pct_done <= 0:
        return (0.0, forecast_eod * 2)
    if pct_done >= 1.0:
        return (forecast_eod, forecast_eod)
    sigma_rel = math.sqrt((1 - pct_done) / pct_done) * base_noise
    delta = 1.645 * sigma_rel * forecast_eod
    return (max(0.0, forecast_eod - delta), forecast_eod + delta)


# 盤中量能累積占比曲線（源自 wantgoo.com 公開 JS，54 點 × 5 分鐘）
# key = 從 9:00 起算的分鐘數，value = 該時刻典型累積占比
# 來源：wantgoo r 陣列倒數，由歷史多年數據回歸而得，比手工 J 型曲線準
# 註：13:25~13:29 保留集合競價凍結平台（wantgoo 沒處理，我們疊上去）
INTRADAY_VOLUME_CURVE = {
    0:   0.0667,  # 9:00 (1/14.99)
    5:   0.1055,
    10:  0.1404,
    15:  0.1715,
    20:  0.2004,
    25:  0.2262,
    30:  0.2506,  # 9:30
    35:  0.2732,
    40:  0.2950,
    45:  0.3145,
    50:  0.3344,
    55:  0.3534,
    60:  0.3704,  # 10:00
    65:  0.3876,
    70:  0.4032,
    75:  0.4184,
    80:  0.4348,
    85:  0.4484,
    90:  0.4651,
    95:  0.4785,
    100: 0.4926,
    105: 0.5076,
    110: 0.5208,
    115: 0.5348,
    120: 0.5464,  # 11:00
    125: 0.5587,
    130: 0.5747,
    135: 0.5848,
    140: 0.5988,
    145: 0.6135,
    150: 0.6250,
    155: 0.6369,
    160: 0.6494,
    165: 0.6623,
    170: 0.6757,
    175: 0.6849,
    180: 0.6993,  # 12:00
    185: 0.7092,
    190: 0.7246,
    195: 0.7353,
    200: 0.7463,
    205: 0.7576,
    210: 0.7692,
    215: 0.7813,
    220: 0.8000,
    225: 0.8130,
    230: 0.8264,
    235: 0.8403,
    240: 0.8547,  # 13:00
    245: 0.8772,
    250: 0.8929,
    255: 0.9174,
    260: 0.9434,  # 13:20 — wantgoo 最後連續競價節點
    # 集合競價凍結平台（我們加的，wantgoo 線性內插到 1.0 處理失真）
    264: 0.9434,
    265: 0.9434,  # 13:25 集合競價開始（量凍結）
    269: 0.9434,  # 13:29 撮合前一刻
    270: 1.0000,  # 13:30 集合競價撮合（5.66% 突發）
}

# 燈號門檻
LEVEL_OBSERVE = 1.2
LEVEL_WARN = 1.5
LEVEL_DANGER = 1.8

# 過濾條件：樣本太小不可靠
MIN_CUM_VOL = 1000
MIN_PCT_DONE = 0.02  # 9:00 開盤即啟用（早盤雜訊大，注意）


def _pct_done(minute_idx: int) -> float:
    """
    線性內插盤中時間 → 累積占比。
    minute_idx: 從 9:00 起算分鐘數（int）。
    超出 [0, 269] 範圍時夾邊界值。
    """
    keys = sorted(INTRADAY_VOLUME_CURVE.keys())
    if minute_idx <= keys[0]:
        return INTRADAY_VOLUME_CURVE[keys[0]]
    if minute_idx >= keys[-1]:
        return INTRADAY_VOLUME_CURVE[keys[-1]]
    # 找到包夾的兩個 key 做線性內插
    for i in range(len(keys) - 1):
        lo, hi = keys[i], keys[i + 1]
        if lo <= minute_idx <= hi:
            v_lo = INTRADAY_VOLUME_CURVE[lo]
            v_hi = INTRADAY_VOLUME_CURVE[hi]
            ratio = (minute_idx - lo) / (hi - lo)
            return v_lo + (v_hi - v_lo) * ratio
    return 1.0


def _bayes_blend(cum_value: float, pct_done: float, prior_eod: float, k: float = 5.0) -> float:
    """
    Bayesian blend：早盤偏 prior、晚盤偏觀測。
    w_obs = min(1.0, pct_done * k)
    Returns blended EOD forecast.
    """
    if pct_done <= 0 or prior_eod <= 0:
        return cum_value / pct_done if pct_done > 0 else prior_eod
    obs_forecast = cum_value / pct_done
    w_obs = min(1.0, pct_done * k)
    return w_obs * obs_forecast + (1.0 - w_obs) * prior_eod


def _classify_level(rvol_forecast: float) -> str:
    """rvol_forecast → 燈號"""
    if rvol_forecast >= LEVEL_DANGER:
        return 'DANGER'
    if rvol_forecast >= LEVEL_WARN:
        return 'WARN'
    if rvol_forecast >= LEVEL_OBSERVE:
        return 'OBSERVE'
    return 'NONE'


def _current_minute_idx(ts=None) -> int:
    """目前時刻換算為從 9:00 起算分鐘數（Asia/Taipei，假設機器本地時間就是 TW）"""
    now = ts or datetime.now()
    idx = (now.hour - 9) * 60 + now.minute
    # 強制盤中模式：盤前夾到 0（=9:00），盤後夾到 270（=13:30）
    if _force_intraday():
        return max(0, min(270, idx))
    return idx


def _is_intraday(ts=None) -> bool:
    """是否在 9:00 ~ 13:30 盤中"""
    if _force_intraday():
        return True
    now = ts or datetime.now()
    hm = now.hour * 100 + now.minute
    return 900 <= hm <= 1330


def compute_forecast(stock_id, snapshot_ts, cum_vol, adv20, minute_idx):
    """
    計算單檔爆量預估。
    回傳 dict 或 None（樣本太小時）。
    """
    pct = _pct_done(minute_idx)
    if cum_vol is None or cum_vol < MIN_CUM_VOL or pct < MIN_PCT_DONE:
        return None
    if adv20 is None or adv20 <= 0:
        return None
    forecast_eod = cum_vol / pct
    rvol = forecast_eod / adv20
    return {
        'stock_id': stock_id,
        'snapshot_ts': snapshot_ts,
        'minute_idx': minute_idx,
        'cum_vol': int(cum_vol),
        'pct_done': round(pct, 4),
        'forecast_eod_vol': round(forecast_eod, 0),
        'adv20': round(adv20, 0),
        'rvol_forecast': round(rvol, 3),
        'level': _classify_level(rvol),
    }


def _fetch_adv20_map(conn, today_str):
    """
    取每檔過去 20 個交易日 volume 均值（排除今天）。
    回傳: dict[stock_id] = adv20
    """
    rows = conn.execute("""
        SELECT stock_id, AVG(volume) AS adv20
        FROM (
            SELECT stock_id, date, volume,
                   ROW_NUMBER() OVER (PARTITION BY stock_id ORDER BY date DESC) AS rn
            FROM daily_prices
            WHERE date < ? AND volume IS NOT NULL AND volume > 0
        ) t
        WHERE rn <= 20
        GROUP BY stock_id
        HAVING COUNT(*) >= 10
    """, (today_str,)).fetchall()
    return {r['stock_id']: float(r['adv20']) for r in rows}


def _fetch_latest_snapshots(conn):
    """
    取每檔今日最新一筆 intraday_snapshot。
    回傳: list of (stock_id, snapshot_ts, cum_volume, last_price)
    """
    today_str = datetime.now().strftime('%Y-%m-%d')
    rows = conn.execute("""
        SELECT s.stock_id, s.snapshot_ts, s.cum_volume, s.last_price
        FROM intraday_snapshot s
        INNER JOIN (
            SELECT stock_id, MAX(snapshot_ts) AS max_ts
            FROM intraday_snapshot
            WHERE snapshot_ts >= ?
            GROUP BY stock_id
        ) m ON m.stock_id = s.stock_id AND m.max_ts = s.snapshot_ts
    """, (today_str + ' 00:00:00',)).fetchall()
    return rows


def _extract_taiex_today_value(portal_payload):
    """
    從 KGI portal /api/quote/kbar/TSE001 回傳體抓出「今日累計成交值」（元）。

    portal_payload 格式：
        {'received_at': ISO timestamp, 'raw': {<kbar 欄位>}}

    kgisuperpy KBar_Stock_v0 schema（含繼承）有以下 property：
        symbol, datetime, delay_time, timeframe,
        open, high, low, close, volume, average,
        exchange, avg_price, total_amount
    其中 total_amount 為「今日累計成交金額（元）」— 這正是我們要的。
    （若版本回傳的是 Index_Stock_v0，欄位為 index_value/total_qty/total_count/total_amount，也適用。）

    回傳 (today_value, raw_dict) 或 (None, raw_dict)；無法解析時 today_value=None。
    """
    global _KGI_TAIEX_LOGGED_FIELDS
    if not isinstance(portal_payload, dict):
        return None, None
    raw = portal_payload.get('raw') or {}
    if not isinstance(raw, dict):
        return None, raw

    if not _KGI_TAIEX_LOGGED_FIELDS:
        try:
            logger.info(
                f"[TAIEX portal] first kbar received: keys={list(raw.keys())} "
                f"sample={ {k: raw.get(k) for k in list(raw.keys())[:12]} }"
            )
        except Exception:
            pass
        _KGI_TAIEX_LOGGED_FIELDS = True

    # 主要候選：累計成交金額（元）
    for key in ('total_amount', 'TotalAmount', 'Amount', 'amount',
                'cum_amount', 'CumAmount', 'Total'):
        v = raw.get(key)
        if v not in (None, '', 0):
            try:
                return float(v), raw
            except (TypeError, ValueError):
                continue
    return None, raw


def _compute_taiex(conn, snapshots, adv20_map, minute_idx):
    """
    以全市場成交值近似加權指數量能：
    sum(今日 cum_vol × last_price)  vs  過去 20 日全市場成交值均值

    優先：從 KGI portal 拿 TSE001 即時 1mK 的 total_amount（真實大盤量）。
    fallback：sum-of-stocks（保留原本邏輯）。
    """
    pct = _pct_done(minute_idx)
    if pct < MIN_PCT_DONE:
        return None

    # ── 先試 KGI portal（即時加權指數累計成交值） ─────────────
    today_value = None
    source = 'sum_of_stocks'
    portal_received_at = None
    portal_payload = _fetch_tse001_from_portal()
    if portal_payload is not None:
        portal_value, _raw = _extract_taiex_today_value(portal_payload)
        if portal_value and portal_value > 0:
            today_value = portal_value
            source = 'kgi_portal_tse001'
            portal_received_at = portal_payload.get('received_at')

    # 今日 universe：用今天 snapshot 有量的股票（決定 ADV20 baseline 的範圍）
    today_sids = [s['stock_id'] for s in snapshots if s['cum_volume'] and s['last_price']]
    if not today_sids:
        return None

    # ── fallback：sum(snapshot.cum_vol × price)（保留原本邏輯，未刪） ─
    if today_value is None:
        sum_value = 0.0
        for snap in snapshots:
            if snap['cum_volume'] and snap['last_price']:
                sum_value += float(snap['cum_volume']) * 1000.0 * float(snap['last_price'])
        if sum_value <= 0:
            return None
        today_value = sum_value
        source = 'sum_of_stocks'

    # ADV20 限縮在「今日有 snapshot 的同一批 stock_id」內，避免 universe 不一致
    # （早盤 9:00~9:30 還有很多冷門股沒成交，全市場 sum 會高估 baseline 約 1.5~2x）
    today_str = datetime.now().strftime('%Y-%m-%d')
    placeholders = ','.join(['?'] * len(today_sids))
    row = conn.execute(f"""
        SELECT AVG(daily_value) AS adv20_value FROM (
            SELECT date, SUM(COALESCE(trade_value, 0)) AS daily_value
            FROM daily_prices
            WHERE date < ? AND stock_id IN ({placeholders})
            GROUP BY date
            ORDER BY date DESC
            LIMIT 20
        )
    """, [today_str] + today_sids).fetchone()
    adv20_value = float(row['adv20_value']) if row and row['adv20_value'] else 0.0
    if adv20_value <= 0:
        return None

    # Bayesian blend：早盤偏 prior（ADV20）、晚盤偏觀測值
    forecast_eod_value = _bayes_blend(today_value, pct, adv20_value)
    rvol = forecast_eod_value / adv20_value
    ci_low, ci_high = _forecast_ci(forecast_eod_value, pct)
    return {
        'forecast_eod_value': round(forecast_eod_value, 0),
        'adv20_value': round(adv20_value, 0),
        'rvol_forecast': round(rvol, 3),
        'level': _classify_level(rvol),
        'ci_low': round(ci_low, 0),
        'ci_high': round(ci_high, 0),
        'source': source,  # 'kgi_portal_tse001' or 'sum_of_stocks'
        'source_received_at': portal_received_at,
    }


def scan_volume_anomaly(conn, top_n=50, now_ts=None):
    """
    主掃描函式。
    回傳 dict（盤前/盤後也回傳結構，但 level=NONE、stocks=[]）。
    """
    now = now_ts or datetime.now()
    as_of = now.strftime('%Y-%m-%d %H:%M:%S')
    minute_idx = _current_minute_idx(now)

    if not _is_intraday(now):
        return {
            'as_of': as_of,
            'minute_idx': minute_idx,
            'pct_done': 0.0,
            'stocks': [],
            'taiex': {
                'forecast_eod_value': 0,
                'adv20_value': 0,
                'rvol_forecast': 0.0,
                'level': 'NONE',
                'ci_low': 0,
                'ci_high': 0,
            },
            'note': '非盤中時段',
        }

    pct = _pct_done(minute_idx)

    today_str = now.strftime('%Y-%m-%d')
    adv20_map = _fetch_adv20_map(conn, today_str)
    snapshots = _fetch_latest_snapshots(conn)

    # 對每檔算 forecast
    results = []
    name_rows = conn.execute("SELECT stock_id, name, market FROM stocks").fetchall()
    name_map = {r['stock_id']: (r['name'], r['market']) for r in name_rows}

    for snap in snapshots:
        sid = snap['stock_id']
        if sid not in adv20_map:
            continue
        fc = compute_forecast(
            sid,
            snap['snapshot_ts'],
            snap['cum_volume'],
            adv20_map[sid],
            minute_idx,
        )
        if fc is None:
            continue
        if fc['level'] == 'NONE':
            continue
        name, market = name_map.get(sid, ('', ''))
        fc['name'] = name
        fc['market'] = market
        fc['last_price'] = snap['last_price']
        results.append(fc)

    # 依 rvol_forecast 排序
    results.sort(key=lambda x: x['rvol_forecast'], reverse=True)
    results = results[:top_n]

    taiex = _compute_taiex(conn, snapshots, adv20_map, minute_idx) or {
        'forecast_eod_value': 0,
        'adv20_value': 0,
        'rvol_forecast': 0.0,
        'level': 'NONE',
        'ci_low': 0,
        'ci_high': 0,
    }

    return {
        'as_of': as_of,
        'minute_idx': minute_idx,
        'pct_done': round(pct, 4),
        'stocks': results,
        'taiex': taiex,
    }
