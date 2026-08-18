# 移除 volume_alert 的所有工作排程
$names = @("VolumeAlert-Healthcheck","VolumeAlert-Start","VolumeAlert-Stop","VolumeAlert-AutoStart")
foreach ($n in $names) {
    try {
        Unregister-ScheduledTask -TaskName $n -Confirm:$false -ErrorAction Stop
        Write-Host "已移除：$n"
    } catch {
        Write-Host "略過（不存在）：$n"
    }
}
