"""
全歷史 breakouts 重算（向量化版）
用 SQLite window function 一次算完所有 (stock_id, date, window) 組合，
比逐日呼叫 scan_breakouts 快 ~50 倍。

判定：close_price > MAX(high_price) over preceding N rows (不含今日)
"""
import os, sys, time, logging
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.chdir(os.path.dirname(os.path.abspath(__file__)))

from models.database import get_conn, init_db
from config import BREAKOUT_DAYS

os.makedirs('log', exist_ok=True)
log_file = f"log/{datetime.now().strftime('%Y%m%d-%H%M%S')}-backfill-breakouts-vec.log"
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)-5s %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(log_file, encoding='utf-8'),
        logging.FileHandler('backfill_breakouts.log', encoding='utf-8', mode='a'),
    ],
)
logger = logging.getLogger(__name__)


def main():
    init_db()
    conn = get_conn()

    logger.info("=" * 60)
    logger.info("向量化 breakouts 全歷史重算")
    logger.info(f"  窗口: {BREAKOUT_DAYS}")
    logger.info("=" * 60)

    # 先清空 breakouts（全歷史一次重算才正確）
    cnt_before = conn.execute("SELECT COUNT(*) FROM breakouts").fetchone()[0]
    logger.info(f"清空既有 breakouts ({cnt_before:,} 筆)")
    conn.execute("DELETE FROM breakouts")
    conn.commit()

    t0 = time.time()

    # 為每個窗口算 prev_max_high，得到該窗口的 breakout 旗標
    # 用 temp table 存中間結果
    logger.info("建立中間 temp table...")
    conn.execute("DROP TABLE IF EXISTS _bk_tmp")
    conn.execute("""
        CREATE TEMP TABLE _bk_tmp (
            stock_id TEXT,
            date TEXT,
            close_price REAL,
            change_pct REAL,
            break_5 INTEGER DEFAULT 0,
            break_10 INTEGER DEFAULT 0,
            break_20 INTEGER DEFAULT 0,
            break_60 INTEGER DEFAULT 0,
            break_120 INTEGER DEFAULT 0,
            break_240 INTEGER DEFAULT 0,
            PRIMARY KEY (stock_id, date)
        )
    """)
    conn.commit()

    # 一次填入所有 (stock_id, date, close, change_pct)，並計算各窗口 prev_max_high
    logger.info("計算各窗口 prev_max_high 並標記突破...")
    select_cols = []
    for n in BREAKOUT_DAYS:
        select_cols.append(
            f"CASE WHEN close_price > MAX(high_price) OVER ("
            f"PARTITION BY stock_id ORDER BY date "
            f"ROWS BETWEEN {n} PRECEDING AND 1 PRECEDING"
            f") THEN 1 ELSE 0 END AS break_{n}"
        )
    sql = f"""
        INSERT INTO _bk_tmp (stock_id, date, close_price, change_pct, {', '.join(f'break_{n}' for n in BREAKOUT_DAYS)})
        SELECT stock_id, date, close_price, change_pct,
               {', '.join(select_cols)}
        FROM daily_prices
        WHERE close_price IS NOT NULL
    """
    conn.execute(sql)
    conn.commit()
    elapsed = time.time() - t0
    n_tmp = conn.execute("SELECT COUNT(*) FROM _bk_tmp").fetchone()[0]
    logger.info(f"  → temp table 完成 {n_tmp:,} 筆，耗時 {elapsed:.1f}s")

    # 篩出有任一突破的列，寫入 breakouts
    logger.info("寫入最終 breakouts 表...")
    t1 = time.time()
    cond = ' OR '.join(f'break_{n}=1' for n in BREAKOUT_DAYS)
    conn.execute(f"""
        INSERT OR REPLACE INTO breakouts
        (stock_id, date, break_5, break_10, break_20, break_60, break_120, break_240, close_price, change_pct)
        SELECT stock_id, date, break_5, break_10, break_20, break_60, break_120, break_240,
               close_price, change_pct
        FROM _bk_tmp
        WHERE {cond}
    """)
    conn.commit()
    elapsed = time.time() - t1
    n_break = conn.execute("SELECT COUNT(*) FROM breakouts").fetchone()[0]
    logger.info(f"  → 寫入完成 {n_break:,} 筆，耗時 {elapsed:.1f}s")

    # 統計
    total_elapsed = timedelta(seconds=int(time.time() - t0))
    stats = conn.execute(f"""
        SELECT
            COUNT(*) total,
            COUNT(DISTINCT date) dates,
            COUNT(DISTINCT stock_id) stocks,
            SUM(break_5) b5, SUM(break_10) b10, SUM(break_20) b20,
            SUM(break_60) b60, SUM(break_120) b120, SUM(break_240) b240
        FROM breakouts
    """).fetchone()

    logger.info("=" * 60)
    logger.info(f"全歷史 breakouts 重算完成 — 總耗時 {total_elapsed}")
    logger.info(f"  總筆數: {stats['total']:,}")
    logger.info(f"  涵蓋日期: {stats['dates']:,}  涵蓋股票: {stats['stocks']:,}")
    logger.info(f"  break_5={stats['b5']:,}  break_10={stats['b10']:,}  break_20={stats['b20']:,}")
    logger.info(f"  break_60={stats['b60']:,}  break_120={stats['b120']:,}  break_240={stats['b240']:,}")
    logger.info("=" * 60)
    conn.close()


if __name__ == '__main__':
    main()
