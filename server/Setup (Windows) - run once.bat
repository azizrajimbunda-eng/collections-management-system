@echo off
title Collection Database - Setup
cd /d "%~dp0"
echo ============================================================
echo   Collection Database - one-time setup (Windows)
echo ============================================================
echo.

set PY=python
where py >nul 2>nul && set PY=py

echo [1 of 2] Installing required components...
%PY% -m pip install -r requirements.txt
if errorlevel 1 goto nopy

echo.
echo [2 of 2] Building the database from the sample data...
%PY% import_data.py --force

echo.
echo Setup complete.
echo.
echo   To load your real Excel data, run in this folder:
echo       %PY% import_xlsx.py "your-workbook.xlsx"
echo.
echo   To start the app, double-click:  Start Collection Database (Windows).bat
echo.
pause
exit /b 0

:nopy
echo.
echo Could not install. Please install Python 3 from https://www.python.org
echo and tick "Add Python to PATH" during installation, then run this file again.
echo.
pause
exit /b 1
