# Runs the daily job every 3 hours (from 7 am), in the background, without a window.
# Fresh gigs with few bids then pop up as a Windows notification.
# Switch it on/off, and choose its hours, on the dashboard's Settings page (no need to run this again).
#
# Run once from the project folder:
#     powershell -ExecutionPolicy Bypass -File scripts\schedule_every_3_hours.ps1
# Stop it any time:
#     Unregister-ScheduledTask -TaskName "Automation Demand Finder" -Confirm:$false

$ErrorActionPreference = "Stop"
$dir = Split-Path -Parent $PSScriptRoot
$pythonw = Join-Path $dir ".venv\Scripts\pythonw.exe"   # pythonw = no console window
if (-not (Test-Path $pythonw)) {
    Write-Host "The app is not installed yet. Follow '1. Install' in README.md first."
    exit 1
}

$action = New-ScheduledTaskAction -Execute $pythonw -Argument "run_daily.py --scheduled" -WorkingDirectory $dir
$start = (Get-Date).Date.AddHours(7)
$trigger = New-ScheduledTaskTrigger -Once -At $start -RepetitionInterval (New-TimeSpan -Hours 3) -RepetitionDuration (New-TimeSpan -Days 3650)
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Hours 1) -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName "Automation Demand Finder" -Action $action -Trigger $trigger -Settings $settings `
    -Description "Collect, filter and rate new posts every 3 hours; notify about fresh gigs with few bids" -Force | Out-Null

Write-Host "Done. The job now runs every 3 hours (first at 7 am; missed runs start when the computer is on)."
Write-Host "Logs: logs\app.log"
