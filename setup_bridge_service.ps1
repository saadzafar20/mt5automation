# PlatAlgo Cloud Bridge — Windows Service Setup
# Run as Administrator in PowerShell:
#   powershell -ExecutionPolicy Bypass -File C:\trading\setup_bridge_service.ps1

$ErrorActionPreference = "Stop"
$ServiceName = "PlatAlgoBridge"
$DisplayName = "PlatAlgo Cloud Bridge"
$Description = "PlatAlgo trading relay bridge — connects MT5 to TradingView webhooks"
$PythonExe   = "C:\trading\venv\Scripts\python.exe"
$Script      = "C:\trading\cloud_bridge.py"
$WorkDir     = "C:\trading"
$NssmUrl     = "https://nssm.cc/release/nssm-2.24.zip"
$NssmDir     = "C:\nssm"
$NssmExe     = "$NssmDir\nssm.exe"

Write-Host "============================================" -ForegroundColor Cyan
Write-Host "  PlatAlgo Bridge — Service Setup" -ForegroundColor Cyan
Write-Host "============================================" -ForegroundColor Cyan
Write-Host ""

# ── Step 1: Download NSSM (Non-Sucking Service Manager) ─────────────────────
if (-not (Test-Path $NssmExe)) {
    Write-Host "[1/4] Downloading NSSM..." -ForegroundColor Yellow
    New-Item -ItemType Directory -Path $NssmDir -Force | Out-Null
    $zipPath = "$NssmDir\nssm.zip"
    Invoke-WebRequest -Uri $NssmUrl -OutFile $zipPath -UseBasicParsing
    Expand-Archive -Path $zipPath -DestinationPath $NssmDir -Force
    # Move the 64-bit exe to the expected location
    $extracted = Get-ChildItem "$NssmDir\nssm-*\win64\nssm.exe" -Recurse | Select-Object -First 1
    if ($extracted) {
        Copy-Item $extracted.FullName $NssmExe -Force
    }
    Remove-Item $zipPath -Force -ErrorAction SilentlyContinue
    Write-Host "  NSSM installed to $NssmExe" -ForegroundColor Green
} else {
    Write-Host "[1/4] NSSM already installed." -ForegroundColor Green
}

# ── Step 2: Remove existing service if present ───────────────────────────────
$existing = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
if ($existing) {
    Write-Host "[2/4] Removing existing $ServiceName service..." -ForegroundColor Yellow
    & $NssmExe stop $ServiceName 2>$null
    & $NssmExe remove $ServiceName confirm
    Start-Sleep -Seconds 2
} else {
    Write-Host "[2/4] No existing service to remove." -ForegroundColor Green
}

# ── Step 3: Install the service ──────────────────────────────────────────────
Write-Host "[3/4] Installing $ServiceName service..." -ForegroundColor Yellow

& $NssmExe install $ServiceName $PythonExe
& $NssmExe set $ServiceName AppParameters "cloud_bridge.py --port 8080"
& $NssmExe set $ServiceName AppDirectory $WorkDir
& $NssmExe set $ServiceName DisplayName $DisplayName
& $NssmExe set $ServiceName Description $Description
& $NssmExe set $ServiceName Start SERVICE_AUTO_START
& $NssmExe set $ServiceName AppStdout "C:\trading\logs\bridge_stdout.log"
& $NssmExe set $ServiceName AppStderr "C:\trading\logs\bridge_stderr.log"
& $NssmExe set $ServiceName AppRotateFiles 1
& $NssmExe set $ServiceName AppRotateBytes 10485760
& $NssmExe set $ServiceName AppRestartDelay 5000

# Ensure logs directory exists
New-Item -ItemType Directory -Path "C:\trading\logs" -Force | Out-Null

Write-Host "  Service installed." -ForegroundColor Green

# ── Step 4: Start the service ────────────────────────────────────────────────
Write-Host "[4/4] Starting $ServiceName service..." -ForegroundColor Yellow
& $NssmExe start $ServiceName
Start-Sleep -Seconds 3

$svc = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
if ($svc -and $svc.Status -eq "Running") {
    Write-Host ""
    Write-Host "============================================" -ForegroundColor Green
    Write-Host "  Service is RUNNING" -ForegroundColor Green
    Write-Host "============================================" -ForegroundColor Green
} else {
    Write-Host ""
    Write-Host "  Service may not have started. Check:" -ForegroundColor Red
    Write-Host "    Get-Service $ServiceName" -ForegroundColor Yellow
    Write-Host "    Get-Content C:\trading\logs\bridge_stderr.log -Tail 20" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "Useful commands:" -ForegroundColor Cyan
Write-Host "  Restart:  Restart-Service $ServiceName" -ForegroundColor White
Write-Host "  Stop:     Stop-Service $ServiceName" -ForegroundColor White
Write-Host "  Status:   Get-Service $ServiceName" -ForegroundColor White
Write-Host "  Logs:     Get-Content C:\trading\logs\bridge_stderr.log -Tail 50" -ForegroundColor White
Write-Host ""
