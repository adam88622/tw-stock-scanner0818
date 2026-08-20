"""
即時報價獨立 worker — 由 watchdog 管理，與 Flask 完全分離。
盤中每 10 分鐘抓取全部股票即時報價 + 重算突破。
"""
import sys
import os
import time
import logging

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _PROJECT_ROOT)
os.chdir(_PROJECT_ROOT)

from models.database import init_db, get_conn
from scrapers.realtime import fetch_realtime_prices, is_trading_hours
from scanners.breakout import scan_breakouts
from datetime import datetime

logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)-5s %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
)
logger = logging.getLogger(__name__)

INTERVAL = 600  # 10 分鐘


def main():
    init_db()
    logger.info("[即時報價 worker] 啟動，每 %d 秒執行一次", INTERVAL)

    while True:
        try:
            if is_trading_hours():
                logger.info("[即時報價 worker] 開始抓取...")
                conn = get_conn()
                try:
                    count = fetch_realtime_prices(conn)
                    if count > 0:
                        today = datetime.now().strftime('%Y-%m-%d')
                        scan_breakouts(conn, today)
                        conn.commit()
                        logger.info(f"[即時報價 worker] 更新 {count} 筆，已重算突破")
                    else:
                        logger.info("[即時報價 worker] 無新資料")
                finally:
                    conn.close()
        except Exception as e:
            logger.error(f"[即時報價 worker] 錯誤: {e}")

        time.sleep(INTERVAL)


if __name__ == '__main__':
    main()
