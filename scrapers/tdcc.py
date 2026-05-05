"""
TDCC 集保戶股權分散表 scraper

每週五公布,17 個 band:
  band 1-15: 持股區間 (1-999 ... 1,000,001+)
  band 16: 差異數調整
  band 17: 合計

資料源: https://www.tdcc.com.tw/portal/zh/smWeb/qryStock
- POST 表單,需要 SYNCHRONIZER_TOKEN (CSRF) + cookie session
- User-Agent 必須像瀏覽器,否則回 "查無資料"
"""
import re
import time
import logging
import requests
from config import REQUEST_TIMEOUT, REQUEST_RETRY, REQUEST_RETRY_DELAY

logger = logging.getLogger(__name__)

TDCC_URL = "https://www.tdcc.com.tw/portal/zh/smWeb/qryStock"

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/130.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
    "Origin": "https://www.tdcc.com.tw",
    "Referer": TDCC_URL,
}


def make_session():
    s = requests.Session()
    s.headers.update(BROWSER_HEADERS)
    return s


def fetch_form_state(session):
    """
    GET 首頁,取得 SYNCHRONIZER_TOKEN + 可選的 scaDate 列表 + 最新 firDate。
    回傳: dict(token, fir_date, sca_dates: list[str])
    """
    resp = session.get(TDCC_URL, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    html = resp.text

    token = None
    m = re.search(r'name="SYNCHRONIZER_TOKEN"\s+value="([^"]+)"', html)
    if m:
        token = m.group(1)

    fir_date = None
    m = re.search(r'name="firDate"\s+value="(\d+)"', html)
    if m:
        fir_date = m.group(1)

    sca_dates = re.findall(r'<option value="(\d{8})"', html)

    return {"token": token, "fir_date": fir_date, "sca_dates": sca_dates}


def parse_holder_table(html):
    """
    從查詢結果 HTML 解析 17 行 band 資料。
    回傳: list[dict] with keys: band, band_label, holders, shares, pct
    解析失敗或查無資料則回傳 []
    """
    m = re.search(r"持股/單位數分級.*?</table>", html, re.S)
    if not m:
        return []

    block = m.group(0)
    rows = re.findall(r"<tr[^>]*>(.*?)</tr>", block, re.S)

    parsed = []
    for r in rows:
        cells = re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", r, re.S)
        cells = [re.sub(r"<[^>]+>", "", c).strip() for c in cells]
        if len(cells) != 5:
            continue
        # 跳過表頭(內容是 "分級","人數" 這類,band 欄位不是純數字)
        if not cells[0].isdigit():
            continue

        try:
            band = int(cells[0])
        except ValueError:
            continue

        band_label = cells[1]
        holders = _to_int(cells[2])
        shares = _to_int(cells[3])
        pct = _to_float(cells[4])

        parsed.append({
            "band": band,
            "band_label": band_label,
            "holders": holders,
            "shares": shares,
            "pct": pct,
        })

    # bands 1–15 是 15 個持股區間,必須全部存在。
    # 最後一行是「合計」(band 16 或 17,依是否有「差異數調整」而定)。
    # 「差異數調整」介於 15 與合計之間,多數股票沒有。
    bands_present = {r['band'] for r in parsed}
    if not set(range(1, 16)).issubset(bands_present):
        return []
    return parsed


def fetch_holder_distribution(session, stock_id, sca_date, form_state=None):
    """
    抓單支股票在某週的持股分佈。
    sca_date: YYYYMMDD
    回傳: list[dict] (17 row) 或 [] (查無 / 失敗)
    """
    if form_state is None:
        form_state = fetch_form_state(session)

    if not form_state.get("token"):
        logger.error("TDCC: 取不到 SYNCHRONIZER_TOKEN")
        return []

    payload = {
        "SYNCHRONIZER_TOKEN": form_state["token"],
        "SYNCHRONIZER_URI": "/portal/zh/smWeb/qryStock",
        "method": "submit",
        "firDate": form_state.get("fir_date") or sca_date,
        "scaDate": sca_date,
        "sqlMethod": "StockNo",
        "stockNo": str(stock_id).strip(),
        "stockName": "",
    }

    for attempt in range(REQUEST_RETRY):
        try:
            resp = session.post(TDCC_URL, data=payload, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            rows = parse_holder_table(resp.text)
            if rows:
                return rows
            # 查無資料 — 不重試,可能該股當週未上市/已下市
            return []
        except requests.RequestException as e:
            logger.warning(f"TDCC fetch {stock_id}@{sca_date} 第 {attempt+1} 次失敗: {e}")
            if attempt < REQUEST_RETRY - 1:
                time.sleep(REQUEST_RETRY_DELAY)
    return []


def _to_int(s):
    if s is None or s == "":
        return None
    try:
        return int(str(s).replace(",", "").strip())
    except (ValueError, TypeError):
        return None


def _to_float(s):
    if s is None or s == "":
        return None
    try:
        return float(str(s).replace(",", "").strip())
    except (ValueError, TypeError):
        return None
