@echo off
REM 結束 volume_alert 所有 process（watchdog / app / worker）
setlocal
cd /d "%~dp0"

echo 正在結束 volume_alert 相關進程...
powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name='python.exe' OR Name='pythonw.exe'\" | Where-Object { $_.CommandLine -match 'watchdog.py|volume_alert_worker|app.py' -and $_.CommandLine -match '%~dp0'.Replace('\','\\') } | ForEach-Object { Write-Host ('kill PID ' + $_.ProcessId + ' : ' + $_.CommandLine); Stop-Process -Id $_.ProcessId -Force }"
echo 完成。
endlocal
