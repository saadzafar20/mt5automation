@echo off
REM Deprecated compatibility wrapper.
echo ====================================
echo MT5 Integration Launcher (Deprecated)
echo ====================================
echo The old direct integration script has been removed.
echo Use one of the supported launchers instead:
echo.
echo   start_cloud_bridge_windows.bat
echo   start_relay_windows.bat
echo.
echo Or run manually:
echo   python cloud_bridge.py
echo   python relay.py --bridge-url http://localhost:5001 --user-id YOUR_USER --password YOUR_PASSWORD --config config.json
echo.
pause
exit /b 1
