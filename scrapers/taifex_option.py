"""
TAIFEX TXO 台指選擇權盤後每日行情抓取與解析（FN-001，option-sr）

資料源：
  期交所「選擇權每日行情下載」端點 optDataDown（down_type=1），
  單日 GET 回傳 Big5(cp950) 編碼之 CSV，含全契約全履約價的收盤/成交量/結算價/
  未沖銷契約數(OI)/漲跌/契約到期日，且每個 (履約價,買賣權) 同時有「一般」與
  「盤後」兩個交易時段列。

本模組職責（純抓取＋解析，不碰 DB）：
  1. 依日期組請求、強制 Big5 解碼（自動偵測會亂碼）。
  2. 逐列解析並正規化欄位（日期轉 ISO、買權/賣權→C/P、'-' → None、漲跌% 去 %）。
  3. **只保留「一般」交易時段列**：盤後列 OI/結算多為空('-')，若混入會在
     下游唯一鍵 (date,contract,strike,cp) 覆蓋掉真實 OI，導致支撐/壓力計算全錯。
  4. 非交易日 / 資料未出 → 回 []（不拋例外）。

慣例對齊：from config import REQUEST_HEADERS, REQUEST_TIMEOUT（同 scanners/futures_basis.py）。
"""
import csv
import io
import logging

import requests

from config import REQUEST_HEADERS, REQUEST_TIMEOUT

logger = logging.getLogger(__name__)

TAIFEX_OPT_URL = 'https://www.taifex.com.tw/cht/3/optDataDown'

# CSV 欄位索引（0-based；資料列實測共 21 欄，表頭因尾逗號 22 欄）
_COL_DATE = 0        # 交易日期  2026/06/30
_COL_COMMODITY = 1   # 契約      TXO（過濾用）
_COL_CONTRACT = 2    # 到期月份(週別)  202607W1 / 202607F1 / 202607(月選)
_COL_STRIKE = 3      # 履約價    40100.0000
_COL_CP = 4          # 買賣權    買權 / 賣權
_COL_CLOSE = 8       # 收盤價
_COL_VOLUME = 9      # 成交量
_COL_SETTLEMENT = 10 # 結算價
_COL_OI = 11         # 未沖銷契約數（OI）
_COL_SESSION = 17    # 交易時段  一般 / 盤後
_COL_CHANGE = 18     # 漲跌價
_COL_CHANGE_PCT = 19 # 漲跌%     -78.26%
_COL_EXPIRY = 20     # 契約到期日  20260701

_MIN_COLS = 21             # 資料列至少要有的欄數（含 expiry）
_SESSION_REGULAR = '一般'  # 只保留一般時段


def _to_float(v):
    """'-'/''/'--'/None → None；去除千分位逗號與尾端 '%'；否則 float。比照 futures_basis._parse_float。"""
    if v is None:
        return None
    s = str(v).strip()
    if s in ('', '-', '--'):
        return None
    s = s.replace(',', '').rstrip('%')
    try:
        return float(s)
    except (ValueError, TypeError):
        return None


def _to_int(v):
    """數值欄位取整；無效 → None。"""
    f = _to_float(v)
    return int(f) if f is not None else None


def _cp_of(v):
    """買權→'C'、賣權→'P'；其他 → None。"""
    s = (v or '').strip()
    if s == '買權':
        return 'C'
    if s == '賣權':
        return 'P'
    return None


def _iso_date(v):
    """'2026/06/30' → '2026-06-30'；空 → None。"""
    s = (v or '').strip()
    return s.replace('/', '-') if s else None


def _parse_csv(text):
    """
    輸入：已用 Big5 解碼的完整 CSV 字串。
    回傳：list[dict]，每筆 {date, contract, strike, cp, close, settlement,
          change, change_pct, volume, oi, expiry}。
    僅保留 commodity==TXO 且交易時段=='一般' 之列；跳過表頭、殘缺列、
    無法辨識買賣權或履約價之列。空 CSV / 僅表頭 → []。
    """
    rows = []
    reader = csv.reader(io.StringIO(text))
    for i, cols in enumerate(reader):
        if i == 0:
            continue  # 表頭
        if len(cols) < _MIN_COLS:
            continue  # 空行 / 欄位殘缺
        if cols[_COL_COMMODITY].strip() != 'TXO':
            continue
        if cols[_COL_SESSION].strip() != _SESSION_REGULAR:
            continue  # 濾掉盤後列（避免其空 OI 覆蓋真實 OI）
        cp = _cp_of(cols[_COL_CP])
        strike = _to_float(cols[_COL_STRIKE])
        if cp is None or strike is None:
            continue  # 非有效行情列
        rows.append({
            'date': _iso_date(cols[_COL_DATE]),
            'contract': cols[_COL_CONTRACT].strip(),
            'strike': strike,
            'cp': cp,
            'close': _to_float(cols[_COL_CLOSE]),
            'settlement': _to_float(cols[_COL_SETTLEMENT]),
            'change': _to_float(cols[_COL_CHANGE]),
            'change_pct': _to_float(cols[_COL_CHANGE_PCT]),
            'volume': _to_int(cols[_COL_VOLUME]),
            'oi': _to_int(cols[_COL_OI]),
            'expiry': (cols[_COL_EXPIRY].strip() or None),
        })
    return rows


def fetch_txo_daily(date_str):
    """
    抓取指定單日 TXO 選擇權盤後每日行情（含 OI），解析為逐列 dict。

    參數：
        date_str (str): 'YYYYMMDD'（比照既有 scraper 慣例）。

    回傳：
        list[dict] — 每筆 {date(ISO), contract, strike, cp('C'/'P'),
        close, settlement, change, change_pct, volume, oi, expiry}；
        數值欄位為 float/int 或 None。
        非交易日 / 資料未出 / 請求失敗 → []（記 log，不拋例外）。
    """
    if not date_str or len(str(date_str)) != 8 or not str(date_str).isdigit():
        logger.error('fetch_txo_daily: date_str 須為 YYYYMMDD，收到 %r', date_str)
        return []
    date_str = str(date_str)
    q = f'{date_str[0:4]}/{date_str[4:6]}/{date_str[6:8]}'
    params = {
        'down_type': 1,
        'commodity_id': 'TXO',   # 必須 snake_case（commodityId 會失敗）
        'queryStartDate': q,     # 單日抓取：起訖同日
        'queryEndDate': q,
    }
    try:
        resp = requests.get(TAIFEX_OPT_URL, params=params,
                            headers=REQUEST_HEADERS, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
    except requests.RequestException as e:
        logger.error('fetch_txo_daily(%s) 請求失敗: %s', date_str, e)
        return []

    resp.encoding = 'big5'  # 強制 Big5；requests 自動偵測會亂碼（errors=replace 由 .text 處理）
    try:
        rows = _parse_csv(resp.text)
    except Exception as e:  # 解析防禦：格式異常不應中斷排程
        logger.error('fetch_txo_daily(%s) 解析失敗: %s', date_str, e)
        return []

    logger.info('fetch_txo_daily(%s): 解析出 %d 筆一般時段資料', date_str, len(rows))
    return rows


if __name__ == '__main__':
    # 簡易自我驗證：實抓 2026-06-30 單日
    logging.basicConfig(level=logging.INFO)
    data = fetch_txo_daily('20260630')
    print(f'總筆數: {len(data)}')
    calls = [r for r in data if r['cp'] == 'C']
    puts = [r for r in data if r['cp'] == 'P']
    nonzero_oi = [r for r in data if r['oi']]
    print(f'call={len(calls)} put={len(puts)} 非零OI={len(nonzero_oi)}')
    if data:
        print('範例一列:', data[0])
