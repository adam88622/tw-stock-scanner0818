"""
volume_alert 系統盤前健檢（08:55 由工作排程觸發）
檢查項目：
  1. 資料庫連線 + volume_anomaly_cache 表可寫
  2. realtime 報價來源連通（抓 1 檔測試）
  3. scanners.volume_anomaly 可正常 import 與呼叫
失敗時 log 並以非 0 退出，方便工作排程 / 監控發現問題。
"""
import sys
import os
import logging
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

LOG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'health_check_volume_alert.log')
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)-5s %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    handlers=[logging.StreamHandler(), logging.FileHandler(LOG_FILE, encoding='utf-8')],
)
logger = logging.getLogger(__name__)


def check_db():
    from models.database import init_db, get_conn
    init_db()
    conn = get_conn()
    try:
        cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='volume_anomaly_cache'")
        if cur.fetchone() is None:
            return False, "volume_anomaly_cache 表不存在"
        cur = conn.execute("SELECT updated_at FROM volume_anomaly_cache WHERE id=1")
        row = cur.fetchone()
        last = row[0] if row else 'N/A'
        return True, f"DB OK（cache 最後更新: {last}）"
    finally:
        conn.close()


def check_realtime_source():
    import time as _t
    import requests
    from config import REQUEST_HEADERS, REQUEST_TIMEOUT
    session = requests.Session()
    session.headers.update(REQUEST_HEADERS)
    try:
        session.get('https://mis.twse.com.tw/stock/index.jsp', timeout=10)
    except Exception:
        pass

    last_err = None
    for attempt in range(3):
        try:
            r = session.get(
                'https://mis.twse.com.tw/stock/api/getStockInfo.jsp',
                params={'ex_ch': 'tse_2330.tw', 'json': 1, 'delay': 0},
                timeout=REQUEST_TIMEOUT,
            )
            if r.status_code != 200:
                last_err = f"HTTP {r.status_code}"
                _t.sleep(5)
                continue
            data = r.json()
            if not data.get('msgArray'):
                last_err = f"空回傳（rtcode={data.get('rtcode')}）"
                _t.sleep(5)
                continue
            return True, f"MIS 報價來源 OK（2330 可取，attempt {attempt + 1}）"
        except Exception as e:
            last_err = str(e)
            _t.sleep(5)
    return False, f"MIS 連線失敗（3 次）: {last_err}"


def check_scanner():
    from models.database import get_conn
    from scanners.volume_anomaly import scan_volume_anomaly
    conn = get_conn()
    try:
        result = scan_volume_anomaly(conn)
        if 'stocks' not in result or 'taiex' not in result:
            return False, f"scanner 回傳格式異常: keys={list(result.keys())}"
        return True, f"scanner OK（minute_idx={result.get('minute_idx')}）"
    finally:
        conn.close()


def main():
    logger.info("=== volume_alert 盤前健檢開始 ===")
    checks = [
        ("資料庫", check_db),
        ("MIS 報價源", check_realtime_source),
        ("掃描模組", check_scanner),
    ]
    failures = []
    for name, fn in checks:
        try:
            ok, msg = fn()
            if ok:
                logger.info(f"[PASS] {name}: {msg}")
            else:
                logger.error(f"[FAIL] {name}: {msg}")
                failures.append((name, msg))
        except Exception as e:
            logger.error(f"[FAIL] {name}: 例外 {e}", exc_info=True)
            failures.append((name, str(e)))

    if failures:
        logger.error(f"=== 健檢失敗 {len(failures)} 項 ===")
        for name, msg in failures:
            logger.error(f"  - {name}: {msg}")
        sys.exit(1)
    logger.info("=== 健檢全部通過 ===")


if __name__ == '__main__':
    main()
