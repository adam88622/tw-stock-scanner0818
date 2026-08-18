"""
盤中即時報價抓取模組
使用 mis.twse.com.tw 即時行情 API
支援上市(tse)與上櫃(otc)批次查詢
"""
import time
import logging
import requests
from datetime import datetime
from config import REQUEST_HEADERS, REQUEST_TIMEOUT

logger = logging.getLogger(__name__)

MIS_URL = 'https://mis.twse.com.tw/stock/api/getStockInfo.jsp'
BATCH_SIZE = 50  # 每次查詢最多 50 檔


def _build_query(stock_ids_with_market):
    """
    組合查詢字串。
    stock_ids_with_market: list of (stock_id, market)
    回傳: 'tse_2330.tw|tse_2317.tw|otc_6547.tw|...'
    """
    parts = []
    for sid, market in stock_ids_with_market:
        prefix = 'tse' if market == 'twse' else 'otc'
        parts.append(f'{prefix}_{sid}.tw')
    return '|'.join(parts)


def is_trading_day():
    """
    檢查今天是否為台股交易日。
    先排除週末，再用 TWSE API 確認非國定假日。
    結果快取一整天，避免重複呼叫。
    """
    today = datetime.now().strftime('%Y%m%d')

    # 快取：同一天只查一次
    if hasattr(is_trading_day, '_cache') and is_trading_day._cache[0] == today:
        return is_trading_day._cache[1]

    now = datetime.now()
    if now.weekday() >= 5:
        is_trading_day._cache = (today, False)
        return False

    # 用 TWSE 當日行情 API 確認（有資料 = 有開盤）
    try:
        r = requests.get(
            'https://www.twse.com.tw/exchangeReport/MI_INDEX',
            params={'response': 'json', 'date': today, 'type': 'IND'},
            headers=REQUEST_HEADERS, timeout=10)
        data = r.json()
        is_open = data.get('stat') == 'OK'
    except Exception:
        is_open = True  # API 失敗就假設有開盤，寧可多抓不漏抓

    is_trading_day._cache = (today, is_open)
    return is_open


def is_trading_hours():
    """檢查是否在交易時段（交易日 09:00~13:30）。
    註：VOLUME_ALERT_FORCE_INTRADAY 只影響 scanner 邏輯（讓 cache 顯示盤中介面），
    不影響此處 — 避免在真實非盤中時段對 TWSE MIS 打無效請求。
    """
    if not is_trading_day():
        return False
    now = datetime.now()
    hour_min = now.hour * 100 + now.minute
    return 855 <= hour_min <= 1335  # 08:55 ~ 13:35 (含盤前盤後緩衝)


def fetch_realtime_prices(conn, record_snapshot=False):
    """
    抓取所有股票的即時報價，更新 daily_prices。
    record_snapshot: 同時寫入 intraday_snapshot（保留歷史，供爆量預估）
    回傳: 更新筆數
    """
    from models.database import upsert_daily_price
    if record_snapshot:
        from models.database import upsert_intraday_snapshot

    if not is_trading_hours():
        logger.info("非交易時段，跳過即時報價抓取")
        return 0

    today = datetime.now().strftime('%Y-%m-%d')
    snapshot_ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S') if record_snapshot else None

    # 從 DB 取所有股票
    rows = conn.execute("SELECT stock_id, market FROM stocks ORDER BY stock_id").fetchall()
    if not rows:
        logger.warning("資料庫無股票資料，無法抓取即時報價")
        return 0

    stock_list = [(r['stock_id'], r['market']) for r in rows]
    total_updated = 0

    # 熔斷器（與 futures_basis 共用同一份檔案狀態，跨行程生效）。
    # MIS 對本機 IP 的封鎖是慢性狀態，被擋時連單檔請求都會被秒拒；
    # 開路期間直接跳過整輪，一個請求都不發，讓對端封鎖有機會冷卻。
    from scrapers.spot_quote import (
        circuit_open, circuit_state, report_failure, report_success,
    )
    if circuit_open():
        st = circuit_state()
        logger.warning(
            f"MIS 熔斷開路中（連續失敗 {st['consecutive_fail']} 批，"
            f"剩餘冷卻 {st['cooldown_remain_sec']}s），本輪跳過抓取"
        )
        return 0

    # 用 Session 重用 TCP 連線（降低 TWSE 對短連線爆量的觀感）
    session = requests.Session()
    session.headers.update(REQUEST_HEADERS)
    # 先打 MIS 首頁建立 session（拿 cookie + 觀感更像真實瀏覽器）
    try:
        session.get('https://mis.twse.com.tw/stock/index.jsp', timeout=10)
    except Exception:
        pass

    consecutive_fail = 0

    # 分批查詢
    for i in range(0, len(stock_list), BATCH_SIZE):
        batch = stock_list[i:i + BATCH_SIZE]
        query = _build_query(batch)

        try:
            resp = session.get(
                MIS_URL,
                params={'ex_ch': query, 'json': 1, 'delay': 0},
                timeout=REQUEST_TIMEOUT,
            )
            resp.raise_for_status()
            data = resp.json()
            consecutive_fail = 0
            report_success()
        except Exception as e:
            consecutive_fail += 1
            logger.warning(
                f"即時報價批次 {i // BATCH_SIZE + 1} 失敗（連續 {consecutive_fail}）: {e}"
            )
            # 舊行為是 backoff 後 continue 走完全部 30 批（被封鎖時單輪 15 分鐘
            # 純敲門），反而讓封鎖一直續期。改為交熔斷器判定：達門檻立即中止整輪。
            if report_failure(e):
                logger.warning("MIS 熔斷開路，中止本輪剩餘批次")
                break
            backoff = min(30, 2 ** min(consecutive_fail, 5))
            time.sleep(backoff)
            continue

        msg_array = data.get('msgArray', [])
        for item in msg_array:
            try:
                stock_id = item.get('c', '')  # 股票代號
                if not stock_id or not stock_id.isdigit() or len(stock_id) != 4:
                    continue

                # 驗證資料日期：d 欄位格式 'YYYYMMDD'，必須是今天
                item_date = item.get('d', '')
                if item_date and item_date != today.replace('-', ''):
                    continue  # 非今日資料，跳過

                # z=收盤/最新成交價, o=開盤, h=最高, l=最低, v=成交量(張)
                close_price = _parse_float(item.get('z'))
                open_price = _parse_float(item.get('o'))
                high_price = _parse_float(item.get('h'))
                low_price = _parse_float(item.get('l'))
                volume = _parse_int(item.get('v'))       # 已是張數
                trade_value = 0  # 即時 API 無成交金額

                # y=昨收
                yesterday_close = _parse_float(item.get('y'))

                if close_price is None:
                    # 尚未成交，跳過
                    continue

                # 計算漲跌幅
                if yesterday_close and yesterday_close > 0:
                    change_pct = round((close_price - yesterday_close) / yesterday_close * 100, 2)
                else:
                    change_pct = 0

                upsert_daily_price(conn, stock_id, today,
                                   open_price, high_price, low_price,
                                   close_price, volume, trade_value, change_pct)
                if record_snapshot and snapshot_ts:
                    try:
                        upsert_intraday_snapshot(conn, stock_id, snapshot_ts, volume, close_price)
                    except Exception as e:
                        logger.error(f"intraday_snapshot 寫入錯誤 {stock_id}: {e}")
                total_updated += 1
            except Exception as e:
                logger.error(f"即時報價解析錯誤 {item.get('c', '?')}: {e}")
                continue

        # API 頻率控制
        time.sleep(0.5)

    conn.commit()
    logger.info(f"即時報價更新完成: {today}，共 {total_updated} 筆")
    return total_updated


def _parse_float(val):
    if val is None or val == '-' or val == '' or val == '--':
        return None
    try:
        return float(str(val).replace(',', ''))
    except (ValueError, TypeError):
        return None


def _parse_int(val):
    if val is None or val == '-' or val == '':
        return 0
    try:
        return int(str(val).replace(',', ''))
    except (ValueError, TypeError):
        return 0
