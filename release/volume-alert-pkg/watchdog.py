"""
volume_alert package — 守護程序
管理 Flask + volume_alert_worker，每 30 秒檢查、掛了重啟。
volume_alert_worker 只在平日 09:00–13:30 拉起來。
"""
import subprocess
import socket
import time
import os
import sys
import logging
from datetime import datetime
import requests

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_FILE = os.path.join(BASE_DIR, 'log', 'watchdog.log')
os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)-5s %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    handlers=[logging.StreamHandler(), logging.FileHandler(LOG_FILE, encoding='utf-8')],
)
logger = logging.getLogger(__name__)

PYTHON = sys.executable  # 使用啟動 watchdog 的同一個 python（通常是 venv）
DETACHED = 0x00000008    # Windows DETACHED_PROCESS
PORT = 5000


def is_flask_running():
    try:
        r = requests.get(f"http://127.0.0.1:{PORT}/", timeout=5, allow_redirects=True)
        return r.status_code in (200, 302)
    except Exception:
        return False


def _port_in_use():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(('127.0.0.1', PORT)) == 0


def start_flask():
    if _port_in_use():
        logger.info(f"Port {PORT} 已被佔用，跳過啟動")
        return
    logger.info("啟動 Flask...")
    subprocess.Popen(
        [PYTHON, "app.py"],
        cwd=BASE_DIR,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=DETACHED,
    )
    for _ in range(15):
        time.sleep(1)
        if is_flask_running():
            logger.info(f"Flask 啟動成功 (port {PORT})")
            return
    logger.error("Flask 啟動失敗（15 秒超時）")


def start_volume_alert_worker():
    logger.info("啟動爆量預估 worker...")
    subprocess.Popen(
        [PYTHON, "volume_alert_worker.py"],
        cwd=BASE_DIR,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=DETACHED,
    )
    logger.info("爆量預估 worker 已啟動")


def _in_volume_alert_window():
    """平日 09:00–13:30"""
    now = datetime.now()
    if now.weekday() >= 5:
        return False
    hm = now.hour * 100 + now.minute
    return 900 <= hm < 1330


def _is_volume_alert_running():
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
             "Where-Object { $_.CommandLine -like '*volume_alert_worker*' } | "
             "Measure-Object | Select-Object -ExpandProperty Count"],
            capture_output=True, text=True, timeout=10)
        return int(r.stdout.strip() or '0') > 0
    except Exception:
        return False


def main():
    logger.info("=== volume_alert watchdog 啟動 ===")

    if not is_flask_running():
        start_flask()
    else:
        logger.info("Flask 已在運行")

    if _in_volume_alert_window() and not _is_volume_alert_running():
        start_volume_alert_worker()

    worker_check_counter = 0
    while True:
        time.sleep(30)
        try:
            if not is_flask_running():
                logger.warning("Flask 掛了，重啟...")
                start_flask()

            worker_check_counter += 1
            if worker_check_counter >= 10:  # 每 5 分鐘檢查一次 worker
                worker_check_counter = 0
                if _in_volume_alert_window() and not _is_volume_alert_running():
                    logger.warning("爆量預估 worker 掛了，重啟...")
                    start_volume_alert_worker()
        except Exception as e:
            logger.error(f"監控迴圈例外（不中斷）: {e}")


if __name__ == '__main__':
    while True:
        try:
            main()
        except KeyboardInterrupt:
            logger.info("收到 Ctrl+C，watchdog 結束")
            break
        except Exception as e:
            logger.error(f"watchdog 異常，10 秒後重啟: {e}")
            time.sleep(10)
