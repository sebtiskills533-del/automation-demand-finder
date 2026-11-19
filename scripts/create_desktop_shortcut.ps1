# Creates the desktop shortcuts:
#   "Automation Demand Finder"            starts the dashboard and opens the browser
#   "Automation Demand Finder - Daily job" collects and rates new posts now
# Run from the project folder:
#   powershell -ExecutionPolicy Bypass -File scripts\create_desktop_shortcut.ps1

$ErrorActionPreference = "Stop"
$projectDir = Split-Path -Parent $PSScriptRoot
$desktop = [Environment]::GetFolderPath("Desktop")
$shell = New-Object -ComObject WScript.Shell

function New-AppShortcut {
    param(
        [string]$Name,
        [string]$Script,
        [string]$Description,
        [string]$Icon,
        [int]$WindowStyle  # 1 = normal window, 7 = minimised
    )
    $target = Join-Path $projectDir "scripts\$Script"
    if (-not (Test-Path $target)) { throw "Script not found: $target" }
    $path = Join-Path $desktop "$Name.lnk"
    $shortcut = $shell.CreateShortcut($path)
    $shortcut.TargetPath = $target
    $shortcut.WorkingDirectory = $projectDir
    $shortcut.Description = $Description
    $shortcut.IconLocation = $Icon
    $shortcut.WindowStyle = $WindowStyle
    $shortcut.Save()
    Write-Host "Shortcut created: $path"
}

New-AppShortcut -Name "Automation Demand Finder" -Script "start_dashboard.bat" `
    -Description "Open the Automation Demand Finder dashboard" `
    -Icon "$env:SystemRoot\System32\shell32.dll,22" -WindowStyle 7

# The daily job shows its summary, so its window opens normally.
New-AppShortcut -Name "Automation Demand Finder - Daily job" -Script "run_daily_job.bat" `
    -Description "Collect new posts and have Claude rate them" `
    -Icon "$env:SystemRoot\System32\shell32.dll,238" -WindowStyle 1
