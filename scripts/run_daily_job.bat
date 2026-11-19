@echo off
rem Runs the daily job: collect new posts -> keyword filter -> Claude rating.
title Automation Demand Finder - daily job
cd /d "%~dp0.."

if not exist ".venv\Scripts\python.exe" (
    echo The app is not installed yet. Follow "1. Install" in README.md first.
    pause
    exit /b 1
)

echo Collecting and rating new posts. This takes 1-2 minutes...
echo.
".venv\Scripts\python.exe" run_daily.py
echo.
if errorlevel 1 (
    echo Finished, but something needs attention - read the lines above.
) else (
    echo Done. Open the dashboard to see the new posts.
)
echo.
pause
