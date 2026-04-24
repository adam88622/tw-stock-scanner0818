"""
五大宏觀指標抓取 — 降低投票系統共線性

1. 10Y-3M Treasury Spread  (FRED: T10Y3M)     — 景氣/政策
2. Financial CP-Treasury    (FRED: DCPF3M-DTB3) — 資金壓力
3. Broad Dollar Index       (FRED: DTWEXBGS)    — 全球美元
4. Implied Correlation      (Yahoo: ^KCJ)       — 系統性風險
5. MOVE Index               (Yahoo: ^MOVE)      — 國債波動/流動性

資料來源: FRED CSV (免費無需 key) + Yahoo Finance
"""

import logging
import time
import requests
from datetime import datetime, timedelta
from models.database import upsert_macro

logger = logging.getLogger(__name__)

FRED_CSV = "https://fred.stlouisfed.org/graph/fredgraph.csv"
YAHOO_CHART = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
}

# ── 各指標信號門檻 ──
THRESHOLDS = {
    'T10Y3M': {
        # 殖利率利差: >1.5 正常, 0~1.5 趨平, <0 倒掛(衰退風險)
        'bull': 1.0,   # > 1.0 → 正常
        'warn': 0.0,   # 0~1.0 → 警戒
        # < 0 → 危險
    },
    'CP_SPREAD': {
        # CP-Treasury: <0.3 正常, 0.3~0.8 緊張, >0.8 資金壓力
        'safe': 0.3,
        'danger': 0.8,
    },
    'DOLLAR': {
        # Dollar: 用 percentile rank (最近250天), >70 偏強=風險, <30 偏弱=寬鬆
        'strong': 70,
        'weak': 30,
    },
    'COR3M': {
        # VIX proxy: >30 系統性風險高, <20 正常
        'danger': 30,
        'safe': 20,
    },
    'MOVE': {
        # MOVE: >120 國債市場壓力, <80 正常
        'danger': 120,
        'safe': 80,
    },
}

# ── 各指標對應持股水位 ──
POSITION_MAP = {
    'T10Y3M': {'GREEN': 100, 'YELLOW': 60, 'RED': 20},
    'CP_SPREAD': {'GREEN': 100, 'YELLOW': 50, 'RED': 0},
    'DOLLAR': {'GREEN': 90, 'YELLOW': 60, 'RED': 30},
    'COR3M': {'GREEN': 90, 'YELLOW': 50, 'RED': 10},
    'MOVE': {'GREEN': 90, 'YELLOW': 60, 'RED': 20},
}


def _fetch_fred_csv(series_id, years=3):
    """從 FRED 抓 CSV，回傳 list of (date_str, value)。"""
    start = (datetime.now() - timedelta(days=years * 365)).strftime('%Y-%m-%d')
    params = {
        'id': series_id,
        'cosd': start,
        'fq': 'Daily',
    }
    try:
        resp = requests.get(FRED_CSV, params=params, headers=HEADERS, timeout=30)
        resp.raise_for_status()
        lines = resp.text.strip().split('\n')
        results = []
        for line in lines[1:]:  # skip header
            parts = line.split(',')
            if len(parts) >= 2 and parts[1] != '.' and parts[1] != '':
                try:
                    results.append((parts[0], float(parts[1])))
                except ValueError:
                    continue
        return results
    except Exception as e:
        logger.error(f"FRED {series_id} fetch error: {e}")
        return []


def _fetch_yahoo(symbol, days=750):
    """從 Yahoo Finance 抓歷史資料。"""
    end = int(time.time())
    start = end - days * 86400
    url = YAHOO_CHART.format(symbol=symbol)
    params = {
        'period1': start,
        'period2': end,
        'interval': '1d',
    }
    try:
        resp = requests.get(url, params=params, headers=HEADERS, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        result = data.get('chart', {}).get('result', [])
        if not result:
            return []
        ts_list = result[0].get('timestamp', [])
        closes = result[0].get('indicators', {}).get('quote', [{}])[0].get('close', [])
        out = []
        for ts, c in zip(ts_list, closes):
            if c is not None:
                d = datetime.utcfromtimestamp(ts).strftime('%Y-%m-%d')
                out.append((d, round(c, 4)))
        return out
    except Exception as e:
        logger.error(f"Yahoo {symbol} fetch error: {e}")
        return []


def _classify_t10y3m(value):
    if value > THRESHOLDS['T10Y3M']['bull']:
        return 'GREEN'
    if value > THRESHOLDS['T10Y3M']['warn']:
        return 'YELLOW'
    return 'RED'


def _classify_cp_spread(value):
    if value < THRESHOLDS['CP_SPREAD']['safe']:
        return 'GREEN'
    if value < THRESHOLDS['CP_SPREAD']['danger']:
        return 'YELLOW'
    return 'RED'


def _classify_dollar_pctile(pctile):
    """pctile = 0~100, 越高=美元越強=風險越高"""
    if pctile > THRESHOLDS['DOLLAR']['strong']:
        return 'RED'
    if pctile < THRESHOLDS['DOLLAR']['weak']:
        return 'GREEN'
    return 'YELLOW'


def _classify_cor3m(value):
    if value > THRESHOLDS['COR3M']['danger']:
        return 'RED'
    if value < THRESHOLDS['COR3M']['safe']:
        return 'GREEN'
    return 'YELLOW'


def _classify_move(value):
    if value > THRESHOLDS['MOVE']['danger']:
        return 'RED'
    if value < THRESHOLDS['MOVE']['safe']:
        return 'GREEN'
    return 'YELLOW'


def _percentile_rank(values, current):
    """計算 current 在 values 中的百分位。"""
    if not values:
        return 50.0
    below = sum(1 for v in values if v < current)
    return round(below / len(values) * 100, 1)


def update_macro_indicators(conn):
    """抓取並更新五大宏觀指標到 DB。"""
    results = {}

    # ── 1. 10Y-3M Treasury Spread ──
    logger.info("Fetching T10Y3M from FRED...")
    t10y3m = _fetch_fred_csv('T10Y3M')
    if t10y3m:
        for date, val in t10y3m:
            signal = _classify_t10y3m(val)
            upsert_macro(conn, date, 'T10Y3M', val, signal)
        latest = t10y3m[-1]
        results['T10Y3M'] = {'date': latest[0], 'value': latest[1],
                             'signal': _classify_t10y3m(latest[1])}
        logger.info(f"T10Y3M: {latest[1]} ({_classify_t10y3m(latest[1])})")

    # ── 2. Financial CP - Treasury spread ──
    logger.info("Fetching CP spread from FRED...")
    dcpf3m = _fetch_fred_csv('DCPF3M')
    dtb3 = _fetch_fred_csv('DTB3')
    if dcpf3m and dtb3:
        dtb3_map = dict(dtb3)
        for date, cp_val in dcpf3m:
            tb_val = dtb3_map.get(date)
            if tb_val is not None:
                spread = round(cp_val - tb_val, 4)
                signal = _classify_cp_spread(spread)
                upsert_macro(conn, date, 'CP_SPREAD', spread, signal)
        # latest
        for date, cp_val in reversed(dcpf3m):
            tb_val = dtb3_map.get(date)
            if tb_val is not None:
                spread = round(cp_val - tb_val, 4)
                results['CP_SPREAD'] = {'date': date, 'value': spread,
                                        'signal': _classify_cp_spread(spread)}
                logger.info(f"CP_SPREAD: {spread} ({_classify_cp_spread(spread)})")
                break

    # ── 3. Broad Dollar Index (Yahoo DXY 即時 + FRED 歷史) ──
    logger.info("Fetching Dollar Index from Yahoo (DX-Y.NYB)...")
    dollar = _fetch_yahoo('DX-Y.NYB')
    if not dollar:
        logger.info("Yahoo DXY failed, fallback to FRED DTWEXBGS...")
        dollar = _fetch_fred_csv('DTWEXBGS')
    if dollar:
        values = [v for _, v in dollar]
        for date, val in dollar:
            idx = next(i for i, (d, _) in enumerate(dollar) if d == date)
            lookback = values[max(0, idx - 250):idx + 1]
            pctile = _percentile_rank(lookback, val) if len(lookback) > 20 else 50
            signal = _classify_dollar_pctile(pctile)
            upsert_macro(conn, date, 'DOLLAR', val, signal)
        latest = dollar[-1]
        lookback_vals = values[max(0, len(values) - 250):]
        pctile = _percentile_rank(lookback_vals, latest[1])
        results['DOLLAR'] = {'date': latest[0], 'value': latest[1],
                             'pctile': pctile,
                             'signal': _classify_dollar_pctile(pctile)}
        logger.info(f"DOLLAR: {latest[1]} (pctile={pctile})")

    # ── 4. VIX 系統性風險 (Yahoo 即時 + FRED 歷史) ──
    logger.info("Fetching VIX from Yahoo (^VIX)...")
    vix_data = _fetch_yahoo('%5EVIX')
    if not vix_data:
        logger.info("Yahoo VIX failed, fallback to FRED VIXCLS...")
        vix_data = _fetch_fred_csv('VIXCLS')
    if vix_data:
        for date, val in vix_data:
            signal = _classify_cor3m(val)
            upsert_macro(conn, date, 'COR3M', val, signal)
        latest = vix_data[-1]
        results['COR3M'] = {'date': latest[0], 'value': latest[1],
                            'signal': _classify_cor3m(latest[1])}
        logger.info(f"VIX: {latest[1]} ({_classify_cor3m(latest[1])})")

    # ── 5. MOVE Index (Treasury Volatility) ──
    logger.info("Fetching MOVE from Yahoo (^MOVE)...")
    move = _fetch_yahoo('%5EMOVE')
    if move:
        for date, val in move:
            signal = _classify_move(val)
            upsert_macro(conn, date, 'MOVE', val, signal)
        latest = move[-1]
        results['MOVE'] = {'date': latest[0], 'value': latest[1],
                           'signal': _classify_move(latest[1])}
        logger.info(f"MOVE: {latest[1]} ({_classify_move(latest[1])})")

    conn.commit()
    logger.info(f"Macro indicators updated: {list(results.keys())}")
    return results


def get_all_latest(conn):
    """取得所有宏觀指標最新值。"""
    from models.database import get_macro_latest
    indicators = ['T10Y3M', 'CP_SPREAD', 'DOLLAR', 'COR3M', 'MOVE']
    result = {}
    for ind in indicators:
        row = get_macro_latest(conn, ind)
        if row:
            result[ind] = {
                'date': row['date'],
                'value': row['value'],
                'signal': row['signal'],
                'position': POSITION_MAP.get(ind, {}).get(row['signal'], 50),
            }
    return result
