@echo off
title Collection Database - Install Auto-Start
cd /d "%~dp0"
echo Registering the Collection Database to start automatically at logon...
echo.

set PY=python
where py >nul 2>nul && set PY=py

schtasks /Create /TN "CollectionDatabase" /TR "\"%PY%\" \"%~dp0run_server.py\"" /SC ONLOGON /RL HIGHEST /F
if errorlevel 1 goto fail

echo.
echo Done. The app will start automatically whenever this computer logs in.
echo.
echo   To turn it off later, run:  schtasks /Delete /TN CollectionDatabase /F
echo.
pause
exit /b 0

:fail
echo.
echo Could not register auto-start. Right-click this file and choose
echo "Run as administrator", then try again.
echo.
pause
exit /b 1
