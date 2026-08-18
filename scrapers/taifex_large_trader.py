"""
TAIFEX「期貨大額交易人未沖銷部位結構表」抓取與解析（個股期大戶淨部位用）

資料源：
  期交所 largeTraderFutDown（POST，queryStartDate / queryEndDate），
  回傳 Big5(cp950) 編碼之 CSV，單次查詢區間上限實測為 3 個月
  （2026/05/14~2026/08/13 可，2026/05/01~2026/08/13 會回錯誤頁）。

CSV 欄位（實測 10 欄，無百分比欄）：
  0 日期  1 商品(契約)  2 商品名稱(契約名稱)  3 到期月份(週別)  4 交易人類別
  5 前五大交易人買方  6 前五大交易人賣方  7 前十大交易人買方  8 前十大交易人賣方
  9 全市場未沖銷部位數

本模組職責（純抓取＋解析，不碰 DB）：
  1. 依日期區間組 POST 請求、強制 Big5 解碼（自動偵測會亂碼）。
  2. **只保留「到期月份 = 999999」= 所有月份合計**：避免只看近月時，
     換月當週部位在新舊月之間搬家造成淨部位假性暴增/暴減。
  3. 保留兩種交易人類別列（同一商品同一天各一列）：
       0 = 整體十大交易人（含造市者）
       1 = 特定法人（三大法人那類）
     兩者相減才得到「非特法的十大交易人」，公式在 scanners/futures_large_trader.py。
  4. 非交易日 / 資料未出 / 區間過長被拒 → 回 []（記 log，不拋例外）。

慣例對齊：from config import REQUEST_HEADERS, REQUEST_TIMEOUT（同 scrapers/taifex_option.py）。
"""
import csv
import io
import logging
from datetime import date, datetime, timedelta

import requests

from config import REQUEST_HEADERS, REQUEST_TIMEOUT

logger = logging.getLogger(__name__)

TAIFEX_LARGE_TRADER_URL = 'https://www.taifex.com.tw/cht/3/largeTraderFutDown'

# CSV 欄位索引（0-based；實測資料列共 10 欄）
_COL_DATE = 0        # 日期        2026/08/13
_COL_PRODUCT = 1     # 商品(契約)  'CD     '（右側補空白，需 strip）
_COL_NAME = 2        # 商品名稱    台積電期貨 / 小型台積電期貨
_COL_MONTH = 3       # 到期月份    '202608 ' / '999999 '
_COL_TYPE = 4        # 交易人類別  0=整體十大(含造市者) 1=特定法人
_COL_B5 = 5          # 前五大交易人買方部位數
_COL_S5 = 6          # 前五大交易人賣方部位數
_COL_B10 = 7         # 前十大交易人買方部位數
_COL_S10 = 8         # 前十大交易人賣方部位數
_COL_OI = 9          # 全市場未沖銷部位數

_MIN_COLS = 10
ALL_MONTHS = '999999'   # 所有月份合計（本模組唯一保留的層級）

# 單次查詢區間上限（實測 3 個月可、3.5 個月被拒）；回補時以此為分段長度
MAX_RANGE_DAYS = 80


def _to_int(v):
    """'-'/''/None → 0；去千分位逗號後取整。大額交易人表的部位數不會是小數。"""
    if v is None:
        return 0
    s = str(v).strip().replace(',', '')
    if s in ('', '-', '--'):
        return 0
    try:
        return int(float(s))
    except (ValueError, TypeError):
        return 0


def _iso_date(v):
    """'2026/08/13' → '2026-08-13'；空 → None。"""
    s = (v or '').strip()
    return s.replace('/', '-') if s else None


def _fmt_query(d):
    """date / datetime / 'YYYYMMDD' / 'YYYY-MM-DD' → 'YYYY/MM/DD'。"""
    if isinstance(d, (date, datetime)):
        return d.strftime('%Y/%m/%d')
    s = str(d).strip().replace('-', '').replace('/', '')
    if len(s) != 8 or not s.isdigit():
        raise ValueError(f'日期須為 YYYYMMDD / YYYY-MM-DD / date，收到 {d!r}')
    return f'{s[0:4]}/{s[4:6]}/{s[6:8]}'


def _parse_csv(text):
    """
    輸入：已用 Big5 解碼的完整 CSV 字串。
    回傳：list[dict]，每筆 {date, product_code, product_name, trader_type,
          top5_buy, top5_sell, top10_buy, top10_sell, market_oi}。
    僅保留到期月份 == 999999 之列；跳過表頭、殘缺列、交易人類別非 0/1 之列。
    """
    rows = []
    reader = csv.reader(io.StringIO(text))
    for i, cols in enumerate(reader):
        if i == 0:
            continue  # 表頭
        if len(cols) < _MIN_COLS:
            continue  # 空行 / 錯誤頁 / 欄位殘缺
        if cols[_COL_MONTH].strip() != ALL_MONTHS:
            continue  # 只取所有月份合計，濾掉逐月列
        ttype = cols[_COL_TYPE].strip()
        if ttype not in ('0', '1'):
            continue
        iso = _iso_date(cols[_COL_DATE])
        code = cols[_COL_PRODUCT].strip()
        if not iso or not code:
            continue
        rows.append({
            'date': iso,
            'product_code': code,
            'product_name': cols[_COL_NAME].strip(),
            'trader_type': int(ttype),
            'top5_buy': _to_int(cols[_COL_B5]),
            'top5_sell': _to_int(cols[_COL_S5]),
            'top10_buy': _to_int(cols[_COL_B10]),
            'top10_sell': _to_int(cols[_COL_S10]),
            'market_oi': _to_int(cols[_COL_OI]),
        })
    return rows


def fetch_large_trader(start_date, end_date=None):
    """
    抓取指定日期區間的期貨大額交易人未沖銷部位（僅所有月份合計 999999）。

    參數：
        start_date: 'YYYYMMDD' / 'YYYY-MM-DD' / date。
        end_date:   同上；不傳則與 start_date 同日（單日抓取）。
                    區間超過 MAX_RANGE_DAYS 時期交所會回錯誤頁，
                    呼叫端應自行分段（見 fetch_large_trader_range）。

    回傳：
        list[dict] — 見 _parse_csv；非交易日 / 資料未出 / 請求失敗 → []。
    """
    try:
        q_start = _fmt_query(start_date)
        q_end = _fmt_query(end_date) if end_date else q_start
    except ValueError as e:
        logger.error('fetch_large_trader: %s', e)
        return []

    payload = {'queryStartDate': q_start, 'queryEndDate': q_end}
    try:
        resp = requests.post(TAIFEX_LARGE_TRADER_URL, data=payload,
                             headers=REQUEST_HEADERS, timeout=max(REQUEST_TIMEOUT, 120))
        resp.raise_for_status()
    except requests.RequestException as e:
        logger.error('fetch_large_trader(%s~%s) 請求失敗: %s', q_start, q_end, e)
        return []

    resp.encoding = 'big5'  # 強制 Big5；自動偵測會亂碼
    try:
        rows = _parse_csv(resp.text)
    except Exception as e:  # 解析防禦：格式異常不應中斷排程
        logger.error('fetch_large_trader(%s~%s) 解析失敗: %s', q_start, q_end, e)
        return []

    if not rows:
        # 期交所對「區間過長」也是回 200 + 一頁極短的錯誤 HTML，這裡一併提醒
        logger.info('fetch_large_trader(%s~%s): 無資料（非交易日 / 未出爐 / 區間過長被拒，'
                    '回應 %d bytes）', q_start, q_end, len(resp.content))
    else:
        n_dates = len({r['date'] for r in rows})
        logger.info('fetch_large_trader(%s~%s): %d 筆 / %d 個交易日',
                    q_start, q_end, len(rows), n_dates)
    return rows


def fetch_large_trader_range(start_date, end_date, chunk_days=MAX_RANGE_DAYS, sleep_sec=3):
    """
    長區間回補用：自動切成 <= chunk_days 的分段連續抓取並合併。

    參數：
        start_date / end_date: 'YYYYMMDD' / 'YYYY-MM-DD' / date。
        chunk_days: 每段天數（含頭尾），預設 MAX_RANGE_DAYS。
        sleep_sec:  段與段之間的禮貌間隔秒數。

    回傳：list[dict]（各段串接，未去重；同一 (date, product_code, trader_type)
          在期交所來源本就唯一，下游 upsert 也會覆蓋）。
    """
    import time

    def _to_date(v):
        if isinstance(v, datetime):
            return v.date()
        if isinstance(v, date):
            return v
        s = str(v).strip().replace('-', '').replace('/', '')
        return date(int(s[0:4]), int(s[4:6]), int(s[6:8]))

    d0, d1 = _to_date(start_date), _to_date(end_date)
    if d0 > d1:
        d0, d1 = d1, d0

    all_rows = []
    cur = d0
    while cur <= d1:
        seg_end = min(cur + timedelta(days=chunk_days - 1), d1)
        rows = fetch_large_trader(cur, seg_end)
        all_rows.extend(rows)
        cur = seg_end + timedelta(days=1)
        if cur <= d1 and sleep_sec:
            time.sleep(sleep_sec)
    return all_rows


if __name__ == '__main__':
    # 簡易自我驗證：實抓最近一日，並印出台積電（CD）大型契約兩種交易人類別
    logging.basicConfig(level=logging.INFO)
    import sys
    day = sys.argv[1] if len(sys.argv) > 1 else datetime.now().strftime('%Y%m%d')
    data = fetch_large_trader(day)
    print(f'總筆數: {len(data)}  商品數: {len({r["product_code"] for r in data})}')
    for r in data:
        if r['product_code'] == 'CD':
            print(r)
