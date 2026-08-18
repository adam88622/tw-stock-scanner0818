"""
volume_alert package — 首次安裝後執行此腳本，建立歷史資料基準。
抓取最近 N 個交易日的 TWSE + TPEx 個股 OHLCV，用於 ADV20 與爆量比較。
"""
import sys
import os
import logging

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import SEED_DAYS
from models.database import init_db, get_conn
from scrapers.seed import seed_recent_days

LOG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'log', 'seed_data.log')
os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)-5s %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    handlers=[logging.StreamHandler(), logging.FileHandler(LOG_FILE, encoding='utf-8')],
)
logger = logging.getLogger(__name__)


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else SEED_DAYS
    logger.info(f"=== 抓取最近 {n} 個交易日的歷史 OHLCV ===")

    init_db()
    conn = get_conn()
    try:
        seed_recent_days(conn, n)
    finally:
        conn.close()
    logger.info("=== seed 完成 ===")


if __name__ == '__main__':
    main()
