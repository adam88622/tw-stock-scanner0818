"""一次性回補：把 macro_indicators 五大指標 (T10Y3M / CP_SPREAD / DOLLAR / COR3M / MOVE)
往前拉到 FRED / Yahoo 能給的最早日期。執行完不影響每日排程。
"""
import os
import sys

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _PROJECT_ROOT)
os.chdir(_PROJECT_ROOT)
import logging
import sys
from datetime import datetime

from models.database import get_conn, upsert_macro
from scanners.macro_indicators import (
    _fetch_fred_csv, _fetch_yahoo,
    _classify_t10y3m, _classify_cp_spread,
    _classify_dollar_pctile, _classify_cor3m, _classify_move,
    _percentile_rank,
)

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
logger = logging.getLogger(__name__)

YEARS_FRED = 25      # FRED 拉 25 年（約 2001 起）
DAYS_YAHOO = 25 * 365


def _coverage(conn, ind):
    r = conn.execute(
        "SELECT COUNT(*) n, MIN(date) mn, MAX(date) mx FROM macro_indicators WHERE indicator=?",
        (ind,)
    ).fetchone()
    return r['n'], r['mn'], r['mx']


def backfill_t10y3m(conn):
    logger.info("[T10Y3M] FRED %d 年...", YEARS_FRED)
    rows = _fetch_fred_csv('T10Y3M', years=YEARS_FRED)
    for date, val in rows:
        upsert_macro(conn, date, 'T10Y3M', val, _classify_t10y3m(val))
    return len(rows)


def backfill_cp_spread(conn):
    logger.info("[CP_SPREAD] FRED DCPF3M / DTB3 %d 年...", YEARS_FRED)
    cp = _fetch_fred_csv('DCPF3M', years=YEARS_FRED)
    tb = _fetch_fred_csv('DTB3', years=YEARS_FRED)
    tb_map = dict(tb)
    n = 0
    for date, cp_val in cp:
        tb_val = tb_map.get(date)
        if tb_val is None:
            continue
        spread = round(cp_val - tb_val, 4)
        upsert_macro(conn, date, 'CP_SPREAD', spread, _classify_cp_spread(spread))
        n += 1
    return n


def backfill_dollar(conn):
    """Yahoo DXY (DX-Y.NYB) 優先，缺則 fallback FRED DTWEXBGS。"""
    logger.info("[DOLLAR] Yahoo DX-Y.NYB %d 天...", DAYS_YAHOO)
    rows = _fetch_yahoo('DX-Y.NYB', days=DAYS_YAHOO)
    if not rows:
        logger.info("[DOLLAR] Yahoo 失敗，fallback FRED DTWEXBGS")
        rows = _fetch_fred_csv('DTWEXBGS', years=YEARS_FRED)
    if not rows:
        return 0
    values = [v for _, v in rows]
    n = 0
    for idx, (date, val) in enumerate(rows):
        lookback = values[max(0, idx - 250):idx + 1]
        pctile = _percentile_rank(lookback, val) if len(lookback) > 20 else 50
        upsert_macro(conn, date, 'DOLLAR', val, _classify_dollar_pctile(pctile))
        n += 1
    return n


def backfill_vix(conn):
    """COR3M 欄位實際儲存 VIX。Yahoo ^VIX 優先，fallback FRED VIXCLS。"""
    logger.info("[COR3M/VIX] Yahoo ^VIX %d 天...", DAYS_YAHOO)
    rows = _fetch_yahoo('%5EVIX', days=DAYS_YAHOO)
    if not rows:
        logger.info("[COR3M/VIX] Yahoo 失敗，fallback FRED VIXCLS")
        rows = _fetch_fred_csv('VIXCLS', years=YEARS_FRED)
    for date, val in rows:
        upsert_macro(conn, date, 'COR3M', val, _classify_cor3m(val))
    return len(rows)


def backfill_move(conn):
    logger.info("[MOVE] Yahoo ^MOVE %d 天...", DAYS_YAHOO)
    rows = _fetch_yahoo('%5EMOVE', days=DAYS_YAHOO)
    for date, val in rows:
        upsert_macro(conn, date, 'MOVE', val, _classify_move(val))
    return len(rows)


def main():
    conn = get_conn()
    print('=== 回補前覆蓋區間 ===')
    for ind in ['T10Y3M', 'CP_SPREAD', 'DOLLAR', 'COR3M', 'MOVE']:
        n, mn, mx = _coverage(conn, ind)
        print(f'  {ind:10s}  n={n:>5}  {mn} ~ {mx}')

    print()
    fns = [
        ('T10Y3M', backfill_t10y3m),
        ('CP_SPREAD', backfill_cp_spread),
        ('DOLLAR', backfill_dollar),
        ('COR3M', backfill_vix),
        ('MOVE', backfill_move),
    ]
    for name, fn in fns:
        try:
            fetched = fn(conn)
            conn.commit()
            n, mn, mx = _coverage(conn, name)
            print(f'  {name:10s}  抓 {fetched:>5} 筆 → DB 共 {n:>5} 筆  {mn} ~ {mx}')
        except Exception as e:
            logger.exception('%s 失敗: %s', name, e)
            print(f'  {name:10s}  ERROR {e}')

    print()
    print('=== 回補後覆蓋區間 ===')
    for ind in ['T10Y3M', 'CP_SPREAD', 'DOLLAR', 'COR3M', 'MOVE']:
        n, mn, mx = _coverage(conn, ind)
        print(f'  {ind:10s}  n={n:>5}  {mn} ~ {mx}')


if __name__ == '__main__':
    main()
