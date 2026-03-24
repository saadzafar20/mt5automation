# Windows VPS Trading Setup - Complete Testing Guide

## Signal Flow Architecture

```
TradingView Pin Script (webhook)
    ↓ POST /signal
Cloud Bridge Service (Port 5001) 
    ↓ Routes to
Relay Service (Port 5000) - Polls cloud bridge
    ↓ Uses
MT5 Terminal (running in background)
    ↓
Live Trading Execution
```

---

## Pre-Flight Checks (Before Starting Services)

### 1. Verify MetaTrader 5 is Running
```powershell
# In PowerShell, check if MT5 is running
tasklist | findstr terminal64.exe
```
**Should show**: `terminal64.exe` in the list
**If not**: Open MetaTrader 5 manually and login with your broker credentials

### 2. Verify Python & Dependencies
```powershell
cd C:\trading

# Check Python version
python --version

# Activate virtual environment
venv\Scripts\activate

# Check MetaTrader5 module
python -c "import MetaTrader5; print('MetaTrader5 OK')"

# Check Flask
python -c "import flask; print('Flask OK')"

# Check all imports
python -c "import MetaTrader5, flask, requests; print('All imports OK')"
```

### 3. Verify Firewall Ports
```powershell
# Check if ports are open
netsh advfirewall firewall show rule name="Flask API 5000"
netsh advfirewall firewall show rule name="Trading API Port 5001"

# Should show both rules exist and enabled
```

### 4. Verify Configuration Files
```powershell
# Check config.json exists and has correct format
cat config.json

# Should show your MT5 login, password, server
# Path should be: C:\Program Files\MetaTrader 5\terminal64.exe
```

---

## Option 1: Run All Services Manually (For Testing)

### Terminal 1: Start Cloud Bridge
```powershell
cd C:\trading
venv\Scripts\activate
python cloud_bridge.py
```
Should show:
```
* Running on http://0.0.0.0:5001
```

### Terminal 2: Start Relay
```powershell
cd C:\trading
venv\Scripts\activate
python relay.py --bridge-url http://localhost:5001 --user-id test-user --config config.json
```
Should show:
```
Relay registered: relay-xxxxx, token=xxxxx...
```

### Terminal 3: Start MT5 Integration (Optional - for direct webhook)
```powershell
cd C:\trading
venv\Scripts\activate
python cloud_bridge.py
```
Should show:
```
Cloud bridge listening on http://0.0.0.0:5001
```

---

## Option 2: Use Batch Files (Easy Start)

Copy these batch files from `/opt/livekit/tradeview/` to `C:\trading\`:
- `start_cloud_bridge_windows.bat`
- `start_relay_windows.bat`
- `start_integration_windows.bat`

### To start services:

**Terminal 1:**
```cmd
start_cloud_bridge_windows.bat
```

**Terminal 2:**
```cmd
start_relay_windows.bat
```

**Terminal 3 (Optional):**
```cmd
start_integration_windows.bat
```

---

## Testing the Signal Flow

### Test 1: Health Check
```powershell
# Check if Cloud Bridge is responding
curl http://localhost:5001/health

# Should return: {"status":"online"}
```

### Test 2: MT5 Direct Integration Health
```powershell
# Check if MT5 Integration is responding
curl http://localhost:5000/health

# Should return: {"status":"online","mt5_connected":true}
```

### Test 3: Send Test Signal to Cloud Bridge
```powershell
$body = @{
    user_id = "test-user"
    relay_id = "relay-test"
    action = "BUY"
    symbol = "EURUSD"
    size = 0.1
} | ConvertTo-Json

Invoke-WebRequest -Uri "http://localhost:5001/signal" `
  -Method POST `
  -ContentType "application/json" `
  -Body $body
```

Expected response:
```json
{"status":"queued","command_id":"xxxxx"}
```

### Test 4: Check Relay Receives Command
In the Relay terminal, you should see:
```
Polled 1 command(s)
Trade executed: BUY 0.1 EURUSD, order=xxxxx
```

### Test 5: Send Direct Signal to MT5 Integration
```powershell
$body = @{
    symbol = "EURUSD"
    action = "BUY"
    lot_size = 0.1
} | ConvertTo-Json

Invoke-WebRequest -Uri "http://localhost:5000/signal" `
  -Method POST `
  -ContentType "application/json" `
  -Body $body
```

Expected response:
```json
{"status":"success","order_id":xxxxx}
```

### Test 6: Check Account Info
```powershell
curl http://localhost:5000/account
```

Should return your account balance, equity, profit, margin info.

### Test 7: Check Open Positions
```powershell
curl http://localhost:5000/positions
```

Should return list of open positions or empty array.

---

## Understanding the Two Approaches

### Approach A: Cloud Bridge + Relay (Recommended)
- TradingView → Cloud Bridge (5001) → Relay (local) → MT5
- Supports multiple VPS instances
- Can run on different servers
- More flexible for scaling

### Approach B: Direct Integration
- TradingView → MT5 Integration (5000) → MT5
- Simpler setup
- Single point of contact
- Best for simple setups

---

## Configuration Files Explained

### config.json
```json
{
    "mt5": {
        "login": 12345678,           // Your MT5 account number
        "password": "password",       // Your MT5 password
        "server": "MetaQuotes-Demo",  // Your broker server name
        "path": "C:\\Program Files\\MetaTrader 5\\terminal64.exe"  // MT5 installation path
    },
    "server": {
        "host": "0.0.0.0",           // Listen on all interfaces
        "port": 5001                 // Cloud bridge port
    },
    "relay": {
        "enabled": false,            // Set to true if relay running on different machine
        "url": "http://localhost:5000",
        "poll_interval": 3           // Poll every 3 seconds
    },
    "trading": {
        "default_lot_size": 0.1,     // Default trade size
        "max_lot_size": 1.0,         // Maximum trade size
        "risk_percentage": 2.0,      // Risk per trade (% of account)
        "max_spread_pips": 50.0      // Don't trade if spread > 50 pips
    }
}
```

### .env (optional local settings)
```env
MT5_PATH=C:\Program Files\MetaTrader 5\terminal64.exe
MT5_LOGIN=12345678
MT5_PASSWORD=your_password
MT5_SERVER=MetaQuotes-Demo
API_HOST=0.0.0.0
API_PORT=5001
API_KEY=your_webhook_secret_key
DEFAULT_LOT_SIZE=0.1
```

---

## TradingView Webhook Setup

Once everything is running on Windows VPS:

1. Go to your TradingView chart
2. Find the Alert dialog (right-click → Add Alert)
3. **Webhook URL** (choose one):
   - **Cloud Bridge**: `http://YOUR_VPS_IP:5001/signal`
   - **Direct Integration**: `http://YOUR_VPS_IP:5000/signal`

4. **Webhook Message** (JSON format):
```json
{
  "symbol": "{{ticker}}",
  "action": "BUY",
  "lot_size": 0.1,
  "comment": "TradingView Signal"
}
```

Get your VPS IP from PowerShell:
```powershell
ipconfig | findstr "IPv4"
```

---

## Troubleshooting

### MT5 Not Connecting
```powershell
# Verify MT5 is running
tasklist | findstr terminal64.exe

# Check credentials in config.json
cat config.json

# Try connecting manually in MT5 terminal
```

### Port Already in Use
```powershell
# Find what's using port 5000
netstat -ano | findstr "5000"

# Kill the process (replace PID with the number shown)
taskkill /PID <PID> /F
```

### Relay Not Receiving Signals
```powershell
# Check cloud bridge is running
curl http://localhost:5001/health

# Check relay heartbeat
# In relay terminal, should see heartbeat every 10 seconds
```

### MetaTrader5 Module Import Error
```powershell
# Reinstall MetaTrader5 module
venv\Scripts\activate
pip install --upgrade MetaTrader5

# Check installation
python -c "import MetaTrader5; print(MetaTrader5.__version__)"
```

### Firewall Blocking Requests
```powershell
# Check firewall rules
netsh advfirewall firewall show rule all | findstr "5000\|5001"

# Add rules if missing
netsh advfirewall firewall add rule name="Trading Cloud Bridge 5001" dir=in action=allow protocol=tcp localport=5001
netsh advfirewall firewall add rule name="Trading Integration 5000" dir=in action=allow protocol=tcp localport=5000
```

---

## Complete Startup Checklist

- [ ] MetaTrader 5 is running (check with `tasklist`)
- [ ] Python virtual environment activated: `venv\Scripts\activate`
- [ ] Dependencies installed: `pip install -r requirements_mt5.txt`
- [ ] config.json has correct MT5 credentials
- [ ] Firewall ports 5000 and 5001 are open
- [ ] Cloud Bridge running and responding to health check
- [ ] Relay running and showing "Relay registered"
- [ ] MT5 Integration running (optional)
- [ ] Test signal sent and received
- [ ] TradingView webhook URL updated to VPS IP

---

## Next Steps

1. **Configure TradingView**: Update webhook URL in your Pine Script alert
2. **Test Live Signal**: Send a test trade from TradingView
3. **Monitor Logs**: Watch the relay/bridge terminals for incoming signals
4. **Setup Auto-Start**: Use Windows Task Scheduler to auto-start on boot
5. **Production Mode**: Move from demo to live account (carefully!)

---

## Getting Your VPS IP

```powershell
# Get IPv4 address
ipconfig

# Or get just the main IP
ipconfig | findstr "IPv4"

# Example output: IPv4 Address: 45.76.123.45
```

Use this IP in TradingView webhooks:
```
http://45.76.123.45:5001/signal
```

