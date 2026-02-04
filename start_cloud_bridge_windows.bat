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

REM Check if requirements are installed
pip show flask > nul 2>&1
if errorlevel 1 (
    echo Installing dependencies...
    pip install flask flask-cors requests
)

REM Get port (default to 5001)
set PORT=%1
if "!PORT!"=="" (
    set PORT=5001
)

echo.
echo Configuration:
echo   Port: !PORT!
echo   URL: http://localhost:!PORT!
echo.
echo Starting Cloud Bridge on port !PORT!...
echo ====================================
echo.

REM Start cloud bridge
python cloud_bridge.py --port !PORT!

REM Pause to see any errors
pause
