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

class RelayClient:
    """Client to communicate with cloud bridge."""
    
    def __init__(self, bridge_url: str, user_id: str, relay_id: Optional[str] = None):
        self.bridge_url = bridge_url.rstrip("/")
        self.user_id = user_id
        self.relay_id = relay_id or f"relay-{uuid.uuid4().hex[:8]}"
        self.token = None
        self.heartbeat_interval = 10
        self.poll_timeout = 5
        self.relay_type = "self-hosted"
        self.session = requests.Session()

    def register(self, relay_type: str = "self-hosted") -> bool:
        """Register with cloud bridge and get auth token."""
        url = f"{self.bridge_url}/relay/register"
        headers = {"X-User-ID": self.user_id}
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

        url = f"{self.bridge_url}/relay/poll"
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
                    logger.info(f"Polled {len(commands)} command(s)")
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
            account = mt5_config.get("login")  # Changed from "account" to "login" to match config.json
            password = mt5_config.get("password")
            server = mt5_config.get("server", "MetaQuotes-Demo")
            path = mt5_config.get("path")  # Get MT5 terminal path from config

            if not mt5.initialize(path=path, login=account, password=password, server=server):
                logger.warning(f"MT5 initialization failed: {mt5.last_error()}")
                self.mt5_connected = False
            else:
                self.mt5_connected = True
                logger.info(f"MT5 connected to {server}")
        except ImportError:
            logger.warning("MetaTrader5 module not available; relay will run in mock mode")
            self.mt5_connected = False
        except Exception as e:
            logger.warning(f"MT5 init error: {e}")
            self.mt5_connected = False

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

            if action == "BUY":
                request = {
                    "action": mt5.TRADE_ACTION_DEAL,
                    "symbol": symbol,
                    "volume": size,
                    "type": mt5.ORDER_TYPE_BUY,
                    "price": mt5.symbol_info_tick(symbol).ask,
                    "sl": sl,
                    "tp": tp,
                    "comment": "relay-trade",
                    "type_filling": mt5.ORDER_FILLING_IOC,
                }
            elif action == "SELL":
                request = {
                    "action": mt5.TRADE_ACTION_DEAL,
                    "symbol": symbol,
                    "volume": size,
                    "type": mt5.ORDER_TYPE_SELL,
                    "price": mt5.symbol_info_tick(symbol).bid,
                    "sl": sl,
                    "tp": tp,
                    "comment": "relay-trade",
                    "type_filling": mt5.ORDER_FILLING_IOC,
                }
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
                        "type_filling": mt5.ORDER_FILLING_IOC,
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
                }
        except Exception as e:
            logger.error(f"Execution error: {e}")
            return {"status": "failed", "error": str(e)}

class Relay:
    """Main relay loop."""
    
    def __init__(self, bridge_url: str, user_id: str, config_path: str = "config.json"):
        self.client = RelayClient(bridge_url, user_id)
        self.executor = MT5Executor(config_path)
        self.running = False

    def start(self):
        """Start relay loop."""
        logger.info(f"Starting relay: {self.client.relay_id}")
        
        # Register
        if not self.client.register():
            logger.error("Failed to register with bridge")
            return False

        self.running = True
        last_heartbeat = 0

        try:
            while self.running:
                now = time.time()

                # Heartbeat every N seconds
                if now - last_heartbeat > self.client.heartbeat_interval:
                    metadata = {
                        "version": "1.0",
                        "mt5_connected": self.executor.mt5_connected,
                        "uptime": time.time(),
                    }
                    self.client.heartbeat(metadata)
                    last_heartbeat = now

                # Poll for commands
                commands = self.client.poll()
                for cmd in commands:
                    result = self.executor.execute_command(cmd)
                    self.client.report_result(cmd["id"], result.get("status", "failed"), result)

                time.sleep(1)

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
    parser.add_argument("--relay-id", help="Relay ID (optional, auto-generated if omitted)")
    parser.add_argument("--config", default="config.json", help="Config path")
    
    args = parser.parse_args()

    relay = Relay(args.bridge_url, args.user_id, args.config)
    relay.start()

if __name__ == "__main__":
    main()
