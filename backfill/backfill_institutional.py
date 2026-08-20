"""
回填三大法人買賣超 — 從 API 最早日期一路回補到今天
- TWSE 上市最早: 2012-05-02
- TPEx 上櫃最早: 2018-01-15

特性:
- 只跑 institutional（不抓收盤、不算突破），最快路徑
- 自動跳過 institutional 表內已存在的日期
- 單一 commit per date，跑壞中斷可以接續
- TWSE 5s 間隔、TPEx 3s 間隔（避免 IP 被擋）
- 全程 log 到 backfill_institutional.log + log/

用法:
    python backfill_institutional.py              # 從各自最早日期跑到今天
    python backfill_institutional.py twse         # 只跑 TWSE
    python backfill_institutional.py tpex         # 只跑 TPEx
    python backfill_institutional.py 20200101     # 從指定日期開始（兩個都跑）
"""
import sys
import os
import time
import logging
import sqlite3
from datetime import datetime, timedelta

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _PROJECT_ROOT)
os.chdir(_PROJECT_ROOT)

from models.database import init_db, get_conn
from scrapers.twse import fetch_twse_institutional
from scrapers.tpex import fetch_tpex_institutional

# API 最早可抓日期（已實測二分搜尋確認）
TWSE_EARLIEST = '20120502'
TPEX_EARLIEST = '20180115'

# 請求間隔（秒）
TWSE_DELAY = 5
TPEX_DELAY = 3

# Log 設定
os.makedirs('log', exist_ok=True)
log_filename = f"log/{datetime.now().strftime('%Y%m%d-%H%M%S')}-backfill-institutional.log"
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)-5s %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(log_filename, encoding='utf-8'),
        logging.FileHandler('backfill_institutional.log', encoding='utf-8', mode='a'),
    ],
)
logger = logging.getLogger(__name__)


def get_existing_dates_by_market(conn, market):
    """取得指定 market 已有的 institutional 日期集合"""
    rows = conn.execute("""
        SELECT DISTINCT i.date
        FROM institutional i
        JOIN stocks s ON s.stock_id = i.stock_id
        WHERE s.market = ?
    """, (market,)).fetchall()
    return set(r['date'] for r in rows)


def iter_business_days(start_str, end_str):
    """產生 start~end 之間所有平日 YYYYMMDD"""
    start = datetime.strptime(start_str, '%Y%m%d')
    end = datetime.strptime(end_str, '%Y%m%d')
    cur = start
    while cur <= end:
        if cur.weekday() < 5:
            yield cur.strftime('%Y%m%d')
        cur += timedelta(days=1)


def backfill_market(market, fetcher, earliest, delay, start_override=None, end_str=None):
    """
    回補單一市場的法人資料
    market: 'twse' | 'tpex'
    fetcher: fetch_twse_institutional 或 fetch_tpex_institutional
    earliest: 該市場 API 最早日期 'YYYYMMDD'
    delay: 每筆請求後等待秒數
    """
    conn = get_conn()

    if end_str is None:
        end_str = datetime.now().strftime('%Y%m%d')

    start_str = start_override or earliest
    if start_str < earliest:
        logger.warning(f"[{market.upper()}] 起始日 {start_str} 早於 API 上限 {earliest}，改用 {earliest}")
        start_str = earliest

    existing = get_existing_dates_by_market(conn, market)
    logger.info(f"[{market.upper()}] DB 已有 {len(existing)} 個日期")

    all_days = list(iter_business_days(start_str, end_str))
    todo = [d for d in all_days
            if f"{d[:4]}-{d[4:6]}-{d[6:8]}" not in existing]

    skipped = len(all_days) - len(todo)
    logger.info(f"[{market.upper()}] 範圍 {start_str}~{end_str}：總平日 {len(all_days)}、已有 {skipped}、待補 {len(todo)}")

    if not todo:
        logger.info(f"[{market.upper()}] 無待補日期，跳過")
        conn.close()
        return 0, 0

    success = 0
    empty = 0
    failed = 0
    estimated_secs = len(todo) * delay
    eta = timedelta(seconds=estimated_secs)
    logger.info(f"[{market.upper()}] 開始回補，預估需時 {eta}（不含實際處理時間）")

    for i, date_str in enumerate(todo, 1):
        iso = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"
        try:
            n = fetcher(conn, date_str)
            conn.commit()
            if n > 0:
                success += 1
                if i % 20 == 0 or i == 1 or i == len(todo):
                    logger.info(f"[{market.upper()}] {i}/{len(todo)} {iso} OK ({n} rows)  累計 OK={success} 空={empty} 失敗={failed}")
            else:
                empty += 1
                # 假日 / 沒掛牌 都會回 0，不用報警
        except Exception as e:
            failed += 1
            logger.error(f"[{market.upper()}] {i}/{len(todo)} {iso} 失敗: {e}")
            conn.rollback()
        time.sleep(delay)

    logger.info(f"[{market.upper()}] 完成: OK={success}, 空={empty}, 失敗={failed}")
    conn.close()
    return success, failed


def prevent_sleep():
    """Windows: 防止系統睡眠 / 螢幕關閉（不影響使用者操作）"""
    try:
        import ctypes
        ES_CONTINUOUS = 0x80000000
        ES_SYSTEM_REQUIRED = 0x00000001
        ES_AWAYMODE_REQUIRED = 0x00000040
        ctypes.windll.kernel32.SetThreadExecutionState(
            ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_AWAYMODE_REQUIRED
        )
        logger.info("已啟動防睡眠模式（執行期間系統不會休眠）")
    except Exception as e:
        logger.warning(f"無法設定防睡眠（非 Windows 或權限不足）: {e}")


def restore_sleep():
    try:
        import ctypes
        ES_CONTINUOUS = 0x80000000
        ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS)
    except Exception:
        pass


def main():
    init_db()
    prevent_sleep()

    args = sys.argv[1:]
    only_market = None
    start_override = None

    for a in args:
        if a in ('twse', 'tpex'):
            only_market = a
        elif len(a) == 8 and a.isdigit():
            start_override = a
        else:
            logger.warning(f"無法辨識的參數: {a}（忽略）")

    logger.info("=" * 60)
    logger.info("法人買賣超回補開始")
    logger.info(f"  目標市場: {only_market or 'twse + tpex'}")
    logger.info(f"  起始日: {start_override or '各市場 API 最早日'}")
    logger.info(f"  日誌: {log_filename}")
    logger.info("=" * 60)

    t0 = time.time()
    total_ok = 0
    total_fail = 0

    if only_market in (None, 'twse'):
        ok, fail = backfill_market('twse', fetch_twse_institutional,
                                    TWSE_EARLIEST, TWSE_DELAY, start_override)
        total_ok += ok
        total_fail += fail

    if only_market in (None, 'tpex'):
        ok, fail = backfill_market('tpex', fetch_tpex_institutional,
                                    TPEX_EARLIEST, TPEX_DELAY, start_override)
        total_ok += ok
        total_fail += fail

    elapsed = timedelta(seconds=int(time.time() - t0))
    logger.info("=" * 60)
    logger.info(f"回補完成 — 成功 {total_ok} 個交易日 / 失敗 {total_fail} 個 / 總耗時 {elapsed}")
    logger.info("=" * 60)

    # 自動接續匯出
    logger.info("自動觸發 export_institutional.py ...")
    try:
        import subprocess
        result = subprocess.run(
            [sys.executable,
             os.path.join(os.path.dirname(os.path.abspath(__file__)), 'export_institutional.py'),
             '--no-csv'],
            capture_output=True, text=True, encoding='utf-8'
        )
        if result.returncode == 0:
            logger.info("匯出完成:\n" + (result.stdout or '')[-2000:])
        else:
            logger.error(f"匯出失敗 (rc={result.returncode}):\n{result.stderr}")
    except Exception as e:
        logger.error(f"觸發匯出失敗: {e}")

    logger.info("=" * 60)
    logger.info("ALL DONE — 資料包: data/institutional_full.parquet")
    logger.info("=" * 60)
    restore_sleep()


if __name__ == '__main__':
    main()
