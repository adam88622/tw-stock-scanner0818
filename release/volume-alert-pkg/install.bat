@echo off
REM ==================================================
REM volume_alert 安裝腳本（Windows）
REM 流程：
REM   1) 建立 .venv 虛擬環境
REM   2) 安裝 Flask + requests
REM   3) 初始化 SQLite 資料庫
REM   4) 抓取最近 60 個交易日的 OHLCV 歷史資料（約 5–10 分鐘）
REM   5) 註冊 Windows 工作排程（8:55 健檢 / 9:00 啟動 / 13:30 結束）
REM 完成後執行 start.bat 立刻啟動 Flask + 看板。
REM ==================================================

setlocal
cd /d "%~dp0"

echo.
echo [1/5] 檢查 Python...
where python >nul 2>&1
if errorlevel 1 (
    echo [錯誤] 系統找不到 python。請先安裝 Python 3.10+ 並勾選 Add to PATH。
    pause
    exit /b 1
)
python --version

echo.
echo [2/5] 建立虛擬環境 .venv ...
if not exist ".venv" (
    python -m venv .venv
)
call ".venv\Scripts\activate.bat"
python -m pip install --upgrade pip
pip install -r requirements.txt
if errorlevel 1 (
    echo [錯誤] 依賴安裝失敗
    pause
    exit /b 1
)

echo.
echo [3/5] 初始化資料庫 schema ...
python -c "from models.database import init_db; init_db()"

echo.
echo [4/5] 抓取最近 60 個交易日歷史 OHLCV（約 5–10 分鐘，請耐心等待）...
python seed_data.py 60
if errorlevel 1 (
    echo [警告] seed 過程有錯誤（可能 TWSE/TPEx 限流），可稍後手動補：python seed_data.py 60
)

echo.
echo [5/5] 註冊 Windows 工作排程 ...
powershell -NoProfile -ExecutionPolicy Bypass -File "install_schedule.ps1"

echo.
echo ==================================================
echo 安裝完成！
echo   - 立刻啟動：執行 start.bat
echo   - 看板網址：http://127.0.0.1:5000/volume-alert
echo   - 排程說明：平日 09:00 自動啟動、13:30 自動結束
echo ==================================================
pause
