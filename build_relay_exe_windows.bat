@echo off
setlocal

REM Build Windows executable for GUI relay app
if not exist "venv\Scripts\python.exe" (
  echo Virtual env not found. Creating...
  python -m venv venv
)

call venv\Scripts\activate.bat
pip install --upgrade pip
pip install pyinstaller

REM Build single-file GUI executable
pyinstaller --noconfirm --onefile --windowed --name MT5Relay relay_gui.py

echo Build complete. EXE is in dist\MT5Relay.exe
pause
