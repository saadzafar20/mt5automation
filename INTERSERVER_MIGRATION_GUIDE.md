# InterServer Windows VPS Migration Guide

Complete guide for migrating your MT5 TradingView setup from Hostinger to InterServer.

## Step 1: Order & Setup InterServer Windows VPS

### Order Process:
1. Go to **interserver.net** → VPS hosting → Windows VPS
2. Select plan (minimum recommended):
   - **2GB RAM** ($6-8/month)
   - **Windows Server 2019/2022**
   - **20GB+ SSD storage**
   - **Static IP** (usually included)

3. Complete order and receive credentials via email:
   - IP Address
   - Administrator username/password
   - RDP port (usually 3389)

### Access Your VPS:
```bash
# On Mac/Linux, use RDP client or:
xfreerdp /u:Administrator /p:PASSWORD /v:YOUR_IP:3389

# On Windows, use Remote Desktop Connection
```

---

## Step 2: Initial Windows VPS Setup

Once connected via RDP to your InterServer VPS:

### 1. Update Windows
```cmd
# Open Windows Update settings
# Install all updates and restart if needed
```

### 2. Install Python 3.11+
```cmd
# Option A: Download from python.org
# https://www.python.org/downloads/

# Option B: Use Chocolatey (preferred)
# First, install Chocolatey in PowerShell (as Administrator):
Set-ExecutionPolicy Bypass -Scope Process -Force; [System.Net.ServicePointManager]::SecurityProtocol = [System.Net.ServicePointManager]::SecurityProtocol -bor 3072; iex ((New-Object System.Net.WebClient).DownloadString('https://community.chocolatey.org/install.ps1'))

# Then install Python:
choco install python -y

# Verify installation:
python --version
pip --version
```

### 3. Configure Firewall (InterServer)
```cmd
# Open Windows Defender Firewall with Advanced Security
# OR run in PowerShell as Administrator:

# Allow port 5000 (Flask API)
netsh advfirewall firewall add rule name="Flask API 5000" dir=in action=allow protocol=tcp localport=5000

# Allow RDP if not already open (should be default)
netsh advfirewall firewall add rule name="RDP 3389" dir=in action=allow protocol=tcp localport=3389
```

---

## Step 3: Download & Configure Your Trading Setup

### 1. Create Trading Directory
```cmd
mkdir C:\trading
cd C:\trading
```

### 2. Copy Files from Your Current Setup

**Option A: Using Git (if Git is installed)**
```cmd
# Install Git if needed:
choco install git -y

# Clone or copy your files:
git clone https://YOUR_REPO_URL.git
# OR manually copy via SFTP
```

**Option B: Manual Copy via SFTP**
Use WinSCP or FileZilla to transfer from Hostinger to InterServer:
- `mt5_tradingview_integration.py`
- `cloud_bridge.py`
- `relay.py`
- `config.json`
- `requirements_mt5.txt`
- `.env` file

Files are in `/opt/livekit/tradeview/` on your Hostinger server.

### 3. Install Python Dependencies
```cmd
cd C:\trading

# Create virtual environment (recommended)
python -m venv venv
venv\Scripts\activate

# Install dependencies
pip install -r requirements_mt5.txt
# Should include: MetaTrader5, Flask, requests, python-dotenv, Flask-CORS
```

---

## Step 4: Download & Install MetaTrader5

1. **Download MT5**: https://www.metatrader5.com/download
2. **Run installer** on your InterServer Windows VPS
3. **Login with your broker credentials**
   - Account number
   - Password
   - Server name
4. **Keep MT5 running** (minimize to system tray, DO NOT close)

**Verify MT5 is running:**
```cmd
# Check if MT5 process is running
tasklist | findstr terminal64.exe
```

---

## Step 5: Update Configuration Files

### 1. Edit `.env` file
```cmd
# Create or edit C:\trading\.env
```

**Contents** (update with your actual values):
```env
# MT5 Configuration
MT5_PATH=C:\Program Files\MetaTrader 5\terminal64.exe
MT5_LOGIN=your_account_number
MT5_PASSWORD=your_account_password
MT5_SERVER=your_broker_server_name

# API Settings
API_HOST=0.0.0.0
API_PORT=5000
API_KEY=your_webhook_secret_key

# Trading Settings
DEFAULT_LOT_SIZE=0.1
MAX_LOT_SIZE=1.0
RISK_PERCENTAGE=2.0

# Notifications (optional)
TELEGRAM_TOKEN=your_telegram_token
TELEGRAM_CHAT_ID=your_chat_id
```

### 2. Edit `config.json`
```json
{
  "relay": {
    "api_port": 5000,
    "api_key": "your_webhook_secret",
    "host": "0.0.0.0"
  },
  "mt5": {
    "login": your_account_number,
    "password": "your_password",
    "server": "your_broker_server"
  }
}
```

---

## Step 6: Test Your Setup

### 1. Start the Python Service
```cmd
cd C:\trading

# Activate venv
venv\Scripts\activate

# Test the script
python relay.py

# OR for Flask API:
python mt5_tradingview_integration.py

# You should see: * Running on http://0.0.0.0:5000
```

### 2. Test Health Check
```cmd
# In a new PowerShell window:
curl http://localhost:5000/health

# Should return: {"status": "ok"}
```

### 3. Get Your InterServer IP Address
```cmd
# In PowerShell:
ipconfig

# Note the IPv4 address (e.g., 123.45.67.89)
```

---

## Step 7: Setup Windows Service (Auto-Start)

So your trading bot runs automatically on VPS restart:

### Option A: Using NSSM (Recommended)

```cmd
# Download NSSM from: https://nssm.cc/download
# Extract to C:\nssm\

cd C:\nssm\win64
nssm install MTradingService "C:\trading\venv\Scripts\python.exe" "C:\trading\relay.py"
nssm start MTradingService

# Check status:
nssm status MTradingService

# View logs:
nssm get MTradingService AppDirectory
```

### Option B: Using Task Scheduler

1. Open Task Scheduler (taskschd.msc)
2. Create Basic Task:
   - Name: "MT5 Trading Service"
   - Trigger: On Startup
   - Action: Start program
   - Program: `C:\trading\venv\Scripts\python.exe`
   - Arguments: `C:\trading\relay.py`
   - ✓ Run with highest privileges

---

## Step 8: Update TradingView Webhook URL

1. Go to your TradingView chart with Pine Script
2. Find the **Alert** section in your script
3. Update webhook URL to:
```
http://YOUR_INTERSERVER_IP:5000/webhook
```

Example: `http://123.45.67.89:5000/webhook`

4. Update the webhook key in Pine Script to match `API_KEY` from `.env`

---

## Step 9: Monitor & Troubleshoot

### View Application Logs
```cmd
# If using NSSM:
nssm get MTradingService AppStderr

# Or check if relay.py creates a log file:
type C:\trading\trading.log
```

### Test Trade Signal
```cmd
# Send test webhook from PowerShell:
$body = @{
    symbol = "EURUSD"
    action = "BUY"
    lot_size = 0.1
} | ConvertTo-Json

Invoke-WebRequest -Uri "http://localhost:5000/webhook" `
  -Method POST `
  -ContentType "application/json" `
  -Body $body
```

### Common Issues

| Issue | Solution |
|-------|----------|
| Port 5000 blocked | Check firewall: `netsh advfirewall firewall show rule all \|findstr 5000` |
| MT5 not connecting | Verify terminal64.exe is running: `tasklist \|findstr terminal64` |
| Python not found | Add Python to PATH or use full path to python.exe |
| Permission denied | Run PowerShell as Administrator |
| Webhook not receiving signals | Verify TradingView webhook URL and check firewall on InterServer |

---

## Step 10: Optional - Keep Hostinger Linux VPS

You can maintain your Hostinger VPS for:
- Backup/redundancy
- Running auxiliary services
- Cloud bridge/relay compatibility layer

**To sync between servers:**
```bash
# On Hostinger, backup config:
cp -r /opt/livekit/tradeview/ /opt/livekit/backups/

# Rsync to InterServer (if accessible):
rsync -avz /opt/livekit/tradeview/ user@interserver:/trading/
```

---

## Final Checklist

- [ ] InterServer Windows VPS ordered and accessible
- [ ] Python 3.8+ installed and verified
- [ ] MetaTrader 5 installed and running
- [ ] Port 5000 open in firewall
- [ ] Trading files copied to `C:\trading`
- [ ] Python dependencies installed (`pip install -r requirements_mt5.txt`)
- [ ] `.env` and `config.json` updated with credentials
- [ ] Health check working: `curl http://localhost:5000/health`
- [ ] Windows Service/Task Scheduler configured
- [ ] TradingView webhook URL updated to InterServer IP
- [ ] Test trade signal received and logged
- [ ] Monitoring/logging verified

---

## Support

- **InterServer Help**: https://www.interserver.net/support
- **MT5 Docs**: https://www.metatrader5.com/en/trading-platform/help
- **Python MetaTrader5**: https://github.com/khramkov/MetaTrader5

