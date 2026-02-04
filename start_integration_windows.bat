@echo off
REM Windows batch file to start MT5 TradingView Integration Service
REM Place this in C:\trading\ on Windows VPS

setlocal enabledelayedexpansion

echo ====================================
echo MT5 TradingView Integration
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
pip show MetaTrader5 > nul 2>&1
if errorlevel 1 (
    echo Installing dependencies...
    pip install -r requirements_mt5.txt
)

REM Get port (default to 5000)
set PORT=%1
if "!PORT!"=="" (
    set PORT=5000
)

echo.
echo Configuration:
echo   Port: !PORT!
echo   URL: http://localhost:!PORT!
echo.
echo Starting MT5 Integration on port !PORT!...
echo ====================================
echo.

REM Start Flask app
python mt5_tradingview_integration.py --port !PORT!

REM Pause to see any errors
pause
