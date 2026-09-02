# 注册/注销 Windows 计划任务:每天 08:05 与 19:05 各跑一轮抓取。
# 用法:
#   powershell -ExecutionPolicy Bypass -File install_task.ps1           # 注册
#   powershell -ExecutionPolicy Bypass -File install_task.ps1 -Remove   # 注销
param(
    [switch]$Remove,
    [string]$ProjectRoot = $PSScriptRoot
)
$ErrorActionPreference = "Stop"
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$Script = Join-Path $ProjectRoot "run_crawl.py"
$TaskName = "WechatArticleCrawler"

if ($Remove) {
    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Write-Host "已注销计划任务 $TaskName"
    } else {
        Write-Host "计划任务 $TaskName 不存在,无需注销"
    }
    exit 0
}
if (-not (Test-Path $Python)) {
    Write-Error "未找到虚拟环境 $Python;请先创建 .venv 并安装 requirements.txt"
    exit 1
}
if (-not (Test-Path $Script)) {
    Write-Error "未找到入口脚本 $Script"
    exit 1
}
$Action   = New-ScheduledTaskAction -Execute $Python -Argument "`"$Script`"" -WorkingDirectory $ProjectRoot
$Triggers = @((New-ScheduledTaskTrigger -Daily -At 08:05), (New-ScheduledTaskTrigger -Daily -At 19:05))
$Settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 4)
Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Triggers `
    -Settings $Settings -Description "微信公众号文章增量爬虫(每天 08:05/19:05)" -Force
Write-Host "已注册计划任务 $TaskName(08:05 / 19:05,当前用户登录时运行)"
