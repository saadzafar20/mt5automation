# MT5 on VPS - Setup & Testing Guide

## Quick Summary

You'll run MT5 on a Windows VPS. The relay (Python) will communicate with MT5 via the MetaTrader5 Python module and execute real trades.

## Architecture

```
TradingView Signal (webhook)
    ↓
Cloud Bridge (on server/VPS)
    ↓
Relay (Python on VPS)
    ↓
MetaTrader5 Python Module (on VPS)
    ↓
MT5 Terminal (on VPS)
    ↓
Real Forex Trades
```

## Step 1: Provision Windows VPS

Use any provider: DigitalOcean, AWS, Linode, Vultr

**Minimum specs:**
- Windows Server 2019 or newer
- 2GB RAM
- 20GB storage
- $10-30/month

**Recommended:**
- Static IP address
- RDP access enabled

## Step 2: Install Dependencies on VPS

### Install Python 3.8+

```cmd
# Download from python.org and install
# OR use Chocolatey:
choco install python
```

### Install MetaTrader5 Python Module

```cmd
pip install MetaTrader5 requests
```

### Install MT5 Terminal

1. Download from MetaQuotes: https://www.metatrader5.com/download
2. Install with your broker credentials
3. Open and verify account login works
4. Keep MT5 running in background (minimize window)

## Step 3: Copy Files to VPS

Copy from `/opt/livekit/tradeview/` to VPS:

```cmd
# On VPS, create directory:
mkdir C:\trading
cd C:\trading

# Copy from server (use SFTP or GitHub):
# - cloud_bridge.py
# - relay.py
# - config.json (update with your MT5 credentials)
```

## Step 4: Update config.json on VPS

```json
{
  "relay": {
    "user_id": "your-user-id",
    "bridge_url": "http://localhost:5001",
    "mode": "mt5"
  },
  "mt5": {
    "account": 12345678,
    "password": "your-mt5-password",
    "server": "MetaQuotes-Demo"
  },
  "server": {
    "host": "0.0.0.0",
    "port": 5001,
    "api_key": "your-api-key"
  },
  "trading": {
    "max_size": 1.0,
    "default_sl": 50,
    "default_tp": 100
  }
}
```

Replace:
- `account`: Your MT5 account number
- `password`: Your MT5 password
- `server`: Your broker's server (e.g., "ICMarketsDemonstration")
- `user_id`: A unique ID for your user

## Step 5: Test Locally on VPS

### Terminal 1 - Cloud Bridge

```cmd
cd C:\trading
python cloud_bridge.py
```

Output:
```
 * Running on http://0.0.0.0:5001
```

### Terminal 2 - Relay

```cmd
cd C:\trading
python relay.py
```

Output should show MT5 connection:
```
[INFO] Connecting to MT5...
[INFO] Connected to account: 12345678
[INFO] Relay polling...
```

### Terminal 3 - Test Signal

```cmd
curl -X POST http://localhost:5001/signal ^
  -H "Content-Type: application/json" ^
  -d "{\"user_id\":\"your-user-id\",\"action\":\"BUY\",\"symbol\":\"EURUSD\",\"size\":0.1}"
```

Response:
```json
{"status":"queued","command_id":"xxx","relay_id":"xxx"}
```

### Verify Trade

**In MT5 Terminal (on VPS):**
- Check **Terminal → Trade** tab
- Should see new BUY position: EURUSD 0.1 lots
- Check **Terminal → Journal** for execution logs

## Step 6: Enable External Access (Optional)

To access bridge from external IP (for webhooks):

**Update config.json:**
```json
{
  "server": {
    "host": "0.0.0.0",
    "port": 5001,
    "api_key": "your-secure-api-key"
  }
}
```

**Test from your Mac:**
```bash
curl http://your-vps-ip:5001/health
```

## Step 7: Enable TradingView Webhooks

**In TradingView Pine Script / Alert:**

Set webhook URL to:
```
http://your-vps-ip:5001/signal
```

With JSON message:
```json
{
  "user_id": "your-user-id",
  "action": "BUY",
  "symbol": "EURUSD",
  "size": 0.1,
  "sl": 1.0700,
  "tp": 1.1000
}
```

## Step 8: Keep VPS Running 24/7

**Option A: Manual (not recommended)**
- Keep RDP session open
- Disable sleep/hibernate
- Manually start Python scripts

**Option B: Windows Task Scheduler (recommended)**
Create scheduled tasks to auto-start:
- `cloud_bridge.py`
- `relay.py`
- Set to run at startup
- Set to run with highest privileges

**Option C: NSSM (Non-Sucking Service Manager)**
```cmd
nssm install CloudBridge python C:\trading\cloud_bridge.py
nssm install Relay python C:\trading\relay.py
```

## Troubleshooting

### Error: "Cannot import MetaTrader5"

Make sure MT5 Python module is installed:
```cmd
pip install MetaTrader5
```

### Error: "MT5 connection failed"

Check:
1. MT5 terminal is open on VPS
2. Account/password correct in config.json
3. Correct server name (e.g., "MetaQuotes-Demo" vs "ICMarketsDemonstration")

### Error: "No response from bridge"

Make sure:
1. Cloud bridge is running (check Terminal 1)
2. Port 5001 is not blocked by firewall
3. Try `curl http://localhost:5001/health` on VPS

### Relay not picking up commands

Check relay logs:
```cmd
python relay.py 2>&1 | more
```

Should show:
```
[POLL] Received X commands
[EXECUTE] ...
```

## Security Notes

⚠️ **Before Production:**

1. **Never commit credentials** - Use environment variables:
   ```cmd
   set MT5_ACCOUNT=12345678
   set MT5_PASSWORD=your-password
   set MT5_SERVER=YourBroker
   ```

2. **Use HTTPS** - Set up SSL/TLS on VPS (use Let's Encrypt)

3. **Secure API Key** - Generate strong random key:
   ```cmd
   python -c "import secrets; print(secrets.token_hex(32))"
   ```

4. **Firewall Rules** - Only allow traffic you need:
   - Port 5001 from your server IP
   - RDP only from your IP

5. **Monitoring** - Set up alerts for:
   - Relay disconnections
   - Failed trades
   - VPS downtime

## Test Checklist

Before going live:

- [ ] VPS provisioned and accessible
- [ ] Python & MT5 module installed
- [ ] MT5 terminal opens with real account
- [ ] config.json updated with credentials
- [ ] Cloud bridge starts without errors
- [ ] Relay connects to MT5 successfully
- [ ] Local signal test executes real trade
- [ ] Trade appears in MT5 Terminal → Trade
- [ ] External curl test works (if using webhook)
- [ ] VPS restarts without data loss

## Next: Production Deployment

Once tested:

1. **Point TradingView webhooks** to `http://your-vps-ip:5001/signal`
2. **Enable auto-start** via Task Scheduler or NSSM
3. **Monitor** MT5 and relay logs
4. **Set up notifications** for trade execution

## Cost Estimate

- Windows VPS: $10-30/month
- MT5 (free with broker account)
- Your broker's trading fees

**Total: $10-30/month + spreads**

---

## Support

If tests fail:
1. Check relay logs for MT5 connection errors
2. Verify MT5 account credentials
3. Verify correct broker server name
4. Check Windows Firewall rules
