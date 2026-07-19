@echo off
title Collection Database Server
cd /d "%~dp0"
echo Starting the Collection Database... a browser window will open shortly.
where py >nul 2>nul && ( py run_server.py ) || ( python run_server.py )
echo.
echo The server has stopped. You can close this window.
pause
