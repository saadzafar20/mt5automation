#!/usr/bin/env python3
"""
Relay Service: Runs on user's machine (self-hosted) or in cloud (managed).
- Registers with cloud bridge
- Maintains heartbeat
- Long-polls for commands
- Executes via MT5
- Reports results back
"""

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
MT5_RETCODE_MESSAGES = {
    10016: "Invalid stop loss or take profit.",
    10018: "Market is currently closed.",
    10019: "Not enough money in account to execute trade.",
}

def map_mt5_retcode(retcode: Optional[int]) -> str:
    if retcode is None:
        return "Trade request failed."
    return MT5_RETCODE_MESSAGES.get(retcode, f"Broker returned error code {retcode}.")

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
                self.poll_timeout = data.get("poll_timeout", 5)
                self.relay_type = relay_type
                logger.info(f"Relay registered: {self.relay_id}, token={self.token[:8]}...")
                return True
            else:
                logger.error(f"Registration failed: {resp.status_code} {resp.text}")
                return False
        except Exception as e:
            logger.error(f"Registration error: {e}")
            return False

    def heartbeat(self, metadata: Optional[Dict] = None) -> bool:
        """Send heartbeat to cloud bridge."""
        if not self.token:
            return False

        url = f"{self.bridge_url}/relay/heartbeat"
        headers = {
            "X-User-ID": self.user_id,
            "X-Relay-ID": self.relay_id,
            "X-Relay-Token": self.token,
        }
        body = {"metadata": metadata or {}}

        try:
            resp = self.session.post(url, json=body, headers=headers, timeout=5)
            return resp.status_code == 200
        except Exception as e:
            logger.warning(f"Heartbeat error: {e}")
            return False

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
            resp = self.session.post(url, json={}, headers=headers, timeout=self.poll_timeout + 10)
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
            logger.error(f"Managed setup failed: {resp.status_code} {resp.text}")
            return False
        except Exception as e:
            logger.error(f"Managed setup error: {e}")
            return False
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
            logger.error(f"Managed setup (login) failed: {resp.status_code} {resp.text}")
            return False
        except Exception as e:
            logger.error(f"Managed setup (login) error: {e}")
            return False

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
    
    def __init__(self, config_path: str = "config.json"):
        self.config_path = config_path
        self.config = self._load_config()
        self.mt5_connected = False
        self._init_mt5()

    def _load_config(self) -> dict:
        """Load config.json."""
        try:
            with open(self.config_path) as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"Error loading config: {e}")
            return {}

    def _init_mt5(self):
        """Initialize MT5 connection."""
        try:
            import MetaTrader5 as mt5
            mt5_config = self.config.get("mt5", {})
            account = mt5_config.get("login")
            password = mt5_config.get("password")
            server = mt5_config.get("server")
            path = mt5_config.get("path")

            # Check if credentials are empty/placeholder - if so, connect to current session
            has_valid_creds = (
                account and 
                password and 
                password not in ("", "your_password_here", "changeme") and
                account not in (0, 12345678)
            )

            if has_valid_creds:
                # Login with specific credentials
                init_ok = mt5.initialize(path=path, login=int(account), password=password, server=server)
            else:
                # Connect to whatever account is already logged in MT5
                logger.info("No MT5 credentials configured - connecting to current MT5 session")
                init_ok = mt5.initialize(path=path) if path else mt5.initialize()

            if not init_ok:
                logger.warning(f"MT5 initialization failed: {mt5.last_error()}")
                self.mt5_connected = False
            else:
                info = mt5.account_info()
                if info:
                    logger.info(f"MT5 connected to account {info.login} on {info.server}")
                else:
                    logger.info("MT5 initialized but no account info available")
                self.mt5_connected = True
        except ImportError:
            logger.warning("MetaTrader5 module not available; relay will run in mock mode")
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
            if not self.mt5_connected:
                # Mock mode
                logger.info(f"[MOCK] Executing {action} {size} {symbol} SL={sl} TP={tp}")
                return {
                    "status": "executed",
                    "order_id": str(uuid.uuid4()),
                    "mode": "mock",
                }

            # Real MT5 execution
            import MetaTrader5 as mt5

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
    
    def __init__(self, bridge_url: str, user_id: str, password: str, config_path: str = "config.json", relay_id: Optional[str] = None, api_key: Optional[str] = None):
        self.client = RelayClient(bridge_url, user_id, relay_id=relay_id, api_key=api_key)
        if relay_id:
            self.client.relay_id = relay_id
        self.password = password
        self.executor = MT5Executor(config_path)
        self.running = False

    def start(self, on_status=None, on_state=None):
        """Start relay loop."""
        logger.info(f"Starting relay: {self.client.relay_id}")
        if on_status:
            on_status("Starting relay authentication...")
        
        auth_ok = False
        if self.password:
            auth_ok = self.client.login(self.password)
        elif self.client.api_key:
            auth_ok = self.client.register()

        if not auth_ok:
            logger.error("Failed to authenticate relay with bridge")
            if on_status:
                on_status("Authentication failed. Check credentials or API key.")
            return False
        if on_status:
            on_status(f"Connected as {self.client.user_id} ({self.client.relay_id})")
        if on_state:
            state = self.executor.get_connection_state()
            state["cloud_connected"] = True
            on_state(state)

        self.running = True
        last_heartbeat = 0

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
                    hb_ok = self.client.heartbeat(metadata)
                    last_heartbeat = now
                    if on_status:
                        on_status("Heartbeat sent")
                    if on_state:
                        conn_state["cloud_connected"] = bool(hb_ok)
                        on_state(conn_state)

                # Wait-poll for commands (low latency)
                commands = self.client.poll()
                for cmd in commands:
                    result = self.executor.execute_command(cmd)
                    self.client.report_result(cmd["id"], result.get("status", "failed"), result)
                    if on_status:
                        action = cmd.get("action", "")
                        symbol = cmd.get("symbol", "")
                        on_status(f"Executed {action} {symbol}: {result.get('status', 'unknown')}")
                    if on_state:
                        conn_state = self.executor.get_connection_state()
                        conn_state["cloud_connected"] = True
                        on_state(conn_state)

                time.sleep(0.05)

        except KeyboardInterrupt:
            logger.info("Relay interrupted")
        except Exception as e:
            logger.error(f"Relay error: {e}")
        finally:
            self.running = False

    def stop(self):
        """Stop relay loop."""
        self.running = False

def main():
    """CLI entry point."""
    import argparse

    parser = argparse.ArgumentParser(description="TradingView-MT5 Relay")
    parser.add_argument("--bridge-url", required=True, help="Cloud bridge URL (e.g., http://localhost:5001)")
    parser.add_argument("--user-id", required=True, help="User ID")
    parser.add_argument("--password", required=True, help="User password (dashboard credentials)")
    parser.add_argument("--relay-id", help="Relay ID (optional, auto-generated if omitted)")
    parser.add_argument("--config", default="config.json", help="Config path")
    parser.add_argument("--bootstrap-managed", action="store_true", help="One-time MT5 managed setup to bridge and exit")
    parser.add_argument("--api-key", help="API key required for managed setup")
    
    args = parser.parse_args()

    if args.bootstrap_managed:
        if not args.api_key:
            logger.error("--api-key is required with --bootstrap-managed")
            raise SystemExit(2)

        mt5_config = {}
        try:
            with open(args.config) as cfg:
                mt5_config = (json.load(cfg) or {}).get("mt5", {})
        except Exception as exc:
            logger.error(f"Failed to read config for managed bootstrap: {exc}")
            raise SystemExit(1)

        client = RelayClient(args.bridge_url, args.user_id, relay_id=args.relay_id)
        ok = client.setup_managed_execution(args.api_key, mt5_config)
        raise SystemExit(0 if ok else 1)

    relay = Relay(args.bridge_url, args.user_id, args.password, args.config, relay_id=args.relay_id)
    relay.start()

if __name__ == "__main__":
    main()
