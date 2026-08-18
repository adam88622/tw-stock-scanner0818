"""
TAIFEX「股票期貨標的證券」清單抓取（期貨商品代碼 ↔ 標的股票代號 對照表）

資料源：
  期交所 https://www.taifex.com.tw/cht/2/stockLists（HTML 表格，UTF-8）
  欄位：股票期貨/選擇權商品代碼、標的證券、證券代號、標的證券簡稱、
        是否為股票期貨標的、是否為股票選擇權標的、…

實測（2026-08-14）：共 320 列 / 270 檔標的證券，其中 50 檔同時有兩個商品代碼
——一個大型、一個小型（例：2330 台積電 → CD 大型、QF 小型），
但**此頁不標示哪個是小型**。因此大小型的判定改由期交所大額交易人 CSV 的
「商品名稱」提供（小型契約名稱含「小型」，如「小型台積電期貨」），
呼叫端把 {商品代碼: 商品名稱} 傳進來即可（見 name_by_code 參數）。

契約乘數（換算「張」用；1 張 = 1,000 股 / 1,000 受益權單位）：
  股票期貨      2,000 股             → 2 張/口
  小型股票期貨  100 股               → 0.1 張/口
  ETF 期貨      10,000 受益權單位    → 10 張/口
  小型 ETF 期貨 1,000 受益權單位     → 1 張/口
ETF 標的以證券代號開頭 '00' 判定（台股 ETF 代號慣例）。
"""
import logging
import re

import requests

from config import REQUEST_HEADERS, REQUEST_TIMEOUT

logger = logging.getLogger(__name__)

TAIFEX_STOCK_LISTS_URL = 'https://www.taifex.com.tw/cht/2/stockLists'

# 契約乘數 → 張/口
LOTS_STOCK = 2.0        # 股票期貨 2,000 股
LOTS_STOCK_MINI = 0.1   # 小型股票期貨 100 股
LOTS_ETF = 10.0         # ETF 期貨 10,000 受益權單位
LOTS_ETF_MINI = 1.0     # 小型 ETF 期貨 1,000 受益權單位

_RE_TR = re.compile(r'<tr[^>]*>(.*?)</tr>', re.S)
_RE_TD = re.compile(r'<t[dh][^>]*>(.*?)</t[dh]>', re.S)
_RE_TAG = re.compile(r'<[^>]+>')
_RE_CODE = re.compile(r'^[A-Z0-9]{2,4}$')
_RE_SID = re.compile(r'^\d{4,6}[A-Z]?$')


def _clean(html):
    """去標籤 + 壓縮空白。"""
    return re.sub(r'\s+', ' ', _RE_TAG.sub('', html)).strip()


def lots_per_contract(stock_id, is_mini):
    """依標的類型與大小型回傳「張/口」換算係數。"""
    is_etf = str(stock_id).startswith('00')
    if is_etf:
        return LOTS_ETF_MINI if is_mini else LOTS_ETF
    return LOTS_STOCK_MINI if is_mini else LOTS_STOCK


def fetch_stock_futures_list(name_by_code=None):
    """
    抓取股票期貨標的證券清單。

    參數：
        name_by_code (dict|None): {商品代碼: 期交所商品名稱}，用來判定大小型。
            通常由當日大額交易人 CSV 提供。未提供時一律視為大型（is_mini=0），
            此時同一標的的兩個代碼會有相同乘數 —— 呼叫端應盡量提供。

    回傳：
        list[dict] — {product_code, stock_id, stock_name, product_name,
                      is_mini(0/1), is_etf(0/1), lots_per_contract(float)}
        僅保留「是股票期貨標的」之列。請求 / 解析失敗 → []（記 log，不拋例外）。
    """
    name_by_code = name_by_code or {}
    try:
        resp = requests.get(TAIFEX_STOCK_LISTS_URL, headers=REQUEST_HEADERS,
                            timeout=max(REQUEST_TIMEOUT, 30))
        resp.raise_for_status()
    except requests.RequestException as e:
        logger.error('fetch_stock_futures_list 請求失敗: %s', e)
        return []

    resp.encoding = 'utf-8'
    try:
        rows = []
        seen = set()
        for tr in _RE_TR.findall(resp.text):
            cells = [_clean(td) for td in _RE_TD.findall(tr)]
            if len(cells) < 5:
                continue
            code, sid, short = cells[0], cells[2], cells[3]
            if not _RE_CODE.match(code) or not _RE_SID.match(sid):
                continue  # 表頭 / 說明列
            if '是股票期貨標的' not in cells[4]:
                continue  # 只有選擇權標的、非期貨標的
            if code in seen:
                continue
            seen.add(code)
            pname = (name_by_code.get(code) or '').strip()
            is_mini = 1 if '小型' in pname else 0
            rows.append({
                'product_code': code,
                'stock_id': sid,
                'stock_name': short,
                'product_name': pname,
                'is_mini': is_mini,
                'is_etf': 1 if sid.startswith('00') else 0,
                'lots_per_contract': lots_per_contract(sid, is_mini),
            })
    except Exception as e:  # 解析防禦：期交所改版不應中斷排程
        logger.error('fetch_stock_futures_list 解析失敗: %s', e)
        return []

    n_mini = sum(r['is_mini'] for r in rows)
    logger.info('fetch_stock_futures_list: %d 個商品代碼 / %d 檔標的（小型 %d）',
                len(rows), len({r['stock_id'] for r in rows}), n_mini)
    return rows


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    from scrapers.taifex_large_trader import fetch_large_trader
    from datetime import datetime, timedelta

    # 往回找最近一個有資料的日子，取商品名稱來判定大小型
    names = {}
    d = datetime.now()
    for _ in range(7):
        rows = fetch_large_trader(d.strftime('%Y%m%d'))
        if rows:
            names = {r['product_code']: r['product_name'] for r in rows}
            break
        d -= timedelta(days=1)

    data = fetch_stock_futures_list(names)
    print(f'共 {len(data)} 筆')
    for r in data:
        if r['stock_id'] in ('2330', '0050'):
            print(r)
