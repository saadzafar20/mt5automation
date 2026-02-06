#!/usr/bin/env python3
"""
Cloud Bridge Service: Multi-tenant API for TradingView → Relay routing.
Routes: /signal, /relay/register, /relay/heartbeat, /relay/poll, /relay/result, /health, /commands/{id}, etc.
"""

import os
import json
import uuid
import time
import logging
import hmac
import hashlib
from datetime import datetime, timedelta
from threading import Lock
from collections import defaultdict
from enum import Enum

from flask import Flask, request, jsonify
from flask_cors import CORS

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
CORS(app)

# ==================== Constants & Config ====================
DEFAULT_COMMAND_TTL = 3600  # 1 hour
DEFAULT_HEARTBEAT_TIMEOUT = 30  # 30 seconds without heartbeat = offline
DEFAULT_RELAY_TOKEN_TTL = 86400 * 30  # 30 days
COMMAND_DEQUEUE_LIMIT = 10  # max commands per poll

# ==================== In-Memory Store (swap for DB in production) ====================

class RelayState(Enum):
    ONLINE = "online"
    OFFLINE = "offline"
    UNKNOWN = "unknown"

class CommandStatus(Enum):
    QUEUED = "queued"
    DELIVERED = "delivered"
    EXECUTED = "executed"
    FAILED = "failed"

class Command:
    def __init__(self, user_id: str, relay_id: str, action: str, symbol: str, size: float, sl=None, tp=None):
        self.id = str(uuid.uuid4())
        self.user_id = user_id
        self.relay_id = relay_id
        self.action = action
        self.symbol = symbol
        self.size = size
        self.sl = sl
        self.tp = tp
        self.status = CommandStatus.QUEUED
        self.created_at = time.time()
        self.ttl = DEFAULT_COMMAND_TTL
        self.result = None
        self.delivered_at = None
        self.executed_at = None

    def is_expired(self):
        return (time.time() - self.created_at) > self.ttl

    def to_dict(self):
        return {
            "id": self.id,
            "user_id": self.user_id,
            "relay_id": self.relay_id,
            "action": self.action,
            "symbol": self.symbol,
            "size": self.size,
            "sl": self.sl,
            "tp": self.tp,
            "status": self.status.value,
            "created_at": self.created_at,
            "delivered_at": self.delivered_at,
            "executed_at": self.executed_at,
            "result": self.result,
        }

class RelayRegistry:
    """Track relay liveness and metadata."""
    def __init__(self):
        self.relays = {}  # user_id -> {relay_id -> {token, state, last_heartbeat, metadata}}
        self.lock = Lock()

    def register(self, user_id: str, relay_id: str, relay_type: str = "self-hosted") -> str:
        """Register a new relay, return an auth token."""
        with self.lock:
            if user_id not in self.relays:
                self.relays[user_id] = {}
            token = str(uuid.uuid4())
            self.relays[user_id][relay_id] = {
                "token": token,
                "state": RelayState.UNKNOWN,
                "last_heartbeat": time.time(),
                "relay_type": relay_type,
                "registered_at": time.time(),
            }
            logger.info(f"Relay registered: user={user_id}, relay={relay_id}, type={relay_type}")
            return token

    def heartbeat(self, user_id: str, relay_id: str, metadata: dict = None):
        """Update relay heartbeat."""
        with self.lock:
            if user_id in self.relays and relay_id in self.relays[user_id]:
                self.relays[user_id][relay_id]["last_heartbeat"] = time.time()
                self.relays[user_id][relay_id]["state"] = RelayState.ONLINE
                if metadata:
                    self.relays[user_id][relay_id]["metadata"] = metadata
                return True
        return False

    def get_relay_state(self, relay_info: dict) -> RelayState:
        """Check if relay is online based on last heartbeat."""
        now = time.time()
        if (now - relay_info["last_heartbeat"]) > DEFAULT_HEARTBEAT_TIMEOUT:
            return RelayState.OFFLINE
        return RelayState.ONLINE

    def list_relays(self, user_id: str):
        """List all relays for a user."""
        with self.lock:
            if user_id in self.relays:
                return {rid: {
                    "state": self.get_relay_state(self.relays[user_id][rid]).value,
                    "last_heartbeat": self.relays[user_id][rid]["last_heartbeat"],
                    "relay_type": self.relays[user_id][rid].get("relay_type", "unknown"),
                    "metadata": self.relays[user_id][rid].get("metadata", {}),
                } for rid in self.relays[user_id]}
        return {}

class CommandQueue:
    """Per-user, per-relay command queue with TTL and retry logic."""
    def __init__(self):
        self.queues = defaultdict(lambda: defaultdict(list))  # user_id -> relay_id -> [commands]
        self.all_commands = {}  # command_id -> Command (for lookup)
        self.lock = Lock()

    def enqueue(self, cmd: Command):
        """Add command to queue."""
        with self.lock:
            self.queues[cmd.user_id][cmd.relay_id].append(cmd)
            self.all_commands[cmd.id] = cmd

    def dequeue(self, user_id: str, relay_id: str, limit: int = COMMAND_DEQUEUE_LIMIT):
        """Get up to `limit` queued commands for a relay."""
        with self.lock:
            queue = self.queues[user_id][relay_id]
            ready = []
            for i, cmd in enumerate(queue):
                if cmd.is_expired():
                    cmd.status = CommandStatus.FAILED
                    cmd.result = {"error": "command expired"}
                else:
                    if len(ready) < limit:
                        cmd.status = CommandStatus.DELIVERED
                        cmd.delivered_at = time.time()
                        ready.append(cmd)
            # Remove delivered/failed commands from queue, but keep in all_commands for lookup
            self.queues[user_id][relay_id] = [c for c in queue if c.status == CommandStatus.QUEUED]
            return ready

    def get_command(self, cmd_id: str):
        """Retrieve command by ID from any storage."""
        with self.lock:
            if cmd_id in self.all_commands:
                return self.all_commands[cmd_id]
        return None

    def update_result(self, cmd_id: str, status: CommandStatus, result: dict):
        """Update command with execution result."""
        cmd = self.get_command(cmd_id)
        if cmd:
            cmd.status = status
            cmd.executed_at = time.time()
            cmd.result = result
            return True
        return False

# Global instances
relay_registry = RelayRegistry()
command_queue = CommandQueue()

# ==================== Auth Helpers ====================

def verify_api_key(user_id: str, api_key: str) -> bool:
    """Verify API key from config or auth service."""
    # TODO: Load from database; for now, check config.json
    try:
        # Try current directory first, then default location
        config_paths = ["config.json", "config.production.json", "/opt/livekit/tradeview/config.json"]
        config_path = next((p for p in config_paths if os.path.exists(p)), None)
        
        if config_path:
            with open(config_path) as f:
                config = json.load(f)
                stored_key = config.get("server", {}).get("api_key", "")
                return api_key == stored_key if stored_key else True  # Allow if key not set
    except Exception as e:
        logger.warning(f"Error verifying API key: {e}")
    return False

def verify_relay_token(user_id: str, relay_id: str, token: str) -> bool:
    """Verify relay authentication token."""
    with relay_registry.lock:
        if user_id in relay_registry.relays and relay_id in relay_registry.relays[user_id]:
            return relay_registry.relays[user_id][relay_id]["token"] == token
    return False

def extract_user_id(request_obj) -> str:
    """Extract user_id from header or session."""
    # For now, use a simple header
    return request_obj.headers.get("X-User-ID", "default-user")

# ==================== Endpoints ====================

@app.route("/health", methods=["GET"])
def health():
    """Health check."""
    return jsonify({
        "status": "online",
        "bridge": "cloud-bridge",
        "timestamp": datetime.utcnow().isoformat(),
    })

@app.route("/signal", methods=["POST"])
def receive_signal():
    """
    Receive TradingView alert.
    POST /signal
    Headers: X-User-ID, X-API-Key (optional)
    Body: {action, symbol, size, sl, tp}
    """
    user_id = extract_user_id(request)
    api_key = request.headers.get("X-API-Key", "")

    # Optional: verify API key
    if api_key and not verify_api_key(user_id, api_key):
        return jsonify({"error": "unauthorized"}), 401

    data = request.get_json(silent=True)
    if data is None:
        raw_body = request.get_data(as_text=True).strip()
        if raw_body:
            try:
                data = json.loads(raw_body)
            except json.JSONDecodeError:
                return jsonify({"error": "invalid JSON payload"}), 400
    data = data or {}
    action = data.get("action", "").upper()
    symbol = data.get("symbol", "")
    size = data.get("size", data.get("lot_size", 0.1))
    sl = data.get("sl", data.get("stop_loss"))
    tp = data.get("tp", data.get("take_profit"))

    if not action or not symbol:
        return jsonify({"error": "missing action or symbol"}), 400

    # Get user's relay(s)
    relays = relay_registry.list_relays(user_id)
    if not relays:
        return jsonify({"error": "no relay registered for user"}), 400

    # For now, route to the first online relay; in production, load-balance or use user preference
    target_relay = None
    for relay_id, relay_info in relays.items():
        if relay_info["state"] == "online":
            target_relay = relay_id
            break

    if not target_relay:
        # No online relay; queue for later delivery
        target_relay = list(relays.keys())[0]
        logger.info(f"No online relay for user {user_id}; queuing command")

    # Create command and enqueue
    cmd = Command(user_id, target_relay, action, symbol, size, sl, tp)
    command_queue.enqueue(cmd)

    logger.info(f"Signal received: user={user_id}, relay={target_relay}, action={action}, symbol={symbol}")
    return jsonify({
        "status": "queued",
        "command_id": cmd.id,
        "relay_id": target_relay,
        "timestamp": datetime.utcnow().isoformat(),
    }), 202

@app.route("/relay/register", methods=["POST"])
def relay_register():
    """
    Register a relay.
    POST /relay/register
    Headers: X-User-ID
    Body: {relay_id, relay_type}
    """
    user_id = extract_user_id(request)
    data = request.get_json() or {}
    relay_id = data.get("relay_id", f"relay-{uuid.uuid4().hex[:8]}")
    relay_type = data.get("relay_type", "self-hosted")  # or "managed"

    token = relay_registry.register(user_id, relay_id, relay_type)

    return jsonify({
        "status": "registered",
        "relay_id": relay_id,
        "token": token,
        "heartbeat_interval": 10,  # seconds
        "poll_timeout": 5,  # seconds
    }), 201

@app.route("/relay/heartbeat", methods=["POST"])
def relay_heartbeat():
    """
    Relay sends heartbeat.
    POST /relay/heartbeat
    Headers: X-User-ID, X-Relay-ID, X-Relay-Token
    Body: {metadata}
    """
    user_id = extract_user_id(request)
    relay_id = request.headers.get("X-Relay-ID", "")
    token = request.headers.get("X-Relay-Token", "")
    data = request.get_json() or {}

    if not relay_id or not token:
        return jsonify({"error": "missing relay ID or token"}), 400

    if not verify_relay_token(user_id, relay_id, token):
        return jsonify({"error": "unauthorized"}), 401

    relay_registry.heartbeat(user_id, relay_id, data.get("metadata"))

    return jsonify({
        "status": "ack",
        "timestamp": datetime.utcnow().isoformat(),
    })

@app.route("/relay/poll", methods=["POST"])
def relay_poll():
    """
    Relay polls for commands.
    POST /relay/poll
    Headers: X-User-ID, X-Relay-ID, X-Relay-Token
    Returns: {commands: []}
    """
    user_id = extract_user_id(request)
    relay_id = request.headers.get("X-Relay-ID", "")
    token = request.headers.get("X-Relay-Token", "")

    if not relay_id or not token:
        return jsonify({"error": "missing relay ID or token"}), 400

    if not verify_relay_token(user_id, relay_id, token):
        return jsonify({"error": "unauthorized"}), 401

    # Get queued commands
    cmds = command_queue.dequeue(user_id, relay_id, COMMAND_DEQUEUE_LIMIT)
    commands_data = [cmd.to_dict() for cmd in cmds]

    logger.info(f"Relay poll: user={user_id}, relay={relay_id}, commands={len(commands_data)}")
    return jsonify({
        "commands": commands_data,
        "timestamp": datetime.utcnow().isoformat(),
    })

@app.route("/relay/result", methods=["POST"])
def relay_result():
    """
    Relay reports command execution result.
    POST /relay/result
    Headers: X-User-ID, X-Relay-ID, X-Relay-Token
    Body: {command_id, status, result}
    """
    user_id = extract_user_id(request)
    relay_id = request.headers.get("X-Relay-ID", "")
    token = request.headers.get("X-Relay-Token", "")
    data = request.get_json() or {}

    if not relay_id or not token:
        return jsonify({"error": "missing relay ID or token"}), 400

    if not verify_relay_token(user_id, relay_id, token):
        return jsonify({"error": "unauthorized"}), 401

    cmd_id = data.get("command_id", "")
    status_str = data.get("status", "failed")
    result = data.get("result", {})

    if not cmd_id:
        return jsonify({"error": "missing command_id"}), 400

    status = CommandStatus[status_str.upper()] if status_str.upper() in CommandStatus.__members__ else CommandStatus.FAILED
    command_queue.update_result(cmd_id, status, result)

    logger.info(f"Relay result: command={cmd_id}, status={status.value}")
    return jsonify({
        "status": "ack",
        "command_id": cmd_id,
        "timestamp": datetime.utcnow().isoformat(),
    })

@app.route("/commands/<cmd_id>", methods=["GET"])
def get_command(cmd_id):
    """
    Get command status.
    GET /commands/{id}
    Headers: X-User-ID
    """
    user_id = extract_user_id(request)
    cmd = command_queue.get_command(cmd_id)

    if not cmd or cmd.user_id != user_id:
        return jsonify({"error": "command not found"}), 404

    return jsonify(cmd.to_dict())

@app.route("/relays", methods=["GET"])
def list_relays():
    """
    List all relays for a user.
    GET /relays
    Headers: X-User-ID
    """
    user_id = extract_user_id(request)
    relays = relay_registry.list_relays(user_id)
    return jsonify({
        "relays": relays,
        "timestamp": datetime.utcnow().isoformat(),
    })

@app.route("/stats", methods=["GET"])
def get_stats():
    """
    Get bridge statistics.
    GET /stats
    """
    user_id = extract_user_id(request)
    relays = relay_registry.list_relays(user_id)
    online_count = sum(1 for r in relays.values() if r["state"] == "online")

    return jsonify({
        "user_id": user_id,
        "relays": {
            "total": len(relays),
            "online": online_count,
            "offline": len(relays) - online_count,
        },
        "timestamp": datetime.utcnow().isoformat(),
    })

@app.errorhandler(404)
def not_found(e):
    return jsonify({"error": "endpoint not found"}), 404

@app.errorhandler(500)
def server_error(e):
    logger.error(f"Server error: {e}")
    return jsonify({"error": "internal server error"}), 500

# ==================== Main ====================

if __name__ == "__main__":
    port = int(os.getenv("CLOUD_BRIDGE_PORT", 5001))
    host = os.getenv("CLOUD_BRIDGE_HOST", "0.0.0.0")
    debug = os.getenv("CLOUD_BRIDGE_DEBUG", "false").lower() == "true"
    logger.info(f"Starting Cloud Bridge on {host}:{port}")
    app.run(host=host, port=port, debug=debug)
