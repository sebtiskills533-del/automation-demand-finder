# Creates a desktop shortcut that starts the dashboard.
# Run from the project folder:
#   powershell -ExecutionPolicy Bypass -File scripts\create_desktop_shortcut.ps1

$ErrorActionPreference = "Stop"
$projectDir = Split-Path -Parent $PSScriptRoot
$target = Join-Path $projectDir "scripts\start_dashboard.bat"
if (-not (Test-Path $target)) { throw "Start script not found: $target" }

$desktop = [Environment]::GetFolderPath("Desktop")
$shortcutPath = Join-Path $desktop "Automation Demand Finder.lnk"

$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = $target
$shortcut.WorkingDirectory = $projectDir
$shortcut.Description = "Open the Automation Demand Finder dashboard"
$shortcut.IconLocation = "$env:SystemRoot\System32\shell32.dll,22"  # magnifier icon
$shortcut.WindowStyle = 7  # start minimised; the browser opens by itself
$shortcut.Save()

Write-Host "Shortcut created: $shortcutPath"
