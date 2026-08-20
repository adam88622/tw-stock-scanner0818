# tw-stock-scanner — 移除 install_schedule.ps1 註冊的全部排程
$names = @(
    "TWScanner-DailyCheck","TWScanner-VA-Healthcheck","TWScanner-VA-Start","TWScanner-VA-Stop",
    "TWScanner-Daily","TWScanner-Option","TWScanner-LargeTrader","TWScanner-Deleveraging",
    "TWScanner-Market","TWScanner-Institutional","TWScanner-Broker","TWScanner-AutoStart"
)
foreach ($n in $names) {
    try {
        Unregister-ScheduledTask -TaskName $n -Confirm:$false -ErrorAction Stop
        Write-Host "removed: $n"
    } catch {
        Write-Host "not found: $n"
    }
}
Write-Host "完成。"
