@echo off
rem Starts the Automation Demand Finder dashboard and opens it in your browser.
rem Close this window (or press Ctrl+C in it) to stop the dashboard.
title Automation Demand Finder
cd /d "%~dp0.."

if not exist ".venv\Scripts\python.exe" (
    echo The app is not installed yet. Follow "1. Install" in README.md first.
    pause
    exit /b 1
)

set "URL=http://localhost:8501"
set "HEALTH=http://127.0.0.1:8501/_stcore/health"

rem Already running? Just open the browser.
curl.exe -s -m 2 -o nul "%HEALTH%" && (
    start "" "%URL%"
    exit /b 0
)

rem Open the browser as soon as the dashboard answers (up to 60 seconds).
start "" /b powershell -NoProfile -WindowStyle Hidden -Command ^
  "for ($i = 0; $i -lt 60; $i++) { try { if ((Invoke-WebRequest -UseBasicParsing '%HEALTH%' -TimeoutSec 2).Content -eq 'ok') { Start-Process '%URL%'; break } } catch {}; Start-Sleep -Seconds 1 }"

echo Starting the dashboard... your browser will open in a few seconds.
echo Keep this window open while you use the app. Close it to stop.
echo.
".venv\Scripts\python.exe" -m streamlit run app\dashboard.py
if errorlevel 1 pause
