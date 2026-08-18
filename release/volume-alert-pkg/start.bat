@echo off
REM 啟動 volume_alert（背景跑 watchdog → 自動拉 Flask + 在交易時段拉 worker）
setlocal
cd /d "%~dp0"

REM 確認已安裝
if not exist ".venv\Scripts\pythonw.exe" (
    echo [錯誤] .venv 尚未建立，請先執行 install.bat
    pause
    exit /b 1
)

REM 用 pythonw.exe 背景啟動 watchdog（無視窗）
start "" ".venv\Scripts\pythonw.exe" watchdog.py

REM 等 Flask 起來再開瀏覽器
echo 等待 Flask 啟動...
for /L %%i in (1,1,20) do (
    timeout /t 1 /nobreak >nul
    powershell -NoProfile -Command "try { (Invoke-WebRequest -Uri http://127.0.0.1:5000/ -UseBasicParsing -TimeoutSec 1).StatusCode } catch { exit 1 }" >nul 2>&1
    if not errorlevel 1 (
        start http://127.0.0.1:5000/volume-alert
        echo Flask 已啟動，已開啟瀏覽器。
        goto :done
    )
)
echo [警告] Flask 啟動逾時，請查看 log/watchdog.log
:done
endlocal
