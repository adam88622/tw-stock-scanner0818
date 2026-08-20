"""
還原權息 (back-adjusted prices) backfill

原理:
  TWSE 的 change_pct 已經是「除權息還原後」的官方漲跌幅,
  因此 adj_close 序列必須滿足 adj_close[t] / adj_close[t-1] = 1 + chg[t]/100

  從最新一筆開始往回推:
    adj_close[latest]   = close[latest]
    adj_close[t-1]      = adj_close[t] / (1 + chg[t]/100)
    factor[t]           = adj_close[t] / close[t]
    adj_open[t]         = open[t]  * factor[t]
    adj_high[t]         = high[t]  * factor[t]
    adj_low[t]          = low[t]   * factor[t]

用法:
  python backfill_adj_prices.py
"""
import os
import sys

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _PROJECT_ROOT)
os.chdir(_PROJECT_ROOT)
import logging
import time
from models.database import get_conn

logging.basicConfig(level=logging.INFO,
                    format='[%(asctime)s] %(message)s', datefmt='%H:%M:%S')
logger = logging.getLogger(__name__)


def adjust_one(conn, stock_id):
    rows = conn.execute("""
        SELECT date, open_price, high_price, low_price, close_price, change_pct
        FROM daily_prices WHERE stock_id = ?
        ORDER BY date ASC
    """, (stock_id,)).fetchall()

    n = len(rows)
    if n == 0:
        return 0

    # 從尾巴 adj_close 直接 = close, 反向推
    adj_close = [None] * n
    adj_close[-1] = rows[-1]['close_price']

    for i in range(n - 2, -1, -1):
        chg = rows[i + 1]['change_pct']
        if chg is None:
            chg = 0.0
        next_adj = adj_close[i + 1]
        denom = 1.0 + chg / 100.0
        if denom <= 0 or next_adj is None:
            # 異常 (-100%) 直接複製,避免除以零
            adj_close[i] = next_adj
        else:
            adj_close[i] = next_adj / denom

    # 計算每日 factor 並推 OHL
    updates = []
    for i, r in enumerate(rows):
        c = r['close_price']
        if c is None or c <= 0 or adj_close[i] is None:
            updates.append((None, None, None, None, stock_id, r['date']))
            continue
        f = adj_close[i] / c
        adj_o = r['open_price'] * f if r['open_price'] is not None else None
        adj_h = r['high_price'] * f if r['high_price'] is not None else None
        adj_l = r['low_price']  * f if r['low_price']  is not None else None
        updates.append((adj_o, adj_h, adj_l, adj_close[i], stock_id, r['date']))

    conn.executemany("""
        UPDATE daily_prices SET adj_open=?, adj_high=?, adj_low=?, adj_close=?
        WHERE stock_id=? AND date=?
    """, updates)
    return n


def main():
    start = time.time()
    with get_conn() as conn:
        stocks = [r['stock_id'] for r in conn.execute(
            "SELECT DISTINCT stock_id FROM daily_prices ORDER BY stock_id"
        ).fetchall()]
        logger.info(f"開始還原 {len(stocks)} 檔股票的 OHLC")

        total = 0
        for i, sid in enumerate(stocks, 1):
            try:
                n = adjust_one(conn, sid)
                total += n
            except Exception as e:
                logger.error(f"  {sid} ERROR: {e}")
            if i % 200 == 0:
                conn.commit()
                elapsed = time.time() - start
                rate = i / elapsed if elapsed > 0 else 0
                logger.info(f"  進度 {i}/{len(stocks)}  {total:,} rows updated  rate={rate:.0f} stocks/s")

        conn.commit()

    elapsed = time.time() - start
    logger.info(f"完成: {total:,} rows updated / {elapsed:.0f}s")


if __name__ == '__main__':
    main()
