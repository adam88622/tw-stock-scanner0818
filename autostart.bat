@echo off
:: 檢查是否已在運行
netstat -ano | findstr ":5000 " | findstr "LISTENING" >nul 2>&1
if not errorlevel 1 (
    exit /b 0
)

cd /d D:\claude\tw-stock-scanner

:: 背景啟動 watchdog（pythonw 無視窗，start /b 不等待）
start "" "C:\Users\User\AppData\Local\Programs\Python\Python312\pythonw.exe" watchdog.py

:: 背景補抓缺少的資料
start "" "C:\Users\User\AppData\Local\Programs\Python\Python312\pythonw.exe" auto_update.py

:: 等 Flask 起來後開瀏覽器
start /b cmd /c "for /L %%i in (1,1,20) do (timeout /t 3 /nobreak >nul & netstat -ano | findstr \":5000 \" | findstr \"LISTENING\" >nul 2>&1 && (start http://127.0.0.1:5000/breakout & exit /b 0))"
