"""
volume_alert package — 歷史資料 seed 抓取
從 TWSE / TPEx 公開 API 抓 N 個交易日的個股 OHLCV，
建立 ADV20 / 爆量比較所需的歷史基準。
"""
import time
import logging
import requests
from datetime import datetime, timedelta

from config import (
    REQUEST_HEADERS, REQUEST_TIMEOUT, REQUEST_RETRY, REQUEST_RETRY_DELAY,
    TWSE_DAILY_URL, TPEX_DAILY_URL,
)
from models.database import upsert_stock, upsert_daily_price

logger = logging.getLogger(__name__)


def _safe_float(val):
    if val is None or val in ('--', '', '---'):
        return None
    try:
        return float(str(val).replace(',', '').strip())
    except (ValueError, TypeError):
        return None


def _safe_int(val):
    if val is None or val in ('--', '', '---'):
        return 0
    try:
        return int(str(val).replace(',', '').strip())
    except (ValueError, TypeError):
        return 0


def _to_roc_date(date_str):
    year = int(date_str[:4]) - 1911
    return f"{year}/{date_str[4:6]}/{date_str[6:8]}"


def _request_with_retry(url, params, label=''):
    for attempt in range(REQUEST_RETRY):
        try:
            resp = requests.get(url, params=params, headers=REQUEST_HEADERS, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.warning(f"{label} 請求失敗 (第 {attempt + 1} 次): {e}")
            if attempt < REQUEST_RETRY - 1:
                time.sleep(REQUEST_RETRY_DELAY)
    return None


def fetch_twse_daily(conn, date_str):
    """抓 TWSE 上市單日 OHLCV"""
    data = _request_with_retry(
        TWSE_DAILY_URL,
        {'response': 'json', 'date': date_str, 'type': 'ALL'},
        label='TWSE',
    )
    if not data or data.get('stat') != 'OK':
        return 0

    rows = None
    if 'tables' in data:
        best = max(data['tables'], key=lambda t: len(t.get('data', [])))
        if len(best.get('data', [])) > 100:
            rows = best['data']
    else:
        for key in ('data9', 'data8', 'data7', 'data6', 'data5'):
            if key in data:
                rows = data[key]
                break
    if not rows:
        return 0

    iso_date = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"
    count = 0
    for row in rows:
        try:
            stock_id = row[0].strip()
            if not stock_id.isdigit() or len(stock_id) != 4:
                continue
            name = row[1].strip()
            volume = _safe_int(row[2])
            trade_value = _safe_int(row[4])
            open_price = _safe_float(row[5])
            high_price = _safe_float(row[6])
            low_price = _safe_float(row[7])
            close_price = _safe_float(row[8])
            sign_html = str(row[9]) if len(row) > 9 else ''
            change = _safe_float(row[10]) if len(row) > 10 else None
            if change is not None and '-' in sign_html:
                change = -change
            if close_price is None:
                continue
            if change is not None and close_price != change:
                prev = close_price - change
                change_pct = round((change / prev) * 100, 2) if prev != 0 else 0
            else:
                change_pct = 0
            volume_lots = volume // 1000 if volume else 0
            upsert_stock(conn, stock_id, name, 'twse')
            upsert_daily_price(conn, stock_id, iso_date, open_price, high_price,
                               low_price, close_price, volume_lots, trade_value, change_pct)
            count += 1
        except Exception:
            continue
    return count


def fetch_tpex_daily(conn, date_str):
    """抓 TPEx 上櫃單日 OHLCV"""
    data = _request_with_retry(
        TPEX_DAILY_URL,
        {'l': 'zh-tw', 'd': _to_roc_date(date_str), 'o': 'json', 'se': 'AL'},
        label='TPEx',
    )
    if not data:
        return 0
    rows = data.get('aaData') or []
    if not rows and 'tables' in data:
        for t in data['tables']:
            r = t.get('data', [])
            if r:
                rows = r
                break
    if not rows:
        return 0

    iso_date = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"
    count = 0
    for row in rows:
        try:
            stock_id = str(row[0]).strip()
            if not stock_id.isdigit() or len(stock_id) != 4:
                continue
            name = str(row[1]).strip()
            close_price = _safe_float(row[2])
            change = _safe_float(row[3])
            open_price = _safe_float(row[4])
            high_price = _safe_float(row[5])
            low_price = _safe_float(row[6])
            volume = _safe_int(row[7])
            trade_value = _safe_int(row[8])
            if close_price is None:
                continue
            if change is not None and close_price != change:
                prev = close_price - change
                change_pct = round((change / prev) * 100, 2) if prev != 0 else 0
            else:
                change_pct = 0
            volume_lots = volume // 1000 if volume else 0
            upsert_stock(conn, stock_id, name, 'tpex')
            upsert_daily_price(conn, stock_id, iso_date, open_price, high_price,
                               low_price, close_price, volume_lots, trade_value, change_pct)
            count += 1
        except Exception:
            continue
    return count


def seed_recent_days(conn, n_days):
    """
    回頭抓最多 n_days 個交易日（自動跳過週末與假日 — 抓不到資料就跳過）。
    為了避免過度打 TWSE/TPEx，每個交易日之間 sleep 2 秒。
    """
    today = datetime.now().date()
    fetched_twse = 0
    fetched_tpex = 0
    cursor = today
    days_done = 0
    days_walked = 0

    while days_done < n_days and days_walked < n_days * 3:
        days_walked += 1
        if cursor.weekday() < 5:
            date_str = cursor.strftime('%Y%m%d')
            logger.info(f"抓取 {cursor} ...")
            t = fetch_twse_daily(conn, date_str)
            time.sleep(2)
            p = fetch_tpex_daily(conn, date_str)
            time.sleep(2)
            if t > 0 or p > 0:
                conn.commit()
                fetched_twse += t
                fetched_tpex += p
                days_done += 1
                logger.info(f"  → twse={t}, tpex={p}（累計 {days_done}/{n_days} 個交易日）")
            else:
                logger.info(f"  → 無資料（可能為假日）")
        cursor -= timedelta(days=1)

    logger.info(f"完成：共 {days_done} 個交易日，twse 累計 {fetched_twse}，tpex 累計 {fetched_tpex}")
    return days_done
