# tw-stock-scanner — 一鍵註冊全部 Windows 工作排程
# 用法：對本檔按右鍵 →「以 PowerShell 執行」（或 powershell -File bin\install_schedule.ps1）
# 移除：powershell -File bin\uninstall_schedule.ps1
#
# 排程總表（平日）：
#   08:00  TWScanner-DailyCheck       workers\daily_check.py         資料完整性健檢＋補抓
#   08:55  TWScanner-VA-Healthcheck   workers\health_check_volume_alert.py
#   09:00  TWScanner-VA-Start         workers\volume_alert_worker.py 盤中爆量掃描
#   13:30  TWScanner-VA-Stop          workers\stop_volume_alert.py   安全網
#   14:00  TWScanner-Daily            run_daily.py                   收盤行情＋突破
#   15:30  TWScanner-Option           run_daily.py option            選擇權 OI
#   15:40  TWScanner-LargeTrader      run_daily.py largetrader       期貨大戶
#   15:45  TWScanner-Deleveraging     run_daily.py deleveraging      去槓桿指標
#   17:00  TWScanner-Market           run_daily.py market            大盤籌碼
#   18:00  TWScanner-Institutional    run_daily.py institutional     三大法人
#   20:00  TWScanner-Broker           run_daily.py broker            券商分點
#   登入時 TWScanner-AutoStart        workers\watchdog.py            守護 Flask/ngrok/worker

$ErrorActionPreference = "Stop"
# 本檔位於 bin\，專案根為上一層
$ProjectDir = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$Python  = Join-Path $ProjectDir ".venv\Scripts\python.exe"
$Pythonw = Join-Path $ProjectDir ".venv\Scripts\pythonw.exe"

if (-not (Test-Path $Python)) {
    Write-Host "[錯誤] 找不到 $Python — 請先在專案根執行: python -m venv .venv 並安裝 requirements" -ForegroundColor Red
    exit 1
}

$days = "Monday","Tuesday","Wednesday","Thursday","Friday"

function Register-DailyTask($name, $exe, $arg, $time, $desc) {
    try { Unregister-ScheduledTask -TaskName $name -Confirm:$false -ErrorAction Stop } catch {}
    $a = New-ScheduledTaskAction -Execute $exe -Argument $arg -WorkingDirectory $ProjectDir
    $t = New-ScheduledTaskTrigger -Weekly -DaysOfWeek $days -At $time
    Register-ScheduledTask -TaskName $name -Action $a -Trigger $t -Description $desc -Force | Out-Null
    Write-Host "OK: $name @ $time"
}

Register-DailyTask "TWScanner-DailyCheck"     $Python  "workers\daily_check.py"               "08:00" "資料完整性健檢＋補抓"
Register-DailyTask "TWScanner-VA-Healthcheck" $Python  "workers\health_check_volume_alert.py" "08:55" "爆量預估盤前健檢"
Register-DailyTask "TWScanner-VA-Start"       $Pythonw "workers\volume_alert_worker.py"       "09:00" "盤中爆量掃描（13:30 自動退出）"
Register-DailyTask "TWScanner-VA-Stop"        $Python  "workers\stop_volume_alert.py"         "13:30" "爆量 worker 安全網"
Register-DailyTask "TWScanner-Daily"          $Python  "run_daily.py"                          "14:00" "收盤行情＋突破掃描"
Register-DailyTask "TWScanner-Option"         $Python  "run_daily.py option"                   "15:30" "TXO 選擇權 OI"
Register-DailyTask "TWScanner-LargeTrader"    $Python  "run_daily.py largetrader"              "15:40" "期貨大額交易人"
Register-DailyTask "TWScanner-Deleveraging"   $Python  "run_daily.py deleveraging"             "15:45" "去槓桿指標"
Register-DailyTask "TWScanner-Market"         $Python  "run_daily.py market"                   "17:00" "大盤籌碼 (FinMind)"
Register-DailyTask "TWScanner-Institutional"  $Python  "run_daily.py institutional"            "18:00" "三大法人買賣超"
Register-DailyTask "TWScanner-Broker"         $Python  "run_daily.py broker"                   "20:00" "券商分點"

# 登入時自動啟動 watchdog（守 Flask + ngrok + 兩支 worker）
try { Unregister-ScheduledTask -TaskName "TWScanner-AutoStart" -Confirm:$false -ErrorAction Stop } catch {}
$a = New-ScheduledTaskAction -Execute $Pythonw -Argument "workers\watchdog.py" -WorkingDirectory $ProjectDir
$t = New-ScheduledTaskTrigger -AtLogOn
Register-ScheduledTask -TaskName "TWScanner-AutoStart" -Action $a -Trigger $t `
    -Description "登入時自動啟動掃描器守護程序" -Force | Out-Null
Write-Host "OK: TWScanner-AutoStart @ logon"

Write-Host ""
Write-Host "全部 12 個排程已註冊。可在『工作排程器』搜尋 TWScanner 查看。"
