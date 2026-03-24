# MT5 Automation – Multi-User Cloud Bridge + MT5 Relay

This project provides a bridge between MetaTrader 5 (MT5) and TradingView, enabling automated trading based on TradingView alerts. It supports multiple users, each with their own user ID, API key, relay, and MT5 account.

## Key Features

- **Multi-User Isolation:** Commands are routed by `user_id` and `relay_id` so each user's signals stay isolated.
- **Persistent Storage:** Cloud Bridge state is stored in SQLite (`bridge.db`) so relays/commands survive restarts.
- **Per-User Authentication:** API key validation is per user (not global).
- **TradingView Integration:** Receives alerts from TradingView and translates them into actionable MT5 orders.
- **MT5 Automation:** Automatically places, modifies, or closes trades on MT5 based on received signals.
- **Easy Setup:** Includes scripts and configuration files for quick deployment on Windows or VPS environments.
- **Extensible:** The codebase is structured for easy customization and future enhancements.

## Use Case

Ideal for:
- individual traders running direct MT5 integration, and
- hosted environments where many users run personal relays/MT5 accounts against a shared bridge.

## Cloud Bridge Production Configuration

Set these environment variables before starting `cloud_bridge.py`:

- `BRIDGE_DB_PATH` (default: `bridge.db`)
- `BRIDGE_REQUIRE_API_KEY` (default: `true`)
- `BRIDGE_AUTH_SALT` (set a long random secret in production)
- `BRIDGE_USERS_JSON` (required to provision users)

Example:

```powershell
$env:BRIDGE_AUTH_SALT="use-a-long-random-secret"
$env:BRIDGE_USERS_JSON='{"alice":"alice-secret-key","bob":"bob-secret-key"}'
python cloud_bridge.py --host 0.0.0.0 --port 5001
```

### Relay registration/auth flow (user login)

1. User logs into relay app using dashboard credentials (`user_id` + password).
2. Relay calls `POST /relay/login` and receives relay token bound to `(user_id, relay_id)`.
3. Relay keeps heartbeat and uses wait-poll (`/relay/poll?wait=25`) for low-latency command delivery.
4. Relay executes on local MT5 and posts execution result to bridge.

### Managed VPS execution (relay not required for uptime)

If you need execution even when user relay is offline, use managed mode:

1. Configure bridge encryption key:

```powershell
$env:BRIDGE_CREDS_KEY="your-long-secret-key"
```

2. Run one-time bootstrap from user side to upload MT5 account credentials:

```powershell
python relay.py --bridge-url https://bridge.yourdomain.com --user-id alice --password "dashboard-password" --config config.json --bootstrap-managed --api-key "alice-api-key"
```

3. After this, bridge executes signals directly on VPS MT5 for that user.
4. Relay can be offline; execution still works in managed mode.

Performance tuning for multi-tenant managed execution:

- `MANAGED_EXECUTOR_WORKERS` (default: `4`) controls concurrent VPS execution workers.
- `MANAGED_EXECUTOR_TIMEOUT_SECS` (default: `20`) timeout per managed execution task.

### Latency tuning (cloud ↔ relay ↔ MT5)

- Place bridge and relays in the same region as the broker POP; avoid cross-continent paths.
- Keep wait-poll short on relays (e.g., `poll_timeout` 5–10s, heartbeat 5–7s) when latency matters.
- Run MT5 and relay on the same Windows host or LAN; avoid Wi‑Fi/power saving and keep MT5 symbols visible in Market Watch.
- Prefer managed/VPS mode when end-user machines sleep; 24/7 host maintains connectivity.

## Users Accessing VPS Bridge from Personal Computers

Recommended setup:

1. Run `cloud_bridge.py` on the VPS.
2. Put Nginx/Caddy in front with HTTPS and reverse proxy to `http://127.0.0.1:5001`.
3. Open only ports `443` (and optionally `80` for ACME). Keep `5001` closed publicly.
4. Give each user:
	 - bridge URL (e.g., `https://bridge.yourdomain.com`)
	 - personal `user_id`
	 - personal API key
5. User runs relay locally on their PC or their own VPS:

```powershell
python relay.py --bridge-url https://bridge.yourdomain.com --user-id alice --password "your-dashboard-password" --config config.json
```

Or use the desktop app (Electron):

```powershell
cd relay-ui
npm install
npm run electron:dev
```

In the GUI:

- Enter only `User ID` and `Password`.
- App auto-uses production bridge URL and auto-detects MT5 path.
- Click `Enable VPS 24/7 Mode` to perform one-time managed setup.
- App can remember credentials via Windows Credential Manager (`keyring`) and auto-connect on launch.
- Visual dots show live status for Cloud Bridge, MT5, and Broker.
- App supports system tray minimize and optional Windows startup launch.
- App includes a dashboard mirror panel (webhook URL, scripts, relay summary).
- App checks `/version` and can open relay download URL when a newer build is available.
- After success, bridge executes for that user on VPS even when relay is offline.

macOS usage parity:

- Run the Electron app from `relay-ui/` (`npm run electron:dev` for development or `npm run electron:build:mac` for packaged build).
- Local MT5 is unavailable on macOS; click **Enable VPS 24/7 Mode** to upload MT5 creds so the bridge executes trades on the managed host.
- The dashboard mirror works on Mac; adjust Bridge URL under **Advanced** if using a custom domain.
- For true local execution from a Mac, use a Windows VM/VPS with the Windows relay plus MT5 installed.

Build Windows EXE for end users:

```powershell
cd relay-ui
npm run electron:build:win
```

### Request headers to include

- TradingView/clients calling `/signal` must send:
	- `X-User-ID: <user-id>`
	- `X-API-Key: <user-api-key>`

If your webhook sender cannot set custom headers (e.g., TradingView), include `user_id` and `api_key` in JSON body instead.

For shared webhook endpoint routing safety, each alert body should include:

- `user_id`
- `api_key`
- `script_name`

### Security checklist

- Use unique API key per user.
- Rotate keys if compromised.
- Keep `BRIDGE_AUTH_SALT` secret and stable.
- Use HTTPS only.
- Restrict firewall to required ports.
- Back up `bridge.db` regularly.

## Web Dashboard (Register / Sign In)

The bridge now includes built-in web pages:

- `GET /register` - user registration
- `GET /login` - sign in
- `GET /dashboard` - user dashboard

### Dashboard shows

- Bridge/relay status per user (online/offline)
- Scripts purchased by that user
- Per-script metrics:
	- total signals received
	- total executed trades
	- recent signal list (action, symbol, size, status, relay, timestamp)

### TradingView UX improvements

- Unique webhook URL per user is shown in dashboard (tokenized `/signal/<webhook_token>`).
- `BRIDGE_PUBLIC_URL` (optional): set this to your public HTTPS origin so the dashboard shows the externally reachable webhook URL (e.g., `https://bridge.yourdomain.com`).
- Payload Generator UI builds ready-to-paste JSON and supports one-click copy.
- Preferred setup: use the user-specific `/signal/<token>` URL with minimal JSON payload.

### User safety controls in dashboard

- **Risk guardrails:** configure max lot size and per-window trade-rate limits.
- **Notifications:** enable/disable alerts and configure Telegram + Discord destinations.
- **Panic button:** `Panic: Close All` sends an immediate `CLOSE_ALL` command for that user.

### Important startup variables

- `BRIDGE_SESSION_SECRET` (required in production for web sessions)
- `BRIDGE_AUTH_SALT` (required)
- `BRIDGE_USERS_JSON` (optional if users self-register via `/register`)
- `BRIDGE_SCRIPTS_JSON` (optional catalog)
- `BRIDGE_USER_SCRIPT_ASSIGNMENTS_JSON` (optional pre-assignment)
- `BRIDGE_ADMIN_USERNAME` (default: `admin`)
- `BRIDGE_ADMIN_PASSWORD` (or `BRIDGE_ADMIN_PASSWORD_HASH`)

### Admin script assignment page

- `GET /admin/login` - admin sign in
- `GET /admin/catalog` - create/update script catalog (persistent)
- `GET /admin/scripts` - assign purchased scripts to users after signup

Scripts shown on user registration are sourced from the persistent `scripts` table and managed via `/admin/catalog`.
Use the catalog page to deactivate a script when you want to hide it from registration without deleting historical data.

Example (PowerShell):

```powershell
$env:BRIDGE_ADMIN_USERNAME="admin"
$env:BRIDGE_ADMIN_PASSWORD="super-strong-password"
python cloud_bridge.py --host 0.0.0.0 --port 5001
```

### Signal payload for script tracking

To associate a signal with a script in dashboard metrics, include one of:

- `script_name` (preferred)
- `script`
- `strategy`

Example TradingView JSON body:

```json
{
	"user_id": "alice",
	"api_key": "alice-secret-key",
	"script_name": "Momentum Breakout v2",
	"action": "BUY",
	"symbol": "EURUSD",
	"size": 0.1
}
```

## Risk Controls and Defaults

The server applies the following safeguards and defaults when a signal is missing values or when risk limits are reached:

- **Smart SL/TP defaults (pips-based):** If `stop_loss` or `take_profit` is not provided in the signal, the script calculates them using pip distance from entry price.
- **Risk-based lot sizing:** If `lot_size` is not provided, the script uses account equity and SL distance to size the position by risk percentage.
- **Micro lot clamp:** Final volume is clamped to 0.01-0.02 (and broker min/max/step) for safety.
- **Daily loss circuit breaker:** If equity drops by 5% from the start of the UTC day, the script blocks new BUY/SELL orders.

### Environment Variables

You can override the defaults with these variables:

- `DEFAULT_SL_PIPS` (default: 50)
- `DEFAULT_TP_PIPS` (default: 100)
- `RISK_PER_TRADE_PCT` (default: 0.01 for 1%)
- `MIN_LOT_SIZE` (default: 0.01)
- `MAX_LOT_SIZE` (default: 0.02)
- `MAX_DAILY_LOSS_PCT` (default: 0.05 for 5%)

### Signal Behavior Summary

- If `stop_loss`/`take_profit` is missing, values are auto-calculated in pips from entry price.
- If `lot_size` is missing, volume is derived from risk percent and SL distance, then clamped.
- If the daily loss limit is hit, BUY/SELL signals return HTTP 403 and no trades are placed.
