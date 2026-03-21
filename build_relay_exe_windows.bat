@echo off
setlocal enabledelayedexpansion

echo ================================================
echo   PlatAlgo Relay - Windows Build
echo ================================================
echo.

REM Activate venv
if exist "venv\Scripts\activate.bat" (
    call venv\Scripts\activate.bat
) else (
    echo Creating virtual environment...
    python -m venv venv
    call venv\Scripts\activate.bat
)

echo Installing/updating build dependencies...
pip install --quiet --upgrade pip
pip install --quiet pyinstaller pillow keyring requests flask flask-cors pywebview

REM Build React UI if dist doesn't exist
if not exist "relay-ui\dist\index.html" (
    echo Building React UI...
    cd relay-ui
    call npm ci
    call npm run build
    cd ..
)

REM Write PE version info file
echo Writing version info...
(
echo VSVersionInfo^(
echo   ffi=FixedFileInfo^(
echo     filevers=^(1,0,0,0^),
echo     prodvers=^(1,0,0,0^),
echo     mask=0x3f,
echo     flags=0x0,
echo     OS=0x40004,
echo     fileType=0x1,
echo     subtype=0x0,
echo     date=^(0, 0^)
echo   ^),
echo   kids=[
echo     StringFileInfo^([
echo       StringTable^(u'040904B0', [
echo         StringStruct^(u'CompanyName', u'PlatAlgo'^),
echo         StringStruct^(u'FileDescription', u'PlatAlgo Relay - MT5 Trading Automation'^),
echo         StringStruct^(u'FileVersion', u'1.0.0.0'^),
echo         StringStruct^(u'InternalName', u'PlatAlgoRelay'^),
echo         StringStruct^(u'LegalCopyright', u'Copyright 2025 PlatAlgo'^),
echo         StringStruct^(u'OriginalFilename', u'PlatAlgoRelay.exe'^),
echo         StringStruct^(u'ProductName', u'PlatAlgo Relay'^),
echo         StringStruct^(u'ProductVersion', u'1.0.0.0'^)
echo       ]^)
echo     ]^),
echo     VarFileInfo^([VarStruct^(u'Translation', [1033, 1200]^)^]^)
echo   ]
echo ^)
) > version_info.txt

REM Ensure config.json exists
if not exist "config.json" (
    echo {} > config.json
)

echo Building PlatAlgoRelay.exe...
pyinstaller --noconfirm --onefile --windowed ^
  --name PlatAlgoRelay ^
  --version-file version_info.txt ^
  --add-data "config.json;." ^
  --add-data "relay-ui\dist;relay-ui\dist" ^
  --hidden-import relay_webview ^
  --hidden-import relay ^
  --hidden-import flask ^
  --hidden-import flask.json ^
  --hidden-import flask_cors ^
  --hidden-import webview ^
  --hidden-import keyring.backends.Windows ^
  --hidden-import keyring.backends.fail ^
  --collect-all flask ^
  --collect-all flask_cors ^
  --collect-all webview ^
  run_relay.py

del version_info.txt 2>nul

if exist "dist\PlatAlgoRelay.exe" (
    echo.
    echo ================================================
    echo   Build complete!
    echo   Output: dist\PlatAlgoRelay.exe
    echo ================================================
) else (
    echo.
    echo   BUILD FAILED - check output above for errors
)

pause
