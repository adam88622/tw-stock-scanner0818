"""
上櫃（TPEx）資料抓取模組
- 每日收盤行情
- 三大法人買賣超
"""
import time
import logging
import requests
from config import REQUEST_HEADERS, REQUEST_TIMEOUT, REQUEST_RETRY, REQUEST_RETRY_DELAY
from models.database import upsert_stock, upsert_daily_price, upsert_institutional

logger = logging.getLogger(__name__)

# TPEx API endpoints
TPEX_DAILY_URL = 'https://www.tpex.org.tw/web/stock/aftertrading/otc_quotes_no1430/stk_wn1430_result.php'
TPEX_INSTITUTIONAL_URL = 'https://www.tpex.org.tw/web/stock/3insti/daily_trade/3itrade_hedge_result.php'


def _safe_float(val):
    if val is None or val == '--' or val == '' or val == '---' or val == '---':
        return None
    try:
        return float(str(val).replace(',', '').strip())
    except (ValueError, TypeError):
        return None


def _safe_int(val):
    if val is None or val == '--' or val == '' or val == '---':
        return 0
    try:
        return int(str(val).replace(',', '').strip())
    except (ValueError, TypeError):
        return 0


def _to_roc_date(date_str):
    """將 YYYYMMDD 轉為 民國年/MM/DD 格式，如 115/03/19"""
    year = int(date_str[:4]) - 1911
    return f"{year}/{date_str[4:6]}/{date_str[6:8]}"


def _request_with_retry(url, params):
    for attempt in range(REQUEST_RETRY):
        try:
            resp = requests.get(url, params=params, headers=REQUEST_HEADERS, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.warning(f"TPEx 請求失敗 (第 {attempt+1} 次): {e}")
            if attempt < REQUEST_RETRY - 1:
                time.sleep(REQUEST_RETRY_DELAY)
    return None


def _get_table_data(data):
    """從 tables 結構中取出最大的 data 表"""
    if 'aaData' in data:
        return data['aaData']
    if 'tables' in data:
        for t in data['tables']:
            rows = t.get('data', [])
            if len(rows) > 0:
                return rows
    return []


def fetch_tpex_daily(conn, date_str):
    """
    抓取上櫃每日收盤行情。
    date_str: 'YYYYMMDD' 格式
    """
    roc_date = _to_roc_date(date_str)
    params = {
        'l': 'zh-tw',
        'd': roc_date,
        'o': 'json',
        'se': 'AL',
    }
    data = _request_with_retry(TPEX_DAILY_URL, params)

    if not data:
        logger.warning(f"TPEx 每日行情無資料: {date_str}")
        return 0

    rows = _get_table_data(data)
    if not rows:
        logger.warning(f"TPEx 每日行情 data 為空: {date_str}")
        return 0

    iso_date = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"
    count = 0

    for row in rows:
        try:
            stock_id = str(row[0]).strip()
            if not stock_id.isdigit() or len(stock_id) != 4:
                continue

            name = str(row[1]).strip()
            # wn1430 fields: 代號(0), 名稱(1), 收盤(2), 漲跌(3),
            # 開盤(4), 最高(5), 最低(6), 成交股數(7),
            # 成交金額(8), 成交筆數(9)
            close_price = _safe_float(row[2])
            change = _safe_float(row[3])
            open_price = _safe_float(row[4])
            high_price = _safe_float(row[5])
            low_price = _safe_float(row[6])
            volume = _safe_int(row[7])       # 成交股數
            trade_value = _safe_int(row[8])  # 成交金額

            if close_price is None:
                continue

            # 漲跌幅
            if change is not None and close_price != change:
                prev = close_price - change
                change_pct = round((change / prev) * 100, 2) if prev != 0 else 0
            else:
                change_pct = 0

            # 成交股數轉張數
            volume_lots = volume // 1000 if volume else 0

            upsert_stock(conn, stock_id, name, 'tpex')
            upsert_daily_price(conn, stock_id, iso_date, open_price, high_price,
                             low_price, close_price, volume_lots, trade_value, change_pct)
            count += 1
        except Exception as e:
            logger.error(f"處理 TPEx 個股資料錯誤: {row[0] if row else '?'} - {e}")
            continue

    logger.info(f"TPEx 每日行情完成: {iso_date}，共 {count} 筆")
    return count


def fetch_tpex_institutional(conn, date_str):
    """
    抓取上櫃三大法人買賣超。
    date_str: 'YYYYMMDD' 格式
    """
    roc_date = _to_roc_date(date_str)
    params = {
        'l': 'zh-tw',
        'd': roc_date,
        'se': 'EW',
        't': 'D',
        'o': 'json',
    }
    data = _request_with_retry(TPEX_INSTITUTIONAL_URL, params)

    if not data:
        logger.warning(f"TPEx 法人買賣超無資料: {date_str}")
        return 0

    rows = _get_table_data(data)
    if not rows:
        logger.warning(f"TPEx 法人買賣超 data 為空: {date_str}")
        return 0

    iso_date = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"
    count = 0

    for row in rows:
        try:
            stock_id = str(row[0]).strip()
            if not stock_id.isdigit() or len(stock_id) != 4:
                continue

            # 欄位 (每3個一組: 買進, 賣出, 買賣超):
            # 2-4: 外資(不含自營)
            # 5-7: 外資自營
            # 8-10: 外資合計
            # 11-13: 投信
            # 14-16: 自營商(自行)
            # 17-19: 自營商(避險)
            # 20-22: 自營商合計
            # 23: 三大法人合計
            foreign_buy = _safe_int(row[4]) // 1000   # 外資買賣超(不含自營)，股轉張
            sitc_buy = _safe_int(row[13]) // 1000     # 投信買賣超
            dealer_buy = _safe_int(row[22]) // 1000   # 自營商買賣超(合計)

            upsert_institutional(conn, stock_id, iso_date, foreign_buy, sitc_buy, dealer_buy)
            count += 1
        except Exception as e:
            logger.error(f"處理 TPEx 法人資料錯誤: {row[0] if row else '?'} - {e}")
            continue

    logger.info(f"TPEx 法人買賣超完成: {iso_date}，共 {count} 筆")
    return count
