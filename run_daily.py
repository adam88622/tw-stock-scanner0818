"""
每日排程入口
用法:
  python run_daily.py              # 14:00 用：抓收盤價 + 突破掃描 + 市場體溫
  python run_daily.py institutional # 18:00 用：抓法人買賣超
  python run_daily.py broker       # 20:00 用：抓券商分點進出
  python run_daily.py market       # 17:00 用：預抓大盤籌碼（期貨/選擇權）
  python run_daily.py regime       # 隨時可跑：更新市場體溫（SPY/VIX 滾動資料）
  python run_daily.py realtime     # 盤中：即時報價 + 突破掃描（每5分鐘）
  python run_daily.py 20260319     # 指定日期（完整：收盤+法人+突破）
  python run_daily.py 20260301 20260319  # 回補日期範圍
"""
import sys
import time
import logging
from datetime import datetime, timedelta

# 加入專案根目錄到 path
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from utils import acquire_lock, release_lock
from models.database import init_db, get_conn
from scrapers.twse import fetch_twse_daily, fetch_twse_institutional
from scrapers.tpex import fetch_tpex_daily, fetch_tpex_institutional
from scanners.breakout import scan_breakouts
try:
    from scanners.regime import get_market_temperature, update_regime_db
    HAS_REGIME = True
except ImportError:
    HAS_REGIME = False

logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)-5s %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
)
logger = logging.getLogger(__name__)


def run_closing(date_str):
    """
    14:00 排程：抓收盤價 + 突破掃描（不含法人）。
    """
    iso_date = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"
    logger.info(f"===== [收盤] 開始處理 {iso_date} =====")

    conn = get_conn()
    try:
        logger.info("抓取上市收盤資料...")
        twse_count = fetch_twse_daily(conn, date_str)
        conn.commit()
        time.sleep(3)

        logger.info("抓取上櫃收盤資料...")
        tpex_count = fetch_tpex_daily(conn, date_str)
        conn.commit()

        if twse_count == 0 and tpex_count == 0:
            logger.info(f"{iso_date} 無資料（可能為非交易日），跳過")
            return False

        logger.info("執行突破掃描...")
        scan_breakouts(conn, iso_date)
        conn.commit()

        # 更新市場體溫（滾動取最新 SPY/VIX 資料）
        if HAS_REGIME:
            try:
                logger.info("更新市場體溫...")
                result = update_regime_db(conn)
                logger.info(f"市場體溫: {result['temperature']}° ({result['regime']})")
            except Exception as e:
                logger.warning(f"市場體溫更新失敗（不影響主流程）: {e}")

        logger.info(f"===== [收盤] {iso_date} 完成 =====")
        return True
    except Exception as e:
        conn.rollback()
        logger.error(f"[收盤] {iso_date} 錯誤: {e}", exc_info=True)
        return False
    finally:
        conn.close()


def run_institutional(date_str):
    """
    18:00 排程：抓法人買賣超。
    """
    iso_date = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"
    logger.info(f"===== [法人] 開始處理 {iso_date} =====")

    conn = get_conn()
    try:
        logger.info("抓取上市法人買賣超...")
        fetch_twse_institutional(conn, date_str)
        conn.commit()
        time.sleep(3)

        logger.info("抓取上櫃法人買賣超...")
        fetch_tpex_institutional(conn, date_str)
        conn.commit()

        logger.info(f"===== [法人] {iso_date} 完成 =====")
        return True
    except Exception as e:
        conn.rollback()
        logger.error(f"[法人] {iso_date} 錯誤: {e}", exc_info=True)
        return False
    finally:
        conn.close()


def run_for_date(date_str):
    """
    完整抓取：收盤 + 法人 + 突破（回補用）。
    """
    iso_date = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"
    logger.info(f"===== 開始處理 {iso_date} =====")

    conn = get_conn()
    try:
        logger.info("抓取上市收盤資料...")
        twse_count = fetch_twse_daily(conn, date_str)
        conn.commit()
        time.sleep(3)

        logger.info("抓取上櫃收盤資料...")
        tpex_count = fetch_tpex_daily(conn, date_str)
        conn.commit()

        if twse_count == 0 and tpex_count == 0:
            logger.info(f"{iso_date} 無資料（可能為非交易日），跳過")
            return False

        time.sleep(3)

        logger.info("抓取上市法人買賣超...")
        fetch_twse_institutional(conn, date_str)
        conn.commit()
        time.sleep(3)

        logger.info("抓取上櫃法人買賣超...")
        fetch_tpex_institutional(conn, date_str)
        conn.commit()

        logger.info("執行突破掃描...")
        scan_breakouts(conn, iso_date)
        conn.commit()

        logger.info(f"===== {iso_date} 處理完成 =====")
        return True
    except Exception as e:
        conn.rollback()
        logger.error(f"處理 {iso_date} 時發生錯誤: {e}", exc_info=True)
        return False
    finally:
        conn.close()


def run_realtime():
    """盤中即時：抓即時報價 + 突破掃描。"""
    from scrapers.realtime import fetch_realtime_prices

    today = datetime.now().strftime('%Y-%m-%d')
    logger.info(f"===== 盤中即時更新 {today} =====")

    conn = get_conn()
    try:
        count = fetch_realtime_prices(conn)
        if count > 0:
            logger.info("重新計算突破掃描...")
            scan_breakouts(conn, today)
            conn.commit()
            logger.info(f"盤中即時更新完成: {count} 筆報價，已更新突破掃描")
        else:
            logger.info("無即時報價資料（可能非交易時段）")
    except Exception as e:
        conn.rollback()
        logger.error(f"盤中即時更新錯誤: {e}")
    finally:
        conn.close()


def run_broker(date_str=None):
    """
    20:00 排程：抓券商分點進出。
    date_str: YYYYMMDD 格式，不傳則用今天
    """
    from scrapers.broker import fetch_all_brokers

    if date_str:
        iso_date = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:]}"
    else:
        iso_date = datetime.now().strftime('%Y-%m-%d')
    logger.info(f"===== [券商分點] 開始處理 {iso_date} =====")

    conn = get_conn()
    try:
        fetch_all_brokers(conn, iso_date)
        logger.info(f"===== [券商分點] {iso_date} 完成 =====")
        return True
    except Exception as e:
        conn.rollback()
        logger.error(f"[券商分點] {iso_date} 錯誤: {e}", exc_info=True)
        return False
    finally:
        conn.close()


def run_market():
    """
    17:00 排程：預抓大盤籌碼（期貨/選擇權法人未平倉、P/C Ratio）。
    呼叫後會更新快取，讓網頁載入更快。
    """
    from scrapers.market import fetch_futures_oi, fetch_retail_ratio, fetch_put_call_ratio

    logger.info("===== [大盤籌碼] 開始抓取 =====")
    try:
        futures = fetch_futures_oi(days=60)
        logger.info(f"台指期未平倉: {len(futures)} 筆")

        retail = fetch_retail_ratio(days=60)
        logger.info(f"微台指法人淨額: {len(retail)} 筆")

        pc = fetch_put_call_ratio(days=60)
        logger.info(f"Put/Call Ratio: {len(pc)} 筆")

        logger.info("===== [大盤籌碼] 完成 =====")
    except Exception as e:
        logger.error(f"[大盤籌碼] 錯誤: {e}", exc_info=True)


def run_regime():
    """更新市場體溫（從 Yahoo Finance 滾動取最新 SPY/VIX 資料）。"""
    if not HAS_REGIME:
        logger.error("regime 模組未安裝，請確認 regime-detector 路徑")
        return False

    logger.info("===== [市場體溫] 開始更新 =====")
    conn = get_conn()
    try:
        result = update_regime_db(conn)
        logger.info(f"體溫: {result['temperature']}° | 狀態: {result['regime']}")
        logger.info(f"重建誤差: {result['current_error']:.4f} | 閾值 τ: {result['tau']:.4f}")
        logger.info(f"資料日期: {result['latest_date']}")
        logger.info(f"近 30 天異常天數: {sum(1 for h in result['history'] if h['regime']=='abnormal')}")
        logger.info("===== [市場體溫] 完成 =====")
        return True
    except Exception as e:
        logger.error(f"[市場體溫] 錯誤: {e}", exc_info=True)
        return False
    finally:
        conn.close()


def backfill(start_str, end_str):
    """回補日期範圍（完整：收盤+法人+突破）。"""
    start = datetime.strptime(start_str, '%Y%m%d')
    end = datetime.strptime(end_str, '%Y%m%d')
    current = start

    success_count = 0
    while current <= end:
        if current.weekday() < 5:
            date_str = current.strftime('%Y%m%d')
            try:
                if run_for_date(date_str):
                    success_count += 1
                time.sleep(5)
            except Exception as e:
                logger.error(f"回補 {date_str} 失敗: {e}")
        current += timedelta(days=1)

    logger.info(f"回補完成，成功 {success_count} 個交易日")


def main():
    init_db()

    try:
        if len(sys.argv) == 1:
            # 無參數 = 14:00 排程：收盤 + 突破
            today = datetime.now().strftime('%Y%m%d')
            run_closing(today)
        elif sys.argv[1] == 'institutional':
            # 18:00 排程：法人
            today = datetime.now().strftime('%Y%m%d')
            run_institutional(today)
        elif sys.argv[1] == 'broker':
            # 20:00 排程：券商分點
            run_broker()
        elif sys.argv[1] == 'market':
            # 17:00 排程：大盤籌碼
            run_market()
        elif sys.argv[1] == 'realtime':
            # 盤中即時
            run_realtime()
        elif sys.argv[1] == 'regime':
            # 市場體溫（隨時可跑）
            run_regime()
        elif len(sys.argv) == 2:
            # 指定日期（完整）
            run_for_date(sys.argv[1])
        elif len(sys.argv) == 3:
            # 日期範圍回補
            backfill(sys.argv[1], sys.argv[2])
        else:
            print("用法:")
            print("  python run_daily.py              # 14:00 收盤+突破掃描+市場體溫")
            print("  python run_daily.py institutional # 18:00 法人買賣超")
            print("  python run_daily.py market       # 17:00 大盤籌碼（期貨/選擇權）")
            print("  python run_daily.py broker       # 20:00 券商分點進出")
            print("  python run_daily.py regime       # 更新市場體溫（SPY/VIX 滾動）")
            print("  python run_daily.py realtime     # 盤中即時（每5分鐘）")
            print("  python run_daily.py 20260319     # 指定日期（完整）")
            print("  python run_daily.py 20250401 20260318  # 回補範圍")
    except Exception as e:
        logger.critical(f"排程主流程未預期錯誤: {e}", exc_info=True)
        sys.exit(1)


if __name__ == '__main__':
    if not acquire_lock('run_daily'):
        print("Another instance is running, skipping.")
        sys.exit(0)
    try:
        main()
    finally:
        release_lock('run_daily')
