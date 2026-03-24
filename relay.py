#!/usr/bin/env python3
"""
Relay Service: Runs on user's machine (self-hosted) or in cloud (managed).
- Registers with cloud bridge
- Maintains heartbeat
- Long-polls for commands
- Executes via MT5
- Reports results back
"""

import concurrent.futures
import os
import sys
import json
import time
import uuid
import logging
import requests
from datetime import datetime
from typing import Optional, Dict, Any

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger(__name__)

from mt5_order_utils import map_mt5_retcode  # noqa: E402

class RelayClient:
    """Client to communicate with cloud bridge."""
    
    def __init__(self, bridge_url: str, user_id: str, relay_id: Optional[str] = None, api_key: Optional[str] = None):
        self.bridge_url = bridge_url.rstrip("/")
        self.user_id = user_id
        self.relay_id = relay_id or f"relay-{uuid.uuid4().hex[:8]}"
        self.api_key = api_key
        self.token = None
        self.heartbeat_interval = 10
        self.poll_timeout = 25
        self.relay_type = "self-hosted"
        self.session = requests.Session()

    def login(self, password: str, relay_type: str = "self-hosted") -> bool:
        """Authenticate relay with user dashboard credentials and receive relay token."""
        url = f"{self.bridge_url}/relay/login"
        body = {
            "user_id": self.user_id,
            "password": password,
            "relay_id": self.relay_id,
            "relay_type": relay_type,
        }
        try:
            resp = self.session.post(url, json=body, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                self.token = data["token"]
                self.heartbeat_interval = data.get("heartbeat_interval", 10)
                self.poll_timeout = data.get("poll_timeout", 25)
                self.relay_id = data.get("relay_id", self.relay_id)
                self.relay_type = relay_type
                logger.info(f"Relay authenticated: {self.relay_id}, token={self.token[:8]}...")
                return True
            logger.error(f"Relay login failed: {resp.status_code} {resp.text}")
            return False
        except Exception as e:
            logger.error(f"Relay login error: {e}")
            return False

    def register(self, relay_type: str = "self-hosted") -> bool:
        """Register with cloud bridge and get auth token."""
        url = f"{self.bridge_url}/relay/register"
        headers = {"X-User-ID": self.user_id}
        if self.api_key:
            headers["X-API-Key"] = self.api_key
        body = {"relay_id": self.relay_id, "relay_type": relay_type}
        
        try:
            resp = self.session.post(url, json=body, headers=headers, timeout=10)
            if resp.status_code == 201:
                data = resp.json()
                self.token = data["token"]
                self.heartbeat_interval = data.get("heartbeat_interval", 10)
                self.poll_timeout = data.get("poll_timeout", 25)
                self.relay_type = relay_type
                logger.info(f"Relay registered: {self.relay_id}, token={self.token[:8]}...")
                return True
            else:
                logger.error(f"Registration failed: {resp.status_code} {resp.text}")
                return False
        except Exception as e:
            logger.error(f"Registration error: {e}")
            return False

    def heartbeat(self, metadata: Optional[Dict] = None) -> Dict:
        """Send heartbeat to cloud bridge. Returns response data or empty dict."""
        if not self.token:
            return {}

        url = f"{self.bridge_url}/relay/heartbeat"
        headers = {
            "X-User-ID": self.user_id,
            "X-Relay-ID": self.relay_id,
            "X-Relay-Token": self.token,
        }
        body = {"metadata": metadata or {}}

        try:
            resp = self.session.post(url, json=body, headers=headers, timeout=5)
            if resp.status_code == 200:
                return resp.json()
            return {}
        except Exception as e:
            logger.warning(f"Heartbeat error: {e}")
            return {}

    def poll(self) -> list:
        """Poll cloud bridge for commands."""
        if not self.token:
            return []

        url = f"{self.bridge_url}/relay/poll?wait={self.poll_timeout}"
        headers = {
            "X-User-ID": self.user_id,
            "X-Relay-ID": self.relay_id,
            "X-Relay-Token": self.token,
        }

        try:
            resp = self.session.post(url, json={}, headers=headers, timeout=self.poll_timeout + 5)
            if resp.status_code == 200:
                data = resp.json()
                commands = data.get("commands", [])
                if commands:
                    logger.info(f"Received {len(commands)} command(s)")
                return commands
            else:
                logger.warning(f"Poll failed: {resp.status_code}")
                return []
        except Exception as e:
            logger.warning(f"Poll error: {e}")
            return []

    def report_result(self, command_id: str, status: str, result: Dict) -> bool:
        """Report command execution result to cloud bridge."""
        if not self.token:
            return False

        url = f"{self.bridge_url}/relay/result"
        headers = {
            "X-User-ID": self.user_id,
            "X-Relay-ID": self.relay_id,
            "X-Relay-Token": self.token,
        }
        body = {
            "command_id": command_id,
            "status": status,
            "result": result,
        }

        try:
            resp = self.session.post(url, json=body, headers=headers, timeout=5)
            return resp.status_code == 200
        except Exception as e:
            logger.warning(f"Report result error: {e}")
            return False

    def setup_managed_execution(self, api_key: str, mt5_config: Dict[str, Any], mt5_path_override: Optional[str] = None) -> bool:
        """One-time setup: send MT5 credentials to bridge for managed VPS execution."""
        url = f"{self.bridge_url}/managed/setup"
        headers = {
            "X-User-ID": self.user_id,
            "X-API-Key": api_key,
        }
        mt5_path = mt5_path_override if mt5_path_override is not None else mt5_config.get("path", "")
        body = {
            "mt5_login": mt5_config.get("login"),
            "mt5_password": mt5_config.get("password"),
            "mt5_server": mt5_config.get("server"),
            "mt5_path": mt5_path,
        }
        try:
            resp = self.session.post(url, json=body, headers=headers, timeout=15)
            if resp.status_code == 200:
                logger.info("Managed execution setup complete")
                return True
            try:
                detail = resp.json().get("error", resp.text)
            except Exception:
                detail = resp.text
            logger.error(f"Managed setup failed: {resp.status_code} — {detail}")
            return detail or False
        except Exception as e:
            logger.error(f"Managed setup error: {e}")
            return str(e)
    def setup_managed_execution_with_login(self, password: str, mt5_config: Dict[str, Any], mt5_path_override: Optional[str] = None) -> bool:
        """One-time setup authenticated by dashboard credentials."""
        url = f"{self.bridge_url}/managed/setup/login"
        mt5_path = mt5_path_override if mt5_path_override is not None else mt5_config.get("path", "")
        body = {
            "user_id": self.user_id,
            "password": password,
            "mt5_login": mt5_config.get("login"),
            "mt5_password": mt5_config.get("password"),
            "mt5_server": mt5_config.get("server"),
            "mt5_path": mt5_path,
        }
        try:
            resp = self.session.post(url, json=body, timeout=15)
            if resp.status_code == 200:
                logger.info("Managed execution setup complete")
                return True
            try:
                detail = resp.json().get("error", resp.text)
            except Exception:
                detail = resp.text
            logger.error(f"Managed setup (login) failed: {resp.status_code} — {detail}")
            return detail or False
        except Exception as e:
            logger.error(f"Managed setup (login) error: {e}")
            return str(e)

    def get_managed_status(self, api_key: str) -> Dict[str, Any]:
        """Read managed mode status for current user."""
        url = f"{self.bridge_url}/managed/status"
        headers = {
            "X-User-ID": self.user_id,
            "X-API-Key": api_key,
        }
        try:
            resp = self.session.get(url, headers=headers, timeout=10)
            if resp.status_code == 200:
                return resp.json()
            return {"error": f"status check failed: {resp.status_code}", "raw": resp.text}
        except Exception as e:
            return {"error": str(e)}

def get_filling_mode(symbol: str) -> int:
    """Get the correct filling mode for a symbol based on broker support."""
    import MetaTrader5 as mt5
    symbol_info = mt5.symbol_info(symbol)
    if not symbol_info:
        return mt5.ORDER_FILLING_FOK  # Safe default
    
    filling = symbol_info.filling_mode
    # Priority: FOK (1) -> IOC (2) -> RETURN
    if filling & 1:  # SYMBOL_FILLING_FOK
        return mt5.ORDER_FILLING_FOK
    if filling & 2:  # SYMBOL_FILLING_IOC
        return mt5.ORDER_FILLING_IOC
    return mt5.ORDER_FILLING_RETURN


def calculate_sl_tp(symbol: str, action: str, price: float, sl_pips, tp_pips):
    """
    Convert SL/TP from pips to actual price levels.
    Returns (sl_price, tp_price) or (0.0, 0.0) if not set.
    """
    import MetaTrader5 as mt5
    
    if sl_pips is None and tp_pips is None:
        return 0.0, 0.0
    
    symbol_info = mt5.symbol_info(symbol)
    if not symbol_info:
        return 0.0, 0.0
    
    # Point value (e.g., 0.00001 for EURUSD with 5 digits)
    point = symbol_info.point
    # For forex, 1 pip = 10 points (for 5-digit brokers)
    # For JPY pairs, 1 pip = 1 point (for 3-digit)
    digits = symbol_info.digits
    pip_value = point * 10 if digits in (3, 5) else point
    
    sl_price = 0.0
    tp_price = 0.0
    
    if action == "BUY":
        if sl_pips and float(sl_pips) > 0:
            sl_price = price - (float(sl_pips) * pip_value)
        if tp_pips and float(tp_pips) > 0:
            tp_price = price + (float(tp_pips) * pip_value)
    else:  # SELL
        if sl_pips and float(sl_pips) > 0:
            sl_price = price + (float(sl_pips) * pip_value)
        if tp_pips and float(tp_pips) > 0:
            tp_price = price - (float(tp_pips) * pip_value)
    
    return round(sl_price, digits), round(tp_price, digits)


class MT5Executor:
    """Execute trades via MT5."""

    def __init__(self, mt5_login=None, mt5_password=None, mt5_server=None, mt5_path=None):
        self.mt5_connected = False
        self._init_mt5(mt5_login, mt5_password, mt5_server, mt5_path)

    def _init_mt5(self, login, password, server, path):
        """Initialize MT5 connection using explicit credentials."""
        try:
            import MetaTrader5 as mt5

            has_valid_creds = (
                login and password and
                str(login) not in ("", "0") and
                str(password) not in ("", "your_password_here", "changeme")
            )

            def _do_mt5_init():
                if has_valid_creds:
                    return mt5.initialize(
                        path=path or None,
                        login=int(login),
                        password=str(password),
                        server=str(server) if server else None,
                    )
                logger.info("No MT5 credentials provided — attaching to current MT5 session")
                return mt5.initialize(path=path) if path else mt5.initialize()

            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as _ex:
                _fut = _ex.submit(_do_mt5_init)
                try:
                    init_ok = _fut.result(timeout=15)
                except concurrent.futures.TimeoutError:
                    logger.warning("MT5 initialize() timed out after 15s — running in mock mode")
                    init_ok = False

            if not init_ok:
                logger.warning(f"MT5 initialization failed: {mt5.last_error()}")
                self.mt5_connected = False
            else:
                info = mt5.account_info()
                if info:
                    logger.info(f"MT5 connected: account {info.login} on {info.server}")
                else:
                    logger.info("MT5 initialized but no account info available")
                self.mt5_connected = True
        except ImportError:
            logger.warning("MetaTrader5 module not available — relay running in mock mode")
            self.mt5_connected = False
        except Exception as e:
            logger.warning(f"MT5 init error: {e}")
            self.mt5_connected = False

    def get_connection_state(self) -> Dict[str, bool]:
        """Return mt5 and broker connectivity state for GUI/heartbeat."""
        state = {
            "mt5_connected": bool(self.mt5_connected),
            "broker_connected": False,
        }
        if not self.mt5_connected:
            return state

        try:
            import MetaTrader5 as mt5
            term = mt5.terminal_info()
            state["broker_connected"] = bool(getattr(term, "connected", False)) if term else False
        except Exception:
            state["broker_connected"] = False
        return state

    def execute_command(self, command: Dict) -> Dict:
        """
        Execute a trade command.
        Returns: {status, order_id, error}
        """
        action = command.get("action", "").upper()
        symbol = command.get("symbol", "")
        size = command.get("size", 0.1)
        sl = command.get("sl")
        tp = command.get("tp")

        if not symbol:
            return {"status": "failed", "error": "missing symbol"}

        try:
            size = float(size)
        except (TypeError, ValueError):
            size = 0.1

        try:
            if not self.mt5_connected:
                # Mock mode — resolve percentage for logging
                if size < 0:
                    logger.info(f"[MOCK] Executing {action} {abs(size):.1f}% equity {symbol} SL={sl} TP={tp}")
                else:
                    logger.info(f"[MOCK] Executing {action} {size} {symbol} SL={sl} TP={tp}")
                return {
                    "status": "executed",
                    "order_id": str(uuid.uuid4()),
                    "mode": "mock",
                }

            # Real MT5 execution
            import MetaTrader5 as mt5

            # Convert percentage-based lot size to absolute lots
            if size < 0:
                pct = abs(size) / 100.0  # e.g. -1.0 → 0.01
                account = mt5.account_info()
                symbol_info = mt5.symbol_info(symbol)
                if account and symbol_info:
                    contract_size = symbol_info.trade_contract_size or 100000.0
                    tick_data = mt5.symbol_info_tick(symbol)
                    price = tick_data.ask if (tick_data and action == "BUY") else (tick_data.bid if tick_data else 0)
                    if price > 0 and contract_size > 0:
                        size = (account.equity * pct) / (contract_size * price)
                    else:
                        size = 0.01
                    # Clamp to broker limits
                    vol_min = symbol_info.volume_min or 0.01
                    vol_max = symbol_info.volume_max or 100.0
                    vol_step = symbol_info.volume_step or 0.01
                    import math as _math
                    size = max(vol_min, min(size, vol_max))
                    if vol_step > 0:
                        size = _math.floor(size / vol_step) * vol_step
                        if size < vol_min:
                            size = vol_min
                else:
                    size = 0.01
                logger.info(f"Percentage lot resolved to {size:.4f} lots")

            filling = get_filling_mode(symbol)
            tick = mt5.symbol_info_tick(symbol)
            if not tick:
                return {"status": "failed", "error": f"symbol {symbol} not found or no tick data"}
            
            if action == "BUY":
                price = tick.ask
                sl_price, tp_price = calculate_sl_tp(symbol, action, price, sl, tp)
                request = {
                    "action": mt5.TRADE_ACTION_DEAL,
                    "symbol": symbol,
                    "volume": size,
                    "type": mt5.ORDER_TYPE_BUY,
                    "price": price,
                    "comment": "relay-trade",
                    "type_filling": filling,
                }
                if sl_price > 0:
                    request["sl"] = sl_price
                if tp_price > 0:
                    request["tp"] = tp_price
            elif action == "SELL":
                price = tick.bid
                sl_price, tp_price = calculate_sl_tp(symbol, action, price, sl, tp)
                request = {
                    "action": mt5.TRADE_ACTION_DEAL,
                    "symbol": symbol,
                    "volume": size,
                    "type": mt5.ORDER_TYPE_SELL,
                    "price": price,
                    "comment": "relay-trade",
                    "type_filling": filling,
                }
                if sl_price > 0:
                    request["sl"] = sl_price
                if tp_price > 0:
                    request["tp"] = tp_price
            elif action.startswith("CLOSE"):
                # Close all or specific position
                positions = mt5.positions_get(symbol=symbol if not action.startswith("CLOSE_ALL") else None)
                if not positions:
                    return {"status": "failed", "error": "no open positions"}
                
                order_ids = []
                for pos in positions:
                    close_request = {
                        "action": mt5.TRADE_ACTION_DEAL,
                        "symbol": pos.symbol,
                        "volume": pos.volume,
                        "type": mt5.ORDER_TYPE_SELL if pos.type == mt5.ORDER_TYPE_BUY else mt5.ORDER_TYPE_BUY,
                        "position": pos.ticket,
                        "comment": "relay-close",
                        "type_filling": get_filling_mode(pos.symbol),
                    }
                    result = mt5.order_send(close_request)
                    if result.retcode == mt5.TRADE_RETCODE_DONE:
                        order_ids.append(result.order)
                
                if order_ids:
                    return {
                        "status": "executed",
                        "order_ids": order_ids,
                    }
                else:
                    return {"status": "failed", "error": "close failed"}
            else:
                return {"status": "failed", "error": f"unknown action: {action}"}

            result = mt5.order_send(request)
            if result.retcode == mt5.TRADE_RETCODE_DONE:
                logger.info(f"Trade executed: {action} {size} {symbol}, order={result.order}")
                return {
                    "status": "executed",
                    "order_id": result.order,
                }
            else:
                logger.error(f"Trade failed: {result.comment}")
                return {
                    "status": "failed",
                    "error": result.comment,
                    "retcode": result.retcode,
                    "error_message": map_mt5_retcode(result.retcode),
                }
        except Exception as e:
            logger.error(f"Execution error: {e}")
            return {"status": "failed", "error": str(e)}

class Relay:
    """Main relay loop."""

    def __init__(
        self,
        bridge_url: str,
        user_id: str,
        password: str,
        relay_id: Optional[str] = None,
        api_key: Optional[str] = None,
        mt5_login=None,
        mt5_password=None,
        mt5_server=None,
        mt5_path=None,
    ):
        self.client = RelayClient(bridge_url, user_id, relay_id=relay_id, api_key=api_key)
        if relay_id:
            self.client.relay_id = relay_id
        self.password = password
        self.executor = MT5Executor(mt5_login, mt5_password, mt5_server, mt5_path)
        self.running = False
        self._hb_failures = 0

    def _authenticate(self, on_status=None):
        """Authenticate with the bridge. Returns True on success."""
        for attempt in range(1, 4):
            if not self.running:
                return False
            if self.password:
                auth_ok = self.client.login(self.password)
            elif self.client.api_key:
                auth_ok = self.client.register()
            else:
                return False
            if auth_ok:
                return True
            wait = 2 ** attempt
            logger.warning(f"Auth attempt {attempt} failed; retrying in {wait}s")
            if on_status:
                on_status(f"Auth failed (attempt {attempt}/3); retrying in {wait}s…")
            time.sleep(wait)
        return False

    def start(self, on_status=None, on_state=None):
        """Start relay loop with auto-reconnect."""
        logger.info(f"Starting relay: {self.client.relay_id}")
        self.running = True

        while self.running:
            # ── Authenticate ─────────────────────────────────────────────────
            if on_status:
                on_status("Connecting to bridge…")
            if on_state:
                on_state({"cloud_connected": False, "mt5_connected": False, "broker_connected": False})

            if not self._authenticate(on_status):
                if not self.running:
                    break
                logger.error("Failed to authenticate after 3 attempts")
                if on_status:
                    on_status("Authentication failed — retrying in 10s…")
                for _ in range(10):
                    if not self.running:
                        break
                    time.sleep(1)
                continue

            if on_status:
                on_status(f"Connected as {self.client.user_id}")
            if on_state:
                # Only report bridge connected — MT5/broker status comes from
                # the first heartbeat (which includes VPS-side status)
                on_state({"cloud_connected": True})

            self._hb_failures = 0
            last_heartbeat = 0  # triggers immediate first heartbeat

            # ── Main loop ────────────────────────────────────────────────────
            try:
                while self.running:
                    now = time.time()

                    # Heartbeat every N seconds
                    if now - last_heartbeat > self.client.heartbeat_interval:
                        conn_state = self.executor.get_connection_state()
                        metadata = {
                            "version": "1.0",
                            "mt5_connected": conn_state.get("mt5_connected", False),
                            "broker_connected": conn_state.get("broker_connected", False),
                            "uptime": time.time(),
                        }
                        hb_resp = self.client.heartbeat(metadata)
                        hb_ok = bool(hb_resp)
                        last_heartbeat = now
                        if hb_ok:
                            self._hb_failures = 0
                            # Use VPS-side MT5 status if available (for managed users)
                            if hb_resp.get("vps_active"):
                                conn_state["mt5_connected"] = hb_resp.get("vps_mt5_connected", False)
                                conn_state["broker_connected"] = hb_resp.get("vps_mt5_connected", False)
                        else:
                            self._hb_failures += 1
                        if on_status:
                            on_status("Heartbeat sent" if hb_ok else f"Bridge unreachable ({self._hb_failures}x)")
                        if on_state:
                            conn_state["cloud_connected"] = hb_ok
                            on_state(conn_state)
                        # Auto-reconnect after consecutive failures
                        if self._hb_failures >= 5:
                            logger.warning("Too many heartbeat failures — reconnecting")
                            if on_status:
                                on_status("Connection lost — reconnecting…")
                            break  # breaks inner loop → outer loop re-authenticates

                    # Wait-poll for commands (low latency)
                    commands = self.client.poll()
                    for cmd in commands:
                        result = self.executor.execute_command(cmd)
                        cmd_status = result.get("status", "failed")
                        for _retry in range(3):
                            if self.client.report_result(cmd["id"], cmd_status, result):
                                break
                            time.sleep(1)
                        else:
                            logger.error(f"Failed to report result for command {cmd['id']} after 3 attempts")
                        if on_status:
                            action = cmd.get("action", "")
                            symbol = cmd.get("symbol", "")
                            on_status(f"Executed {action} {symbol}: {result.get('status', 'unknown')}")
                        if on_state:
                            conn_state = self.executor.get_connection_state()
                            conn_state["cloud_connected"] = True
                            on_state(conn_state)

                    # No sleep — poll() already does server-side long-polling

            except KeyboardInterrupt:
                logger.info("Relay interrupted")
                self.running = False
            except Exception as e:
                logger.error(f"Relay error: {e}")
                if self.running and on_status:
                    on_status(f"Error: {e} — reconnecting in 5s…")
                time.sleep(5)  # brief pause before reconnect

    def stop(self):
        """Stop relay loop."""
        self.running = False

def main():
    """CLI entry point."""
    import argparse

    parser = argparse.ArgumentParser(description="TradingView-MT5 Relay")
    parser.add_argument("--bridge-url", required=True, help="Cloud bridge URL")
    parser.add_argument("--user-id", required=True, help="User ID")
    parser.add_argument("--password", required=True, help="Dashboard password")
    parser.add_argument("--relay-id", help="Relay ID (auto-generated if omitted)")
    parser.add_argument("--api-key", help="API key (for managed setup)")
    parser.add_argument("--mt5-login", help="MT5 account number")
    parser.add_argument("--mt5-password", help="MT5 account password")
    parser.add_argument("--mt5-server", help="MT5 broker server name")
    parser.add_argument("--mt5-path", help="Path to terminal64.exe (Windows only)")
    parser.add_argument("--bootstrap-managed", action="store_true",
                        help="Send MT5 creds to bridge for VPS execution, then exit")
    parser.add_argument("--headless", action="store_true", help="Run without GUI")
    parser.add_argument("--log-file", help="Write logs to file")

    args = parser.parse_args()

    if getattr(args, "log_file", None):
        fh = logging.FileHandler(args.log_file)
        fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
        logging.getLogger().addHandler(fh)

    if getattr(args, "headless", False):
        logger.info("Running in headless mode")

    if args.bootstrap_managed:
        if not args.api_key:
            logger.error("--api-key is required with --bootstrap-managed")
            raise SystemExit(2)
        mt5_config = {
            "login": args.mt5_login,
            "password": args.mt5_password,
            "server": args.mt5_server,
            "path": args.mt5_path or "",
        }
        client = RelayClient(args.bridge_url, args.user_id, relay_id=args.relay_id)
        ok = client.setup_managed_execution(args.api_key, mt5_config)
        raise SystemExit(0 if ok else 1)

    relay = Relay(
        args.bridge_url, args.user_id, args.password,
        relay_id=args.relay_id,
        mt5_login=args.mt5_login,
        mt5_password=args.mt5_password,
        mt5_server=args.mt5_server,
        mt5_path=args.mt5_path,
    )
    relay.start()

if __name__ == "__main__":
    main()
