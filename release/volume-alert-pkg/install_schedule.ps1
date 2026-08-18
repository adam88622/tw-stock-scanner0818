# volume_alert — 註冊 Windows 工作排程
# 排程內容：
#   1. VolumeAlert-Healthcheck  08:55 平日  → 跑 health_check_volume_alert.py
#   2. VolumeAlert-Start        09:00 平日  → 啟動 volume_alert_worker.py
#   3. VolumeAlert-Stop         13:30 平日  → 安全網結束 worker
#   4. VolumeAlert-AutoStart    使用者登入  → 啟動 watchdog（守 Flask + worker）

$ErrorActionPreference = "Stop"
$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python  = Join-Path $ProjectDir ".venv\Scripts\python.exe"
$Pythonw = Join-Path $ProjectDir ".venv\Scripts\pythonw.exe"

if (-not (Test-Path $Python)) {
    Write-Host "[錯誤] 找不到 .venv\Scripts\python.exe，請先執行 install.bat" -ForegroundColor Red
    exit 1
}

$days = "Monday","Tuesday","Wednesday","Thursday","Friday"

# 清掉舊的同名排程
$names = @("VolumeAlert-Healthcheck","VolumeAlert-Start","VolumeAlert-Stop","VolumeAlert-AutoStart")
foreach ($n in $names) {
    try { Unregister-ScheduledTask -TaskName $n -Confirm:$false -ErrorAction Stop } catch {}
}

# 1. 健檢 08:55
$a = New-ScheduledTaskAction -Execute $Python -Argument "health_check_volume_alert.py" -WorkingDirectory $ProjectDir
$t = New-ScheduledTaskTrigger -Weekly -DaysOfWeek $days -At "08:55"
Register-ScheduledTask -TaskName "VolumeAlert-Healthcheck" -Action $a -Trigger $t `
    -Description "volume_alert 盤前健檢 (08:55 weekdays)" -Force | Out-Null
Write-Host "OK: VolumeAlert-Healthcheck @ 08:55"

# 2. 啟動 worker 09:00
$a = New-ScheduledTaskAction -Execute $Pythonw -Argument "volume_alert_worker.py" -WorkingDirectory $ProjectDir
$t = New-ScheduledTaskTrigger -Weekly -DaysOfWeek $days -At "09:00"
Register-ScheduledTask -TaskName "VolumeAlert-Start" -Action $a -Trigger $t `
    -Description "啟動 volume_alert_worker (09:00 weekdays)" -Force | Out-Null
Write-Host "OK: VolumeAlert-Start @ 09:00"

# 3. 結束 worker 13:30（worker 本身會自動結束，這是安全網）
$a = New-ScheduledTaskAction -Execute $Python -Argument "stop_volume_alert.py" -WorkingDirectory $ProjectDir
$t = New-ScheduledTaskTrigger -Weekly -DaysOfWeek $days -At "13:30"
Register-ScheduledTask -TaskName "VolumeAlert-Stop" -Action $a -Trigger $t `
    -Description "結束 volume_alert_worker (13:30 weekdays, safety net)" -Force | Out-Null
Write-Host "OK: VolumeAlert-Stop @ 13:30"

# 4. 開機/登入自動啟動 watchdog（顧 Flask + 在交易時段內 worker）
$a = New-ScheduledTaskAction -Execute $Pythonw -Argument "watchdog.py" -WorkingDirectory $ProjectDir
$t = New-ScheduledTaskTrigger -AtLogOn
Register-ScheduledTask -TaskName "VolumeAlert-AutoStart" -Action $a -Trigger $t `
    -Description "登入時自動啟動 volume_alert watchdog" -Force | Out-Null
Write-Host "OK: VolumeAlert-AutoStart @ logon"

Write-Host ""
Write-Host "全部排程已註冊。可在『工作排程器』中查看（搜尋 VolumeAlert）。"
