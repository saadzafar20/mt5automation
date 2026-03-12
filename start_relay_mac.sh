#!/bin/bash
# PlatAlgo Relay — macOS launcher
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

echo "========================================"
echo "  PlatAlgo Relay — macOS"
echo "========================================"

# Create venv if needed
if [ ! -d "venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv venv
fi

source venv/bin/activate

# Install dependencies (skip MetaTrader5 — Windows only)
pip show requests > /dev/null 2>&1 || pip install -q -r requirements_cloud_bridge.txt
pip show customtkinter > /dev/null 2>&1 || pip install -q customtkinter pillow pystray keyring
pip show pywebview > /dev/null 2>&1 || pip install -q pywebview

echo ""
echo "Starting PlatAlgo Relay GUI..."
echo "  → Local MT5 is not available on macOS."
echo "  → Use 'Enable VPS 24/7 Mode' to let the cloud server"
echo "    execute trades on your behalf."
echo ""

python3 relay_gui.py
