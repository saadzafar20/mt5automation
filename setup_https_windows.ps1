#Requires -RunAsAdministrator
<#
.SYNOPSIS
    Sets up Caddy as a Windows service for HTTPS reverse proxy to the trading app.
    Run this once from an Administrator PowerShell prompt:
        powershell -ExecutionPolicy Bypass -File C:\trading\setup_https_windows.ps1
#>

$ErrorActionPreference = "Stop"

$CADDY_DIR   = "C:\caddy"
$CADDY_EXE   = "$CADDY_DIR\caddy.exe"
$CADDY_FILE  = "C:\trading\Caddyfile"
$SERVICE_NAME = "Caddy"

# ── 1. Create Caddy directory ────────────────────────────────────────────────
Write-Host "`n[1/6] Creating $CADDY_DIR ..." -ForegroundColor Cyan
New-Item -ItemType Directory -Force -Path $CADDY_DIR | Out-Null
New-Item -ItemType Directory -Force -Path "$CADDY_DIR\data" | Out-Null

# ── 2. Download Caddy binary ─────────────────────────────────────────────────
Write-Host "[2/6] Downloading Caddy for Windows (amd64)..." -ForegroundColor Cyan
$downloadUrl = "https://caddyserver.com/api/download?os=windows&arch=amd64"
try {
    Invoke-WebRequest -Uri $downloadUrl -OutFile $CADDY_EXE -UseBasicParsing
    Write-Host "    Downloaded to $CADDY_EXE" -ForegroundColor Green
} catch {
    Write-Error "Failed to download Caddy: $_"
    exit 1
}

# ── 3. Open firewall ports 80 and 443 ───────────────────────────────────────
Write-Host "[3/6] Opening firewall ports 80 (HTTP) and 443 (HTTPS)..." -ForegroundColor Cyan
$rules = @(
    @{ Name = "Caddy HTTP";  Port = 80  },
    @{ Name = "Caddy HTTPS"; Port = 443 }
)
foreach ($rule in $rules) {
    $existing = Get-NetFirewallRule -DisplayName $rule.Name -ErrorAction SilentlyContinue
    if (-not $existing) {
        New-NetFirewallRule `
            -DisplayName $rule.Name `
            -Direction Inbound `
            -Protocol TCP `
            -LocalPort $rule.Port `
            -Action Allow | Out-Null
        Write-Host "    Opened port $($rule.Port)" -ForegroundColor Green
    } else {
        Write-Host "    Port $($rule.Port) rule already exists, skipping." -ForegroundColor Yellow
    }
}

# ── 4. Set CADDY_APPDATA so Caddy stores certs in C:\caddy\data ─────────────
Write-Host "[4/6] Setting CADDY_APPDATA environment variable..." -ForegroundColor Cyan
[System.Environment]::SetEnvironmentVariable("CADDY_APPDATA", "$CADDY_DIR\data", "Machine")
$env:CADDY_APPDATA = "$CADDY_DIR\data"

# ── 5. Remove existing service if present, then install fresh ────────────────
Write-Host "[5/6] Installing Caddy as a Windows service..." -ForegroundColor Cyan
$existing = Get-Service -Name $SERVICE_NAME -ErrorAction SilentlyContinue
if ($existing) {
    Write-Host "    Stopping and removing existing Caddy service..." -ForegroundColor Yellow
    Stop-Service -Name $SERVICE_NAME -Force -ErrorAction SilentlyContinue
    & sc.exe delete $SERVICE_NAME | Out-Null
    Start-Sleep -Seconds 2
}

# Create service using sc.exe (no NSSM required)
$binPath = "`"$CADDY_EXE`" run --config `"$CADDY_FILE`" --adapter caddyfile"
& sc.exe create $SERVICE_NAME `
    binPath= $binPath `
    start= auto `
    DisplayName= "Caddy Web Server" | Out-Null

& sc.exe description $SERVICE_NAME "Caddy reverse proxy with automatic HTTPS for app.platalgo.com" | Out-Null

# Set failure recovery: restart on crash
& sc.exe failure $SERVICE_NAME reset= 86400 actions= restart/5000/restart/10000/restart/30000 | Out-Null

Write-Host "    Service created." -ForegroundColor Green

# ── 6. Start the service ─────────────────────────────────────────────────────
Write-Host "[6/6] Starting Caddy service..." -ForegroundColor Cyan
Start-Service -Name $SERVICE_NAME
Start-Sleep -Seconds 3

$svc = Get-Service -Name $SERVICE_NAME
if ($svc.Status -eq "Running") {
    Write-Host "`nCaddy is running!" -ForegroundColor Green
} else {
    Write-Warning "Caddy service status: $($svc.Status). Check logs: C:\caddy\access.log"
    Write-Host "You can test manually: & '$CADDY_EXE' run --config '$CADDY_FILE' --adapter caddyfile"
}

Write-Host @"

Setup complete. Next steps:
  1. Make sure app.platalgo.com DNS A record → 69.10.45.190
  2. Make sure Flask is running on port 80:
         start_cloud_bridge_windows.bat
  3. Visit https://app.platalgo.com — Caddy will auto-issue the SSL certificate.
  4. Add OAuth redirect URIs in Google/Facebook consoles:
         https://app.platalgo.com/auth/google/callback
         https://app.platalgo.com/auth/facebook/callback

To check Caddy service status:   Get-Service Caddy
To restart Caddy:                Restart-Service Caddy
To view Caddy logs:              Get-Content C:\caddy\access.log -Tail 50
"@ -ForegroundColor Cyan
