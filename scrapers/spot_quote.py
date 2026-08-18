"""
現股即時報價共用供應層（TTL 快取 + 跨行程熔斷器）

背景
----
TWSE MIS（mis.twse.com.tw）對本機 IP 的封鎖是慢性狀態，不是偶發抖動：
volume_alert_worker.log 顯示每日 RemoteDisconnected 由 2026-07-22 的 215 筆
增長到 2026-07-30 的 617 筆；被封鎖時連「單一檔」請求都在 0.2 秒內被
RemoteDisconnected 秒拒。

肇因是本機同時有三個 MIS 消費者且互相疊加：
  1. realtime_worker.py       每 10 分鐘全市場 1499 檔（30 批）
  2. volume_alert_worker.py   每 5 分鐘全市場 1499 檔（30 批）
  3. scanners/futures_basis   每 20 秒 228 檔（5 批）× 每個開著的瀏覽器分頁

而原本的失敗處理是「backoff 後 continue 下一批」，被封鎖時會把 30 批全部走完
（每批 sleep 30s → 單輪 15 分鐘純敲門），等於持續替封鎖續命，永遠等不到冷卻。

本模組的職責
------------
1. **熔斷器**：連續失敗達門檻即開路，冷卻期內「一個請求都不發」，讓對端封鎖
   有機會過期。狀態寫檔共享，跨 Flask / 兩支 worker 三個行程一致生效。
2. **TTL 快取 + single-flight**：同一批報價在 TTL 內只打一次 MIS，N 個瀏覽器
   分頁併發也只會觸發一次抓取。
3. **誠實的 meta**：回傳資料來源、抓取時間、熔斷狀態，讓呼叫端與前端能如實
   標示「這筆現價到底是幾點的」，而不是拿舊價冒充即時。

本模組只負責「取得現股即時價」，不負責寫 DB；DB 收盤價的 fallback 由呼叫端
自行決定並標記 stale。
"""
import json
import logging
import os
import threading
import time
from datetime import datetime

import requests

from config import REQUEST_HEADERS, REQUEST_TIMEOUT

logger = logging.getLogger(__name__)

MIS_URL = 'https://mis.twse.com.tw/stock/api/getStockInfo.jsp'
BATCH_SIZE = 50
BATCH_SLEEP = 0.5

# ── 熔斷器參數 ────────────────────────────────────────────────
FAIL_THRESHOLD = 3      # 連續失敗幾批就開路
COOLDOWN_SEC = 300      # 開路後靜默秒數（5 分鐘，給對端封鎖冷卻時間）
PROBE_BATCH = 1         # 冷卻結束後先試打幾批（半開狀態），成功才恢復全量

# ── TTL 快取參數 ──────────────────────────────────────────────
DEFAULT_TTL = 20        # 秒；前端 20 秒輪詢一次，TTL 對齊避免加倍打

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_BREAKER_FILE = os.path.join(_BASE_DIR, 'log', 'mis_breaker.json')

_cache_lock = threading.Lock()
_fetch_lock = threading.Lock()
_cache = {
    'prices': {},        # stock_id -> float
    'fetched_at': None,  # datetime
    'source': None,      # 'mis' | None
}


# ── 熔斷器狀態（檔案共享，跨行程）────────────────────────────

def _read_breaker():
    """讀熔斷器狀態。檔案不存在/損毀一律視為關路（正常可打）。"""
    try:
        with open(_BREAKER_FILE, encoding='utf-8') as f:
            st = json.load(f)
        return {
            'consecutive_fail': int(st.get('consecutive_fail') or 0),
            'open_until': float(st.get('open_until') or 0),
            'last_error': st.get('last_error'),
            'opened_at': st.get('opened_at'),
        }
    except Exception:
        return {'consecutive_fail': 0, 'open_until': 0.0,
                'last_error': None, 'opened_at': None}


def _write_breaker(st):
    """寫熔斷器狀態。先寫暫存檔再 replace，避免其他行程讀到半截 JSON。"""
    try:
        os.makedirs(os.path.dirname(_BREAKER_FILE), exist_ok=True)
        st = dict(st)
        st['updated_at'] = datetime.now().isoformat(timespec='seconds')
        tmp = _BREAKER_FILE + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(st, f, ensure_ascii=False)
        os.replace(tmp, _BREAKER_FILE)
    except Exception as e:
        logger.debug(f"熔斷器狀態寫入失敗（不影響取價）: {e}")


def circuit_open():
    """目前是否處於熔斷開路（禁止對 MIS 發任何請求）。"""
    return _read_breaker()['open_until'] > time.time()


def circuit_state():
    """回傳熔斷器狀態供 UI/log 顯示。"""
    st = _read_breaker()
    now = time.time()
    is_open = st['open_until'] > now
    return {
        'open': is_open,
        'consecutive_fail': st['consecutive_fail'],
        'cooldown_remain_sec': int(st['open_until'] - now) if is_open else 0,
        'opened_at': st['opened_at'],
        'last_error': st['last_error'],
    }


def report_success():
    """任一批成功 → 清空失敗計數並關路。"""
    st = _read_breaker()
    if st['consecutive_fail'] or st['open_until']:
        _write_breaker({'consecutive_fail': 0, 'open_until': 0.0,
                        'last_error': None, 'opened_at': None})


def report_failure(err):
    """任一批失敗 → 累計；達門檻即開路靜默 COOLDOWN_SEC。

    回傳 True 表示「此刻已開路，呼叫端應立即中止本輪剩餘批次」。
    """
    st = _read_breaker()
    st['consecutive_fail'] += 1
    st['last_error'] = str(err)[:200]
    if st['consecutive_fail'] >= FAIL_THRESHOLD:
        st['open_until'] = time.time() + COOLDOWN_SEC
        st['opened_at'] = datetime.now().isoformat(timespec='seconds')
        _write_breaker(st)
        logger.warning(
            f"MIS 熔斷開路：連續 {st['consecutive_fail']} 批失敗，"
            f"靜默 {COOLDOWN_SEC}s（最後錯誤：{st['last_error']}）"
        )
        return True
    _write_breaker(st)
    return False


# ── MIS 抓取 ─────────────────────────────────────────────────

def _parse_float(val):
    if val is None or val in ('-', '', '--'):
        return None
    try:
        return float(str(val).replace(',', ''))
    except (ValueError, TypeError):
        return None


def _pick_price(item):
    """
    由 MIS 單筆取即時價。

    z（最新成交價）無值時用最佳買/賣價逼近。絕對不可 fallback 到 y（昨收）：
    漲停鎖死時無撮合 → z 空、且只有買價無賣價，取昨收會被誤當即時價，
    導致基差灌水且新鮮度被誤判。三者皆無時回 None，交由呼叫端標記。
    """
    z = _parse_float(item.get('z'))
    if z is not None:
        return z
    bid = _parse_float(item.get('b', '').split('_')[0] if item.get('b') else None)
    ask = _parse_float(item.get('a', '').split('_')[0] if item.get('a') else None)
    if bid and ask:
        return (bid + ask) / 2
    return bid or ask or None


def _fetch_mis(market_map, stock_ids):
    """
    對 MIS 批次取價。回傳 (prices, aborted)。

    aborted=True 代表中途觸發熔斷、剩餘批次未執行（呼叫端據此判斷涵蓋不全）。
    與舊版最大差異：失敗時**不再** backoff 後continue 走完全部批次，
    連續失敗達門檻立即中止整輪，避免替對端封鎖續命。
    """
    prices = {}
    ids = list(stock_ids)
    if not ids:
        return prices, False

    session = requests.Session()
    session.headers.update(REQUEST_HEADERS)
    try:
        session.get('https://mis.twse.com.tw/stock/index.jsp', timeout=10)
    except Exception as e:
        # 首頁都連不上 → 直接記一次失敗，不必再走批次
        if report_failure(e):
            return prices, True

    n_batches = (len(ids) + BATCH_SIZE - 1) // BATCH_SIZE
    for bi in range(n_batches):
        batch = ids[bi * BATCH_SIZE:(bi + 1) * BATCH_SIZE]
        parts = []
        for sid in batch:
            prefix = 'tse' if market_map.get(sid, 'twse') == 'twse' else 'otc'
            parts.append(f'{prefix}_{sid}.tw')
        try:
            resp = session.get(
                MIS_URL, params={'ex_ch': '|'.join(parts), 'json': 1, 'delay': 0},
                timeout=REQUEST_TIMEOUT,
            )
            resp.raise_for_status()
            for item in resp.json().get('msgArray', []):
                sid = item.get('c', '')
                px = _pick_price(item)
                if sid and px is not None:
                    prices[sid] = px
            report_success()
        except Exception as e:
            logger.warning(f"MIS 批次 {bi + 1}/{n_batches} 失敗: {e}")
            if report_failure(e):
                return prices, True   # 熔斷 → 立刻收手
            continue
        if bi < n_batches - 1:
            time.sleep(BATCH_SLEEP)
    return prices, False


# ── 對外主介面 ───────────────────────────────────────────────

def get_spot_prices(conn, stock_ids, ttl=DEFAULT_TTL):
    """
    取現股即時價。回傳 (prices, meta)。

    prices: {stock_id: float}，只含真正抓到的即時價（拿不到的不放入，
            呼叫端須自行 fallback DB 收盤並標記 stale，不可靜默當即時）。
    meta:   {source, fetched_at, age_sec, covered, requested, circuit, cache_hit,
             partial}
            source ∈ 'mis'（本次實抓）/ 'cache'（TTL 內重用）/ 'none'（熔斷或全失敗）

    行為：
      TTL 內 → 直接回快取，不打 MIS。
      熔斷開路 → 一個請求都不發；若快取還有東西就回快取（附實際時間），
                 否則回空，讓呼叫端全部走 DB fallback。
    """
    ids = sorted(set(stock_ids or []))
    requested = len(ids)
    if not requested:
        return {}, {'source': 'none', 'fetched_at': None, 'age_sec': None,
                    'covered': 0, 'requested': 0, 'circuit': circuit_state(),
                    'cache_hit': False, 'partial': False}

    def _meta(source, cache_hit, partial):
        with _cache_lock:
            fetched_at = _cache['fetched_at']
            covered = sum(1 for s in ids if s in _cache['prices'])
        return {
            'source': source,
            'fetched_at': fetched_at.strftime('%Y-%m-%d %H:%M:%S') if fetched_at else None,
            'age_sec': round(time.time() - fetched_at.timestamp(), 1) if fetched_at else None,
            'covered': covered,
            'requested': requested,
            'circuit': circuit_state(),
            'cache_hit': cache_hit,
            'partial': partial,
        }

    def _snapshot():
        with _cache_lock:
            return {s: _cache['prices'][s] for s in ids if s in _cache['prices']}

    # 1) TTL 內直接回快取
    with _cache_lock:
        fetched_at = _cache['fetched_at']
    if fetched_at and (time.time() - fetched_at.timestamp()) < ttl:
        return _snapshot(), _meta('cache', True, False)

    # 2) single-flight：併發請求只讓一個實際去抓，其餘等它抓完用結果
    if not _fetch_lock.acquire(timeout=REQUEST_TIMEOUT + 5):
        return _snapshot(), _meta('cache', True, False)
    try:
        # 進鎖後重驗 TTL（等鎖期間可能已被別人更新）
        with _cache_lock:
            fetched_at = _cache['fetched_at']
        if fetched_at and (time.time() - fetched_at.timestamp()) < ttl:
            return _snapshot(), _meta('cache', True, False)

        # 3) 熔斷中 → 不發任何請求
        if circuit_open():
            return _snapshot(), _meta('none' if not fetched_at else 'cache',
                                      bool(fetched_at), True)

        market_map = {
            r['stock_id']: r['market']
            for r in conn.execute(
                f"SELECT stock_id, market FROM stocks "
                f"WHERE stock_id IN ({','.join('?' * len(ids))})", ids
            ).fetchall()
        }
        prices, aborted = _fetch_mis(market_map, ids)
        if prices:
            with _cache_lock:
                _cache['prices'].update(prices)
                _cache['fetched_at'] = datetime.now()
                _cache['source'] = 'mis'
            return _snapshot(), _meta('mis', False, aborted)
        # 全軍覆沒：不更新 fetched_at，保留舊快取時間戳（誠實反映資料多舊）
        return _snapshot(), _meta('none' if not fetched_at else 'cache',
                                  bool(fetched_at), True)
    finally:
        _fetch_lock.release()
