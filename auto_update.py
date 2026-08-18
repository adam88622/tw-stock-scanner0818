"""
開機自動檢查 + 補抓當日資料
如果今天的資料還沒抓，就自動抓
"""
import sys
import os
import logging
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from utils import acquire_lock, release_lock
from models.database import init_db, get_conn, get_latest_date

logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)-5s %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
)
logger = logging.getLogger(__name__)


def main():
    try:
        init_db()
    except Exception as e:
        logger.error(f"資料庫初始化失敗: {e}")
        return

    today = datetime.now()

    # 週末不抓
    if today.weekday() >= 5:
        logger.info("今天是週末，跳過")
        return

    today_str = today.strftime('%Y-%m-%d')
    today_yyyymmdd = today.strftime('%Y%m%d')
    now_hour = today.hour

    try:
        conn = get_conn()
        latest = get_latest_date(conn)
        conn.close()
    except Exception as e:
        logger.error(f"無法讀取 DB 最新日期: {e}")
        latest = None

    logger.info(f"今天: {today_str}，DB 最新: {latest}，現在: {now_hour}:00")

    # 08:30 後：預抓大盤籌碼，確保盤前報告資料新鮮
    if now_hour >= 8:
        logger.info("預抓大盤籌碼（盤前報告用）...")
        try:
            from scrapers.market import fetch_futures_oi, fetch_put_call_ratio
            fetch_futures_oi(days=20)
            fetch_put_call_ratio(days=20)
            logger.info("盤前籌碼資料已更新")
        except Exception as e:
            logger.warning(f"盤前籌碼抓取失敗（非致命）: {e}")

    # 14:00 後，如果今天收盤資料還沒抓
    if now_hour >= 14 and latest != today_str:
        try:
            logger.info("補抓今日收盤 + 突破掃描...")
            from run_daily import run_closing
            run_closing(today_yyyymmdd)
        except Exception as e:
            logger.error(f"收盤資料補抓失敗（非致命）: {e}")

    # 18:00 後，補抓法人
    if now_hour >= 18:
        try:
            conn = get_conn()
            inst_today = conn.execute(
                "SELECT COUNT(*) as c FROM institutional WHERE date=?", (today_str,)
            ).fetchone()
            conn.close()
            if inst_today['c'] == 0:
                logger.info("補抓今日法人買賣超...")
                from run_daily import run_institutional
                run_institutional(today_yyyymmdd)
        except Exception as e:
            logger.error(f"法人資料補抓失敗（非致命）: {e}")

    # 16:00 後，補抓期貨大額交易人（個股期大戶淨部位）；電腦關機數日也能一次補齊
    if now_hour >= 16:
        try:
            conn = get_conn()
            row = conn.execute("SELECT MAX(date) AS d FROM futures_large_trader").fetchone()
            conn.close()
            last_lt = row['d'] if row else None
            if last_lt != today_str:
                from run_daily import run_large_trader
                if last_lt:
                    # 從最後一筆的隔天補到今天（期交所可查區間，非交易日自動略過）
                    start = (datetime.strptime(last_lt, '%Y-%m-%d') + timedelta(days=1)).strftime('%Y%m%d')
                    logger.info(f"補抓期貨大額交易人 {start}~{today_yyyymmdd}...")
                    run_large_trader(start, today_yyyymmdd)
                else:
                    logger.info("期貨大額交易人尚無資料，補抓近 30 天...")
                    start = (today - timedelta(days=30)).strftime('%Y%m%d')
                    run_large_trader(start, today_yyyymmdd)
        except Exception as e:
            logger.error(f"期貨大額交易人補抓失敗（非致命）: {e}")

    # 20:00 後，補抓分點
    if now_hour >= 20:
        try:
            conn = get_conn()
            broker_today = conn.execute(
                "SELECT COUNT(*) as c FROM broker_trades WHERE date=?", (today_str,)
            ).fetchone()
            conn.close()
            if broker_today['c'] == 0:
                logger.info("補抓今日券商分點...")
                from run_daily import run_broker
                run_broker()
        except Exception as e:
            logger.error(f"券商分點補抓失敗（非致命）: {e}")

    logger.info("自動更新檢查完成")


if __name__ == '__main__':
    if not acquire_lock('auto_update'):
        print("Another instance is running, skipping.")
        sys.exit(0)
    try:
        main()
    finally:
        release_lock('auto_update')
