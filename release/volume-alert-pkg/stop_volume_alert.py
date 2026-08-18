"""
13:30 由工作排程觸發：若 volume_alert_worker 還活著則強制結束。
worker 本身已內建 13:30 自動退出邏輯，此腳本為安全網（防 worker hung 在某次 run_once）。
"""
import subprocess
import logging
import os

LOG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'stop_volume_alert.log')
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)-5s %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    handlers=[logging.StreamHandler(), logging.FileHandler(LOG_FILE, encoding='utf-8')],
)
logger = logging.getLogger(__name__)


def main():
    r = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
         "Where-Object { $_.CommandLine -like '*volume_alert_worker*' } | "
         "Select-Object -ExpandProperty ProcessId"],
        capture_output=True, text=True, timeout=15,
    )
    pids = [p.strip() for p in r.stdout.splitlines() if p.strip()]
    if not pids:
        logger.info("volume_alert_worker 未在運行（已自動結束）")
        return
    for pid in pids:
        try:
            subprocess.run(["taskkill", "/F", "/PID", pid], capture_output=True, text=True, timeout=10)
            logger.info(f"已強制結束 PID {pid}")
        except Exception as e:
            logger.error(f"結束 PID {pid} 失敗: {e}")


if __name__ == '__main__':
    main()
