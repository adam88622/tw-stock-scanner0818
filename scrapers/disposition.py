"""
TWSE 注意股 / 處置股公告 scraper

資料源:
  注意股: https://www.twse.com.tw/rwd/zh/announcement/notice
  處置股: https://www.twse.com.tw/rwd/zh/announcement/punish
日期參數: startDate / endDate (yyyymmdd, 西元年)
回應日期格式: '115.03.23' 或 '115/04/16' (民國年)
"""
import re
import time
import logging
import requests

logger = logging.getLogger(__name__)

NOTICE_URL = "https://www.twse.com.tw/rwd/zh/announcement/notice"
PUNISH_URL = "https://www.twse.com.tw/rwd/zh/announcement/punish"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 Chrome/130.0.0.0 Safari/537.36",
}


def _roc_to_iso(roc_str):
    """'115.03.23' / '115/04/16' → '2026-03-23' / '2026-04-16'"""
    if not roc_str:
        return None
    s = re.sub(r"[^\d]", "", str(roc_str).strip())
    if len(s) != 7:
        return None
    try:
        y = int(s[:3]) + 1911
        m = int(s[3:5])
        d = int(s[5:7])
        return f"{y:04d}-{m:02d}-{d:02d}"
    except ValueError:
        return None


def _is_real_stock(stock_id):
    """過濾掉認購權證/ETF變動。真股票通常是 4 碼純數字 1000-9999"""
    sid = str(stock_id).strip()
    return len(sid) == 4 and sid.isdigit() and 1000 <= int(sid) <= 9999


def fetch_notice(start_date, end_date):
    """
    抓注意股清單。
    start_date / end_date: 'YYYYMMDD'
    回傳: list[dict] (含 announce_date_iso, stock_id, name, cumulative, reason, close_price)
    """
    params = {
        "response": "json",
        "startDate": start_date,
        "endDate": end_date,
        "selectType": "",
        "stockNo": "",
        "querytype": "1",
    }
    try:
        r = requests.get(NOTICE_URL, params=params, headers=HEADERS, timeout=20)
        r.raise_for_status()
        d = r.json()
    except Exception as e:
        logger.error(f"fetch_notice failed: {e}")
        return []

    if d.get("stat") != "OK":
        return []

    out = []
    for row in d.get("data", []):
        # fields: 編號, 證券代號, 證券名稱, 累計次數, 注意交易資訊, 日期, 收盤價, 本益比
        try:
            stock_id = str(row[1]).strip()
            name = str(row[2]).strip()
            cumulative = _to_int(row[3])
            reason = str(row[4]).strip()
            announce_date = _roc_to_iso(row[5])
            close_price = _to_float(row[6])
            out.append({
                "announce_date": announce_date,
                "stock_id": stock_id,
                "name": name,
                "cumulative": cumulative,
                "reason": reason,
                "close_price": close_price,
                "is_real_stock": _is_real_stock(stock_id),
            })
        except (IndexError, ValueError) as e:
            logger.warning(f"notice row parse error: {e} {row}")
    return out


def fetch_punish(start_date, end_date):
    """
    抓處置股清單。
    回傳: list[dict] (announce_date, stock_id, name, level, condition, period_start, period_end, action, content)
    """
    params = {
        "response": "json",
        "startDate": start_date,
        "endDate": end_date,
        "selectType": "",
        "stockNo": "",
        "querytype": "1",
    }
    try:
        r = requests.get(PUNISH_URL, params=params, headers=HEADERS, timeout=20)
        r.raise_for_status()
        d = r.json()
    except Exception as e:
        logger.error(f"fetch_punish failed: {e}")
        return []

    if d.get("stat") != "OK":
        return []

    out = []
    for row in d.get("data", []):
        # fields: 編號, 公布日期, 證券代號, 證券名稱, 累計, 處置條件,
        #         處置起迄時間, 處置措施, 處置內容, 備註
        try:
            announce_date = _roc_to_iso(row[1])
            stock_id = str(row[2]).strip()
            name = str(row[3]).strip()
            cumulative = _to_int(row[4])
            condition = str(row[5]).strip()
            period_raw = str(row[6]).strip()
            period_start, period_end = _parse_period(period_raw)
            action = str(row[7]).strip()
            content = str(row[8]).strip() if len(row) > 8 else ""
            out.append({
                "announce_date": announce_date,
                "stock_id": stock_id,
                "name": name,
                "cumulative": cumulative,
                "condition": condition,
                "period_start": period_start,
                "period_end": period_end,
                "action": action,
                "content": content,
                "is_real_stock": _is_real_stock(stock_id),
            })
        except (IndexError, ValueError) as e:
            logger.warning(f"punish row parse error: {e}")
    return out


def _parse_period(s):
    """'115/04/17～115/04/30' → ('2026-04-17', '2026-04-30')"""
    if not s:
        return (None, None)
    parts = re.split(r"[～~至]", s)
    if len(parts) != 2:
        return (None, None)
    return (_roc_to_iso(parts[0]), _roc_to_iso(parts[1]))


def _to_int(v):
    if v is None or v == "":
        return None
    try:
        return int(str(v).replace(",", "").strip())
    except (ValueError, TypeError):
        return None


def _to_float(v):
    if v is None or v == "" or "----" in str(v):
        return None
    try:
        return float(str(v).replace(",", "").strip())
    except (ValueError, TypeError):
        return None
