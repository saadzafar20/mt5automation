@echo off
REM Windows batch file to start the MT5 Relay Service
REM Place this in C:\trading\ on Windows VPS

setlocal enabledelayedexpansion

echo ====================================
echo MT5 TradingView Relay Starter
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

REM Get config file path (default to current directory)
set CONFIG_PATH=%1
if "!CONFIG_PATH!"=="" (
    set CONFIG_PATH=config.json
)

REM Get bridge URL (default to localhost if running cloud bridge on same machine)
set BRIDGE_URL=%2
if "!BRIDGE_URL!"=="" (
    set BRIDGE_URL=http://localhost:5001
)

REM Get user ID (default to test user)
set USER_ID=%3
if "!USER_ID!"=="" (
    set USER_ID=default-user
)

echo.
echo Configuration:
echo   Config file: !CONFIG_PATH!
echo   Bridge URL: !BRIDGE_URL!
echo   User ID: !USER_ID!
echo.
echo Starting Relay...
echo ====================================
echo.

REM Start relay with arguments
python relay.py --bridge-url !BRIDGE_URL! --user-id !USER_ID! --config !CONFIG_PATH!

REM Pause to see any errors
pause
