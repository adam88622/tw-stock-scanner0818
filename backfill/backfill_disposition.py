"""
Backfill 注意/處置股公告

用法:
  python backfill_disposition.py                  # 抓最近 90 天
  python backfill_disposition.py --days 365       # 抓最近 1 年
  python backfill_disposition.py --start 20240101 --end 20260505

TWSE 對日期區間沒上限,但建議分段(每段 ≤ 90 天)以免單次 response 太大。
"""
import os
import sys

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _PROJECT_ROOT)
os.chdir(_PROJECT_ROOT)
import argparse
import logging
import time
from datetime import datetime, timedelta

from models.database import init_db, get_conn, upsert_notice, upsert_disposition
from scrapers.disposition import fetch_notice, fetch_punish

logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)-5s %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
)
logger = logging.getLogger(__name__)


def date_chunks(start, end, chunk_days=60):
    """切成 chunk_days 為單位的區間"""
    cur = start
    while cur <= end:
        nxt = min(cur + timedelta(days=chunk_days - 1), end)
        yield (cur, nxt)
        cur = nxt + timedelta(days=1)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=90,
                        help="抓最近 N 天 (預設 90)")
    parser.add_argument("--start", type=str, default=None,
                        help="起始日期 YYYYMMDD")
    parser.add_argument("--end", type=str, default=None,
                        help="結束日期 YYYYMMDD")
    args = parser.parse_args()

    if args.start and args.end:
        start = datetime.strptime(args.start, "%Y%m%d").date()
        end = datetime.strptime(args.end, "%Y%m%d").date()
    else:
        end = datetime.now().date()
        start = end - timedelta(days=args.days)

    init_db()
    logger.info(f"日期區間: {start} ~ {end}")

    notice_total = 0
    disp_total = 0

    for chunk_start, chunk_end in date_chunks(start, end, chunk_days=60):
        s = chunk_start.strftime("%Y%m%d")
        e = chunk_end.strftime("%Y%m%d")
        logger.info(f"抓 chunk {s} ~ {e}")

        notices = fetch_notice(s, e)
        punishes = fetch_punish(s, e)

        with get_conn() as conn:
            for rec in notices:
                upsert_notice(conn, rec)
            for rec in punishes:
                upsert_disposition(conn, rec)
            conn.commit()

        notice_total += len(notices)
        disp_total += len(punishes)
        logger.info(f"  notice: {len(notices)} 筆, punish: {len(punishes)} 筆")
        time.sleep(1)  # 禮貌性 throttle

    logger.info(f"=== 完成 ===")
    logger.info(f"注意股總計: {notice_total} 筆")
    logger.info(f"處置股總計: {disp_total} 筆")

    # 驗證
    with get_conn() as conn:
        n_real = conn.execute(
            "SELECT COUNT(*) FROM notice_announcements WHERE is_real_stock=1"
        ).fetchone()[0]
        d_real = conn.execute(
            "SELECT COUNT(*) FROM disposition_announcements WHERE is_real_stock=1"
        ).fetchone()[0]
    logger.info(f"DB 中真股票 notice={n_real}, disposition={d_real}")


if __name__ == "__main__":
    main()
