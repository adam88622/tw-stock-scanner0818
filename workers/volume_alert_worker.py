"""
盤中爆量預估 worker
每 2 分鐘：
  1. 抓最新即時報價（同時寫入 intraday_snapshot 保留歷史）
  2. 跑 scan_volume_anomaly()
  3. 結果序列化後寫入 volume_anomaly_cache，供 /volume-alert 頁面讀取
盤外閒置。獨立於 realtime_worker.py（不影響 production）。
"""
import sys
import os
import json
import time
import logging
from datetime import datetime

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _PROJECT_ROOT)
os.chdir(_PROJECT_ROOT)

from models.database import init_db, get_conn
from scrapers.realtime import fetch_realtime_prices, is_trading_hours
from scanners.volume_anomaly import scan_volume_anomaly

LOG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'volume_alert_worker.log')
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)-5s %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    handlers=[logging.StreamHandler(), logging.FileHandler(LOG_FILE, encoding='utf-8')],
)
logger = logging.getLogger(__name__)

INTERVAL = 300  # 5 分鐘（TWSE MIS 對短連續打擋很兇，5 分鐘是安全閾值）


def should_exit():
    """非交易日或 ≥13:30 自動結束（用工作排程在 09:00 重啟）"""
    if os.environ.get('VOLUME_ALERT_FORCE_INTRADAY', '').strip() not in ('', '0', 'false', 'False'):
        return False
    now = datetime.now()
    if now.weekday() >= 5:
        return True
    return (now.hour * 100 + now.minute) >= 1330


def _save_cache(conn, payload):
    """寫入單 row cache 表"""
    conn.execute("""
        INSERT INTO volume_anomaly_cache (id, payload, updated_at)
        VALUES (1, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(id) DO UPDATE SET
            payload=excluded.payload,
            updated_at=CURRENT_TIMESTAMP
    """, (json.dumps(payload, ensure_ascii=False, default=str),))
    conn.commit()


def _save_trend(conn, payload):
    """盤中時把當下 taiex 結果 upsert 進 taiex_trend（盤前/盤後不寫）"""
    taiex = payload.get('taiex') or {}
    # 盤外 level == NONE 且 forecast 為 0 時跳過
    if taiex.get('level') == 'NONE' and not taiex.get('forecast_eod_value'):
        return
    conn.execute("""
        INSERT OR REPLACE INTO taiex_trend
        (snapshot_ts, minute_idx, rvol_forecast, forecast_eod_value, level, ci_low, ci_high)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (
        payload.get('as_of'),
        payload.get('minute_idx'),
        taiex.get('rvol_forecast'),
        taiex.get('forecast_eod_value'),
        taiex.get('level'),
        taiex.get('ci_low'),
        taiex.get('ci_high'),
    ))
    conn.commit()


def run_once():
    """執行一次：抓報價→寫 snapshot→掃描→更新 cache"""
    t0 = time.monotonic()
    conn = get_conn()
    try:
        if is_trading_hours():
            logger.info("盤中執行：抓即時報價 + 寫 snapshot")
            t_fetch = time.monotonic()
            n = fetch_realtime_prices(conn, record_snapshot=True)
            logger.info(f"更新 {n} 筆即時報價（fetch 耗時 {time.monotonic()-t_fetch:.1f}s）")
        else:
            logger.info("盤外：跳過抓取，仍掃描一次以更新 cache")

        result = scan_volume_anomaly(conn)
        _save_cache(conn, result)
        # 盤中才寫入 trend 表（盤前/盤後不寫）
        if is_trading_hours():
            _save_trend(conn, result)
        logger.info(
            "cache 更新: minute_idx=%s, pct_done=%s, 異常股=%d, 加權=%s（total %.1fs）",
            result['minute_idx'], result['pct_done'],
            len(result['stocks']), result['taiex']['level'],
            time.monotonic() - t0,
        )
    except Exception as e:
        logger.error(f"執行錯誤: {e}", exc_info=True)
    finally:
        conn.close()


def main():
    init_db()
    logger.info("[volume_alert worker] 啟動，每 %d 秒執行一次（13:30 自動結束）", INTERVAL)

    while True:
        if should_exit():
            logger.info("已過 13:30（或非交易日），worker 自動結束")
            return
        try:
            run_once()
        except Exception as e:
            logger.error(f"主迴圈錯誤: {e}", exc_info=True)
        # 用 1s 切片睡眠，確保 13:30 能即時收尾
        for _ in range(INTERVAL):
            if should_exit():
                break
            time.sleep(1)


if __name__ == '__main__':
    main()
