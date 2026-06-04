@echo off
echo =============================================
echo Supreme ViewBot v2.1 - YouTube Edition
echo =============================================
echo.

if "%1"=="diagnostics" (
    echo Running Diagnostics Mode...
    python main.py --mode diagnostics
) else (
    echo Running Full YouTube Engagement...
    echo Usage: run.bat "https://youtube.com/watch?v=VIDEO_ID"
    python main.py --url %1 --mode full
)

pause