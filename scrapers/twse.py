"""
上市（TWSE）資料抓取模組
- 每日收盤行情
- 三大法人買賣超
"""
import time
import logging
import requests
from config import TWSE_DAILY_URL, TWSE_INSTITUTIONAL_URL, REQUEST_HEADERS, REQUEST_TIMEOUT, REQUEST_RETRY, REQUEST_RETRY_DELAY
from models.database import upsert_stock, upsert_daily_price, upsert_institutional

logger = logging.getLogger(__name__)


def _safe_float(val):
    """安全轉換為 float，處理逗號和特殊符號"""
    if val is None or val == '--' or val == '':
        return None
    try:
        return float(str(val).replace(',', '').replace('X', '').strip())
    except (ValueError, TypeError):
        return None


def _safe_int(val):
    """安全轉換為 int"""
    if val is None or val == '--' or val == '':
        return 0
    try:
        return int(str(val).replace(',', '').replace('X', '').strip())
    except (ValueError, TypeError):
        return 0


def _request_with_retry(url, params):
    """帶重試的 HTTP 請求"""
    for attempt in range(REQUEST_RETRY):
        try:
            resp = requests.get(url, params=params, headers=REQUEST_HEADERS, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.warning(f"請求失敗 (第 {attempt+1} 次): {e}")
            if attempt < REQUEST_RETRY - 1:
                time.sleep(REQUEST_RETRY_DELAY)
    return None


def fetch_twse_daily(conn, date_str):
    """
    抓取上市每日收盤行情。
    date_str: 'YYYYMMDD' 格式，如 '20260319'
    回傳: 成功筆數
    """
    params = {
        'response': 'json',
        'date': date_str,
        'type': 'ALL',
    }
    data = _request_with_retry(TWSE_DAILY_URL, params)

    if not data or data.get('stat') != 'OK':
        logger.warning(f"TWSE 每日行情無資料: {date_str}")
        return 0

    # 新版 API 使用 tables 結構，個股行情在最大的那個 table
    rows = None
    if 'tables' in data:
        # 找最大的 table（個股行情資料最多）
        best = max(data['tables'], key=lambda t: len(t.get('data', [])))
        if len(best.get('data', [])) > 100:
            rows = best['data']
    else:
        # 舊版 API fallback
        for key in ['data9', 'data8', 'data7', 'data6', 'data5']:
            if key in data:
                rows = data[key]
                break

    if not rows:
        logger.warning(f"TWSE 找不到個股資料: {date_str}")
        return 0

    iso_date = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"
    count = 0

    for row in rows:
        try:
            stock_id = row[0].strip()
            # 只處理一般股票（4碼數字）
            if not stock_id.isdigit() or len(stock_id) != 4:
                continue

            name = row[1].strip()
            volume = _safe_int(row[2])  # 成交股數
            trade_value = _safe_int(row[4])  # 成交金額
            open_price = _safe_float(row[5])
            high_price = _safe_float(row[6])
            low_price = _safe_float(row[7])
            close_price = _safe_float(row[8])

            # 漲跌幅計算
            # row[9] 含漲跌符號: <p style= color:red>+</p> 或 <p style= color:green>-</p>
            # row[10] 是漲跌價差（絕對值）
            sign_html = str(row[9]) if len(row) > 9 else ''
            change = _safe_float(row[10])  # 漲跌價差（絕對值）
            if change is not None and '-' in sign_html:
                change = -change
            if close_price and change is not None and close_price != change:
                prev = close_price - change
                change_pct = round((change / prev) * 100, 2) if prev != 0 else 0
            else:
                change_pct = 0

            # 成交股數轉成張數
            volume_lots = volume // 1000 if volume else 0

            if close_price is None:
                continue

            upsert_stock(conn, stock_id, name, 'twse')
            upsert_daily_price(conn, stock_id, iso_date, open_price, high_price,
                             low_price, close_price, volume_lots, trade_value, change_pct)
            count += 1
        except Exception as e:
            logger.error(f"處理 TWSE 個股資料錯誤: {row[0] if row else '?'} - {e}")
            continue

    logger.info(f"TWSE 每日行情完成: {iso_date}，共 {count} 筆")
    return count


def fetch_twse_institutional(conn, date_str):
    """
    抓取上市三大法人買賣超。
    date_str: 'YYYYMMDD' 格式
    回傳: 成功筆數
    """
    params = {
        'response': 'json',
        'date': date_str,
        'selectType': 'ALL',
    }
    data = _request_with_retry(TWSE_INSTITUTIONAL_URL, params)

    if not data or data.get('stat') != 'OK':
        logger.warning(f"TWSE 法人買賣超無資料: {date_str}")
        return 0

    rows = data.get('data', [])
    if not rows:
        logger.warning(f"TWSE 法人買賣超 data 為空: {date_str}")
        return 0

    iso_date = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"
    count = 0

    for row in rows:
        try:
            stock_id = row[0].strip()
            if not stock_id.isdigit() or len(stock_id) != 4:
                continue

            # T86 欄位:
            # 0=代號, 1=名稱, 2=外資買進(不含自營), 3=外資賣出, 4=外資買賣超(不含自營),
            # 5=外資自營買, 6=外資自營賣, 7=外資自營買賣超,
            # 8=投信買, 9=投信賣, 10=投信買賣超,
            # 11=自營商買賣超(合計), 12-14=自營(自行), 15-17=自營(避險),
            # 18=三大法人合計
            foreign_buy = _safe_int(row[4]) // 1000   # 外資買賣超(不含自營)，股轉張
            sitc_buy = _safe_int(row[10]) // 1000     # 投信買賣超
            dealer_buy = _safe_int(row[11]) // 1000   # 自營商買賣超(合計)

            name = (row[1] or '').strip() if len(row) > 1 else ''
            if name:
                conn.execute(
                    "INSERT OR IGNORE INTO stocks (stock_id, name, market, sector) VALUES (?, ?, 'twse', '')",
                    (stock_id, name),
                )
            upsert_institutional(conn, stock_id, iso_date, foreign_buy, sitc_buy, dealer_buy)
            count += 1
        except Exception as e:
            logger.error(f"處理 TWSE 法人資料錯誤: {row[0] if row else '?'} - {e}")
            continue

    logger.info(f"TWSE 法人買賣超完成: {iso_date}，共 {count} 筆")
    return count
