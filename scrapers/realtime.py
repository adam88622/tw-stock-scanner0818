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


def is_trading_hours():
    """檢查是否在交易時段（週一至五 09:00~13:30）"""
    now = datetime.now()
    if now.weekday() >= 5:  # 週六日
        return False
    hour_min = now.hour * 100 + now.minute
    return 855 <= hour_min <= 1335  # 08:55 ~ 13:35 (含盤前盤後緩衝)


def fetch_realtime_prices(conn):
    """
    抓取所有股票的即時報價，更新 daily_prices。
    回傳: 更新筆數
    """
    from models.database import upsert_daily_price

    if not is_trading_hours():
        logger.info("非交易時段，跳過即時報價抓取")
        return 0

    today = datetime.now().strftime('%Y-%m-%d')

    # 從 DB 取所有股票
    rows = conn.execute("SELECT stock_id, market FROM stocks ORDER BY stock_id").fetchall()
    if not rows:
        logger.warning("資料庫無股票資料，無法抓取即時報價")
        return 0

    stock_list = [(r['stock_id'], r['market']) for r in rows]
    total_updated = 0

    # 分批查詢
    for i in range(0, len(stock_list), BATCH_SIZE):
        batch = stock_list[i:i + BATCH_SIZE]
        query = _build_query(batch)

        try:
            resp = requests.get(
                MIS_URL,
                params={'ex_ch': query},
                headers=REQUEST_HEADERS,
                timeout=REQUEST_TIMEOUT
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            logger.warning(f"即時報價批次 {i // BATCH_SIZE + 1} 失敗: {e}")
            time.sleep(1)
            continue

        msg_array = data.get('msgArray', [])
        for item in msg_array:
            try:
                stock_id = item.get('c', '')  # 股票代號
                if not stock_id or not stock_id.isdigit() or len(stock_id) != 4:
                    continue

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
