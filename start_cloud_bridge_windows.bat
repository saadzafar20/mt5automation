@echo off
REM Windows batch file to start the Cloud Bridge Service
REM Place this in C:\trading\ on Windows VPS

setlocal enabledelayedexpansion

echo ====================================
echo Cloud Bridge Service Starter
echo ====================================

REM Check if virtual environment exists
if not exist "venv\" (
    echo Virtual environment not found. Creating...
    python -m venv venv
)

REM Activate virtual environment
echo Activating virtual environment...
call venv\Scripts\activate.bat

REM Install/update cloud bridge requirements
echo Installing dependencies...
pip install -r requirements_cloud_bridge.txt --quiet

REM Get port (default to 8080 — Caddy proxies 80/443 → this port internally)
set PORT=%1
if "!PORT!"=="" (
    set PORT=8080
)

echo.
echo Configuration:
echo   Port       : !PORT!
echo   Public URL : https://platalgo.com
echo.
echo Starting Cloud Bridge on port !PORT!...
echo ====================================
echo.

REM Start cloud bridge (production mode via waitress — no file-watching restarts)
python cloud_bridge.py --port !PORT!

REM Pause to see any errors
pause
