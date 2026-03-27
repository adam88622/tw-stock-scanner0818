"""
大盤籌碼資料 — 從 FinMind API 抓取期貨/選擇權法人籌碼
"""
import time
import logging
import threading
import requests
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

# ===== 簡易快取（5 分鐘） =====
_cache = {}
_cache_lock = threading.Lock()
CACHE_TTL = 300  # 5 minutes


def _get_cached(key):
    with _cache_lock:
        entry = _cache.get(key)
        if entry and time.time() - entry['ts'] < CACHE_TTL:
            return entry['data']
    return None


def _set_cached(key, data):
    with _cache_lock:
        _cache[key] = {'data': data, 'ts': time.time()}


def _finmind_get(dataset, data_id, start_date, end_date):
    """呼叫 FinMind API，回傳 data list"""
    try:
        r = requests.get('https://api.finmindtrade.com/api/v4/data',
                         params={
                             'dataset': dataset,
                             'data_id': data_id,
                             'start_date': start_date,
                             'end_date': end_date,
                         },
                         headers={'User-Agent': 'Mozilla/5.0'},
                         timeout=20)
        d = r.json()
        if d.get('status') == 200 and d.get('data'):
            return d['data']
        logger.warning(f"FinMind API 回傳非預期狀態: dataset={dataset}, data_id={data_id}, status={d.get('status')}, msg={d.get('msg', '')}")
        return []
    except Exception as e:
        logger.error(f"FinMind API 請求失敗: dataset={dataset}, data_id={data_id} - {e}")
        return []


def fetch_futures_oi(days=60):
    """
    抓取台指期(TX)三大法人未平倉淨額。
    Returns list of dicts: {date, dealer_net, trust_net, foreign_net, total_net}
    """
    cached = _get_cached('futures_oi')
    if cached is not None:
        return cached

    end = datetime.now()
    start = end - timedelta(days=days + 15)
    rows = _finmind_get('TaiwanFuturesInstitutionalInvestors', 'TX',
                        start.strftime('%Y-%m-%d'), end.strftime('%Y-%m-%d'))
    if not rows:
        logger.warning("台指期未平倉資料為空")
        return []

    # Group by date
    date_map = {}
    for row in rows:
        d = row['date']
        if d not in date_map:
            date_map[d] = {'dealer_net': 0, 'trust_net': 0, 'foreign_net': 0}

        long_oi = int(row.get('long_open_interest_balance_volume', 0) or 0)
        short_oi = int(row.get('short_open_interest_balance_volume', 0) or 0)
        net = long_oi - short_oi

        inv = row.get('institutional_investors', '')
        if '自營' in inv:
            date_map[d]['dealer_net'] += net
        elif '投信' in inv:
            date_map[d]['trust_net'] += net
        elif '外資' in inv or '外資及陸資' in inv:
            date_map[d]['foreign_net'] += net

    result = []
    for d in sorted(date_map.keys()):
        entry = date_map[d]
        entry['date'] = d
        entry['total_net'] = entry['dealer_net'] + entry['trust_net'] + entry['foreign_net']
        result.append(entry)

    result = result[-days:]
    _set_cached('futures_oi', result)
    return result


def fetch_retail_ratio(days=60):
    """
    計算微台指散戶多空比 (%)。
    公式：散戶多空比 = -1 × 法人淨多空 / 全體未平倉量 × 100
    Returns list of dicts: {date, retail_ratio, institutional_net, total_oi}
    """
    cached = _get_cached('retail_ratio')
    if cached is not None:
        return cached

    end = datetime.now()
    start = end - timedelta(days=days + 15)
    start_str = start.strftime('%Y-%m-%d')
    end_str = end.strftime('%Y-%m-%d')

    # 1. 法人未平倉淨額
    inst_rows = _finmind_get('TaiwanFuturesInstitutionalInvestors', 'MTX', start_str, end_str)
    if not inst_rows:
        logger.warning("微台指法人資料為空")
        return []

    inst_map = {}
    for row in inst_rows:
        d = row['date']
        if d not in inst_map:
            inst_map[d] = 0
        long_oi = int(row.get('long_open_interest_balance_volume', 0) or 0)
        short_oi = int(row.get('short_open_interest_balance_volume', 0) or 0)
        inst_map[d] += (long_oi - short_oi)

    # 2. 全體未平倉量
    daily_rows = _finmind_get('TaiwanFuturesDaily', 'MTX', start_str, end_str)
    oi_map = {}
    for row in daily_rows:
        d = row['date']
        if row.get('trading_session') == 'position' and row.get('open_interest', 0) > 0:
            oi_map[d] = oi_map.get(d, 0) + row['open_interest']

    # 3. 計算散戶多空比
    result = []
    for d in sorted(inst_map.keys()):
        inst_net = inst_map[d]
        total_oi = oi_map.get(d, 0)
        if total_oi > 0:
            ratio = round(-1 * inst_net / total_oi * 100, 2)
        else:
            ratio = 0
        result.append({
            'date': d,
            'retail_ratio': ratio,
            'institutional_net': inst_net,
            'total_oi': total_oi,
        })

    result = result[-days:]
    _set_cached('retail_ratio', result)
    return result


def fetch_put_call_ratio(days=60):
    """
    抓取 TXO 選擇權法人資料，計算 Put/Call Ratio。
    Returns list of dicts: {date, put_oi, call_oi, pc_ratio}
    """
    cached = _get_cached('put_call_ratio')
    if cached is not None:
        return cached

    end = datetime.now()
    start = end - timedelta(days=days + 15)
    rows = _finmind_get('TaiwanOptionInstitutionalInvestors', 'TXO',
                        start.strftime('%Y-%m-%d'), end.strftime('%Y-%m-%d'))
    if not rows:
        logger.warning("TXO Put/Call Ratio 資料為空")
        return []

    # Group by date, sum put/call open interest
    date_map = {}
    for row in rows:
        d = row['date']
        if d not in date_map:
            date_map[d] = {'put_oi': 0, 'call_oi': 0}

        long_oi = int(row.get('long_open_interest_balance_volume', 0) or 0)
        short_oi = int(row.get('short_open_interest_balance_volume', 0) or 0)
        oi = long_oi + short_oi
        cp = row.get('call_put', '')
        if '賣權' in cp or 'put' in cp.lower():
            date_map[d]['put_oi'] += oi
        elif '買權' in cp or 'call' in cp.lower():
            date_map[d]['call_oi'] += oi

    result = []
    for d in sorted(date_map.keys()):
        entry = date_map[d]
        call_oi = entry['call_oi']
        put_oi = entry['put_oi']
        pc_ratio = round(put_oi / call_oi, 4) if call_oi > 0 else 0
        result.append({
            'date': d,
            'put_oi': put_oi,
            'call_oi': call_oi,
            'pc_ratio': pc_ratio,
        })

    result = result[-days:]
    _set_cached('put_call_ratio', result)
    return result
