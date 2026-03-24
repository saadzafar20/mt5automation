#!/usr/bin/env python3
"""
Cloud Bridge Service: multi-tenant API for TradingView → Relay routing.
Routes: /signal, /relay/register, /relay/heartbeat, /relay/poll,
/relay/result, /health, /commands/{id}, /relays, /stats.
"""

# Load .env before any other imports that use os.getenv
from dotenv import load_dotenv
import os as _os
_script_dir = _os.path.dirname(_os.path.abspath(__file__))
load_dotenv(_os.path.join(_script_dir, ".env"))

import argparse
import base64
import collections as _collections
from concurrent.futures import ThreadPoolExecutor
import hashlib
import hmac
import json
import logging
import os
import re as _re_top
import secrets
import signal as _signal
import sqlite3
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from enum import Enum
from functools import wraps
import queue as _queue
import threading
from threading import RLock
from typing import Optional

from flask import Flask, flash, jsonify, redirect, render_template, request, session, url_for
from flask_cors import CORS
try:
    from flask_limiter import Limiter
    from flask_limiter.util import get_remote_address
except ImportError:
    Limiter = None
    get_remote_address = None
import csv
import io
import requests
try:
    from authlib.integrations.flask_client import OAuth as _OAuth
except ImportError:
    _OAuth = None
try:
    from cryptography.fernet import Fernet
except ImportError:
    Fernet = None
from werkzeug.security import check_password_hash, generate_password_hash
from managed_mt5_worker import SessionManager
from mt5_order_utils import user_friendly_error as _mt5_user_friendly_error

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

import gzip
import logging.handlers as _log_handlers
import shutil as _shutil
os.makedirs("logs", exist_ok=True)


class _CompressedTimedRotatingFileHandler(_log_handlers.TimedRotatingFileHandler):
    """FIX 13: Gzip rotated log files to save disk space."""

    def doRollover(self):
        super().doRollover()
        # Compress the most-recently rotated file (it has a date suffix)
        for fname in self.getFilesToDelete():
            # getFilesToDelete returns files to *remove* — the rotated (uncompressed)
            # file we want to compress is the one that was just created before deletion.
            pass
        # Compress any uncompressed rotated files (those without .gz suffix)
        base = self.baseFilename
        log_dir = os.path.dirname(base) or "."
        prefix = os.path.basename(base) + "."
        try:
            for entry in os.listdir(log_dir):
                if entry.startswith(os.path.basename(base) + ".") and not entry.endswith(".gz"):
                    full = os.path.join(log_dir, entry)
                    gz_path = full + ".gz"
                    if not os.path.exists(gz_path):
                        try:
                            with open(full, "rb") as f_in, gzip.open(gz_path, "wb") as f_out:
                                _shutil.copyfileobj(f_in, f_out)
                            os.remove(full)
                        except Exception as _gz_exc:
                            # Don't let compression failure break logging
                            logging.getLogger(__name__).warning(
                                f"Log compression failed for {full}: {_gz_exc}"
                            )
        except Exception:
            pass


_file_handler = _CompressedTimedRotatingFileHandler(
    "logs/bridge.log", when="midnight", interval=1, backupCount=30, encoding="utf-8"
)
_file_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
logging.getLogger().addHandler(_file_handler)

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 1 * 1024 * 1024  # 1 MB — reject oversized payloads
CORS(app, origins=os.getenv("ALLOWED_ORIGINS", "https://app.platalgo.com").split(","))

# ── Rate limiting ──
if Limiter is not None:
    _limiter = Limiter(
        key_func=get_remote_address,
        app=app,
        default_limits=["200 per minute"],
        storage_uri="memory://",
    )
else:
    _limiter = None


def _rate_limit(limit_string):
    """Decorator that applies a flask-limiter rate limit when available, otherwise is a no-op."""
    def decorator(f):
        if _limiter is not None:
            return _limiter.limit(limit_string)(f)
        return f
    return decorator

# Trust Caddy reverse proxy headers so url_for generates https:// URLs
from werkzeug.middleware.proxy_fix import ProxyFix
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)

# ==================== Constants & Config ====================
DEFAULT_COMMAND_TTL = 3600  # 1 hour
DEFAULT_HEARTBEAT_TIMEOUT = 30  # 30 seconds without heartbeat = offline
SYSTEM_DEFAULT_SL_PIPS = 50
SYSTEM_DEFAULT_TP_PIPS = 50
COMMAND_DEQUEUE_LIMIT = 10  # max commands per poll
DB_PATH = os.getenv("BRIDGE_DB_PATH", "bridge.db")
REQUIRE_API_KEY = os.getenv("BRIDGE_REQUIRE_API_KEY", "true").lower() == "true"
AUTH_SALT = os.getenv("BRIDGE_AUTH_SALT", "change-me-in-production")
SESSION_SECRET = os.getenv("BRIDGE_SESSION_SECRET", "change-me-session-secret")
ADMIN_USERNAME = os.getenv("BRIDGE_ADMIN_USERNAME", "admin")
ADMIN_PASSWORD_HASH = os.getenv("BRIDGE_ADMIN_PASSWORD_HASH", "")
ADMIN_PASSWORD = os.getenv("BRIDGE_ADMIN_PASSWORD", "")
MANAGED_RELAY_PREFIX = "managed-"
BRIDGE_CREDS_KEY = os.getenv("BRIDGE_CREDS_KEY", "")
MANAGED_EXECUTOR_TIMEOUT_SECS = int(os.getenv("MANAGED_EXECUTOR_TIMEOUT_SECS", "20"))
APP_VERSION = os.getenv("APP_VERSION", "1.0.0")
RELAY_DOWNLOAD_URL = os.getenv("RELAY_DOWNLOAD_URL", "")
RELAY_MANIFEST_URL = os.getenv("RELAY_MANIFEST_URL", "")
_manifest_cache: dict = {"data": None, "ts": 0.0}
PUBLIC_BASE_URL = os.getenv("BRIDGE_PUBLIC_URL", "").rstrip("/")
DESKTOP_OAUTH_STATE_TTL = max(180, min(int(os.getenv("DESKTOP_OAUTH_STATE_TTL", "600")), 900))

app.secret_key = SESSION_SECRET
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
# Section 9: session expires after 24 hours of inactivity
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(hours=24)

# ==================== OAuth Setup ====================
_oauth_client = _OAuth(app) if _OAuth else None

if _oauth_client:
    _google_client_id = os.getenv("GOOGLE_CLIENT_ID", "")
    _google_client_secret = os.getenv("GOOGLE_CLIENT_SECRET", "")
    if _google_client_id and _google_client_secret:
        google_oauth = _oauth_client.register(
            name="google",
            client_id=_google_client_id,
            client_secret=_google_client_secret,
            server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
            client_kwargs={"scope": "openid email profile"},
        )
    else:
        google_oauth = None

    _fb_client_id = os.getenv("FACEBOOK_CLIENT_ID", "")
    _fb_client_secret = os.getenv("FACEBOOK_CLIENT_SECRET", "")
    if _fb_client_id and _fb_client_secret:
        facebook_oauth = _oauth_client.register(
            name="facebook",
            client_id=_fb_client_id,
            client_secret=_fb_client_secret,
            access_token_url="https://graph.facebook.com/oauth/access_token",
            authorize_url="https://www.facebook.com/dialog/oauth",
            api_base_url="https://graph.facebook.com/",
            client_kwargs={"scope": "public_profile"},
        )
    else:
        facebook_oauth = None
else:
    google_oauth = None
    facebook_oauth = None

# ==================== Startup Validation ====================
DEV_MODE = os.getenv("CLOUD_BRIDGE_DEBUG", "false").lower() == "true"

def validate_startup_config():
    """Validate critical configuration at startup."""
    warnings = []
    errors = []

    if AUTH_SALT == "change-me-in-production":
        if not DEV_MODE:
            errors.append("BRIDGE_AUTH_SALT must be set in production")
        else:
            warnings.append("BRIDGE_AUTH_SALT using default - OK for dev only")

    if SESSION_SECRET == "change-me-session-secret":
        if not DEV_MODE:
            errors.append("BRIDGE_SESSION_SECRET must be set in production")
        else:
            warnings.append("BRIDGE_SESSION_SECRET using default - OK for dev only")

    if not BRIDGE_CREDS_KEY:
        errors.append("BRIDGE_CREDS_KEY must be set — MT5 credentials cannot be stored securely without it")

    if not PUBLIC_BASE_URL:
        warnings.append("BRIDGE_PUBLIC_URL not set - webhook URLs will use request host")

    if not RELAY_DOWNLOAD_URL:
        warnings.append("RELAY_DOWNLOAD_URL not set - download page may show empty links")

    if not os.getenv("BRIDGE_ADMIN_PASSWORD") and not os.getenv("BRIDGE_ADMIN_PASSWORD_HASH"):
        warnings.append("No admin password configured - admin panel will be inaccessible")

    for warn in warnings:
        logger.warning(f"[CONFIG] {warn}")

    if errors and not DEV_MODE:
        for err in errors:
            logger.error(f"[CONFIG] FATAL: {err}")
        sys.exit(1)


def _log_startup_summary():
    """Log a human-readable startup summary after store is initialized."""
    try:
        with store.lock:
            user_count = store.conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
            relay_count = store.conn.execute("SELECT COUNT(*) FROM relays").fetchone()[0]
    except Exception:
        user_count = relay_count = "?"
    logger.info(
        f"[STARTUP] PlatAlgo Bridge v{APP_VERSION} ready — "
        f"db={DB_PATH}, users={user_count}, relays={relay_count}, "
        f"managed_creds={'set' if BRIDGE_CREDS_KEY else 'unset'}, "
        f"dev_mode={DEV_MODE}"
    )

# Run validation on import (not just __main__)
# This ensures module-level issues are caught early
try:
    validate_startup_config()
except RuntimeError as e:
    logger.error(str(e))
    # Don't exit here to allow tests to import the module


# ==================== Input Validation Helpers ====================
def validate_positive_float(value, name: str, max_val: float = None) -> tuple:
    """Validate a positive float value. Returns (value, error_msg)."""
    try:
        val = float(value)
        if val <= 0:
            return None, f"{name} must be positive"
        if max_val is not None and val > max_val:
            return None, f"{name} exceeds maximum ({max_val})"
        return val, None
    except (TypeError, ValueError):
        return None, f"invalid {name}"


def validate_positive_int(value, name: str) -> tuple:
    """Validate a positive integer value. Returns (value, error_msg)."""
    try:
        val = int(value)
        if val <= 0:
            return None, f"{name} must be positive"
        return val, None
    except (TypeError, ValueError):
        return None, f"invalid {name}"


def validate_string(value, name: str, min_len: int = 1, max_len: int = 500) -> tuple:
    """Validate a non-empty string. Returns (value, error_msg)."""
    if value is None:
        return None, f"missing {name}"
    val = str(value).strip()
    if len(val) < min_len:
        return None, f"{name} too short (min {min_len})"
    if len(val) > max_len:
        return None, f"{name} too long (max {max_len})"
    return val, None


# Cache for .env file modification time
_env_mtime = 0
_env_path = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), ".env")

def _reload_env_if_changed():
    """Reload .env file if it has been modified."""
    global _env_mtime
    try:
        mtime = _os.path.getmtime(_env_path)
        if mtime > _env_mtime:
            load_dotenv(_env_path, override=True)
            _env_mtime = mtime
            logger.info(f"Reloaded .env (modified at {mtime})")
    except Exception as e:
        logger.warning(f"Failed to reload .env: {e}")


def get_public_base_url():
    """Return externally reachable base URL for webhook links."""
    # Hot-reload .env if changed
    _reload_env_if_changed()
    public_url = os.getenv("BRIDGE_PUBLIC_URL", "").rstrip("/")
    if public_url:
        return public_url
    proto = request.headers.get("X-Forwarded-Proto") or request.scheme
    host = request.headers.get("X-Forwarded-Host") or request.headers.get("Host")
    if host:
        return f"{proto}://{host}".rstrip("/")
    return request.url_root.rstrip("/")

# ==================== Persistence Store ====================

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
    def __init__(self, user_id: str, relay_id: str, action: str, symbol: str, size: float, sl=None, tp=None, script_name=None):
        self.id = str(uuid.uuid4())
        self.user_id = user_id
        self.relay_id = relay_id
        self.action = action
        self.symbol = symbol
        self.size = size
        self.sl = sl
        self.tp = tp
        self.script_name = script_name or "Uncategorized"
        self.status = CommandStatus.QUEUED
        self.created_at = time.time()
        self.ttl = DEFAULT_COMMAND_TTL
        self.result = None
        self.delivered_at = None
        self.executed_at = None
        self.magic = None
        self.max_lot_size = None

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
            "script_name": self.script_name,
            "status": self.status.value,
            "created_at": self.created_at,
            "delivered_at": self.delivered_at,
            "executed_at": self.executed_at,
            "result": self.result,
            "magic": self.magic,
            "max_lot_size": self.max_lot_size,
        }

    @classmethod
    def from_row(cls, row: sqlite3.Row):
        cmd = cls(
            user_id=row["user_id"],
            relay_id=row["relay_id"],
            action=row["action"],
            symbol=row["symbol"],
            size=row["size"],
            sl=row["sl"],
            tp=row["tp"],
            script_name=row["script_name"],
        )
        cmd.id = row["id"]
        cmd.status = CommandStatus(row["status"])
        cmd.created_at = row["created_at"]
        cmd.ttl = row["ttl"]
        cmd.result = json.loads(row["result_json"]) if row["result_json"] else None
        cmd.delivered_at = row["delivered_at"]
        cmd.executed_at = row["executed_at"]
        return cmd


def hash_secret(user_id: str, raw_secret: str) -> str:
    payload = f"{AUTH_SALT}:{user_id}:{raw_secret}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def verify_admin_credentials(username: str, password: str) -> bool:
    if username != ADMIN_USERNAME:
        return False
    if not ADMIN_PASSWORD_HASH:
        return False  # no hashed password configured — admin panel disabled
    return check_password_hash(ADMIN_PASSWORD_HASH, password)


def get_fernet():
    if not BRIDGE_CREDS_KEY or Fernet is None:
        return None
    try:
        key_bytes = BRIDGE_CREDS_KEY.encode("utf-8")
        if len(key_bytes) != 44:
            key_bytes = base64.urlsafe_b64encode(hashlib.sha256(key_bytes).digest())
        return Fernet(key_bytes)
    except Exception as exc:
        logger.error(f"Invalid BRIDGE_CREDS_KEY: {exc}")
        return None


def encrypt_secret(raw: str) -> str:
    fernet = get_fernet()
    if not fernet:
        logger.warning("BRIDGE_CREDS_KEY not set — storing credentials unencrypted. Set BRIDGE_CREDS_KEY for production.")
        return "plain:" + raw
    return "enc:" + fernet.encrypt(raw.encode("utf-8")).decode("utf-8")


def decrypt_secret(token: str) -> str:
    if token.startswith("plain:"):
        return token[6:]
    if token.startswith("enc:"):
        fernet = get_fernet()
        if not fernet:
            raise RuntimeError("Credential was encrypted but BRIDGE_CREDS_KEY is not set. Set the key to decrypt.")
        return fernet.decrypt(token[4:].encode("utf-8")).decode("utf-8")
    # Legacy: no prefix — try fernet decrypt, fall back to plaintext
    fernet = get_fernet()
    if fernet:
        try:
            return fernet.decrypt(token.encode("utf-8")).decode("utf-8")
        except Exception:
            pass
    return token


class BridgeStore:
    """SQLite-backed store for users, relays, and commands."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self.lock = RLock()  # Use RLock for re-entrant locking
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        with self.conn:
            self.conn.execute("PRAGMA journal_mode=WAL")
            self.conn.execute("PRAGMA foreign_keys=ON")
        self._init_schema()
        self._bootstrap_users_from_env()

    def _init_schema(self):
        with self.conn:
            self.conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS users (
                    user_id TEXT PRIMARY KEY,
                    api_key_hash TEXT NOT NULL,
                    password_hash TEXT,
                    webhook_token TEXT,
                    created_at REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS scripts (
                    script_code TEXT PRIMARY KEY,
                    script_name TEXT NOT NULL,
                    active INTEGER NOT NULL DEFAULT 1,
                    created_at REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS user_scripts (
                    user_id TEXT NOT NULL,
                    script_code TEXT NOT NULL,
                    purchased_at REAL NOT NULL,
                    PRIMARY KEY (user_id, script_code),
                    FOREIGN KEY (user_id) REFERENCES users(user_id),
                    FOREIGN KEY (script_code) REFERENCES scripts(script_code)
                );

                CREATE TABLE IF NOT EXISTS relays (
                    user_id TEXT NOT NULL,
                    relay_id TEXT NOT NULL,
                    token_hash TEXT NOT NULL,
                    relay_type TEXT NOT NULL,
                    state TEXT NOT NULL,
                    last_heartbeat REAL NOT NULL,
                    registered_at REAL NOT NULL,
                    metadata_json TEXT,
                    PRIMARY KEY (user_id, relay_id),
                    FOREIGN KEY (user_id) REFERENCES users(user_id)
                );

                CREATE TABLE IF NOT EXISTS managed_accounts (
                    user_id TEXT PRIMARY KEY,
                    mt5_login INTEGER NOT NULL,
                    mt5_password_enc TEXT NOT NULL,
                    mt5_server TEXT NOT NULL,
                    mt5_path TEXT,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    updated_at REAL NOT NULL,
                    FOREIGN KEY (user_id) REFERENCES users(user_id)
                );

                CREATE TABLE IF NOT EXISTS user_settings (
                    user_id TEXT PRIMARY KEY,
                    max_lot_size REAL NOT NULL DEFAULT 0.5,
                    rate_limit_max_trades INTEGER NOT NULL DEFAULT 5,
                    rate_limit_window_secs INTEGER NOT NULL DEFAULT 60,
                    notifications_enabled INTEGER NOT NULL DEFAULT 0,
                    telegram_bot_token TEXT,
                    telegram_chat_id TEXT,
                    discord_webhook_url TEXT,
                    updated_at REAL NOT NULL,
                    FOREIGN KEY (user_id) REFERENCES users(user_id)
                );

                CREATE TABLE IF NOT EXISTS commands (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    relay_id TEXT NOT NULL,
                    action TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    size REAL NOT NULL,
                    sl REAL,
                    tp REAL,
                    script_name TEXT NOT NULL DEFAULT 'Uncategorized',
                    status TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    ttl REAL NOT NULL,
                    result_json TEXT,
                    delivered_at REAL,
                    executed_at REAL,
                    FOREIGN KEY (user_id, relay_id) REFERENCES relays(user_id, relay_id)
                );

                CREATE INDEX IF NOT EXISTS idx_relays_user ON relays(user_id);
                CREATE INDEX IF NOT EXISTS idx_commands_user_relay_status ON commands(user_id, relay_id, status);
                CREATE INDEX IF NOT EXISTS idx_commands_user_script ON commands(user_id, script_name);
                CREATE INDEX IF NOT EXISTS idx_commands_user_script_time ON commands(user_id, script_name, created_at);

                -- FIX 6: Additional performance indexes for tables in first block
                CREATE INDEX IF NOT EXISTS idx_commands_created ON commands(created_at);
                CREATE INDEX IF NOT EXISTS idx_commands_status_created ON commands(status, created_at);
                CREATE INDEX IF NOT EXISTS idx_relays_heartbeat ON relays(user_id, last_heartbeat);
                """
            )
        self._migrate_schema_if_needed()
        self._bootstrap_scripts_from_env()
        self._bootstrap_user_script_assignments_from_env()

    def _migrate_schema_if_needed(self):
        if not self._has_column("users", "password_hash"):
            with self.lock, self.conn:
                self.conn.execute("ALTER TABLE users ADD COLUMN password_hash TEXT")

        if not self._has_column("users", "webhook_token"):
            with self.lock, self.conn:
                self.conn.execute("ALTER TABLE users ADD COLUMN webhook_token TEXT")

        if not self._has_column("commands", "script_name"):
            with self.lock, self.conn:
                self.conn.execute("ALTER TABLE commands ADD COLUMN script_name TEXT NOT NULL DEFAULT 'Uncategorized'")

        # Section 2: default lot/SL/TP per user
        for col, coltype in [
            ("default_lot_size", "REAL"),
            ("default_sl_pips", "REAL"),
            ("default_tp_pips", "REAL"),
            ("private_chat_id", "TEXT"),
        ]:
            if not self._has_column("user_settings", col):
                with self.lock, self.conn:
                    self.conn.execute(f"ALTER TABLE user_settings ADD COLUMN {col} {coltype}")

        if not self._has_column("users", "magic_number"):
            with self.lock, self.conn:
                self.conn.execute("ALTER TABLE users ADD COLUMN magic_number INTEGER")
            # Assign magic numbers to existing users that don't have one
            with self.lock:
                users_without = self.conn.execute(
                    "SELECT user_id FROM users WHERE magic_number IS NULL"
                ).fetchall()
            for row in users_without:
                magic = self._generate_unique_magic_number()
                with self.lock, self.conn:
                    self.conn.execute(
                        "UPDATE users SET magic_number = ? WHERE user_id = ? AND magic_number IS NULL",
                        (magic, row["user_id"]),
                    )

        for col, coltype, default in [
            ("max_daily_loss_pct", "REAL", "5.0"),
            ("consecutive_loss_limit", "INTEGER", "5"),
            ("circuit_broken", "INTEGER", "0"),
            ("circuit_broken_at", "REAL", "NULL"),
        ]:
            if not self._has_column("user_settings", col):
                with self.lock, self.conn:
                    if default == "NULL":
                        self.conn.execute(f"ALTER TABLE user_settings ADD COLUMN {col} {coltype}")
                    else:
                        self.conn.execute(f"ALTER TABLE user_settings ADD COLUMN {col} {coltype} DEFAULT {default}")

        with self.lock, self.conn:
            self.conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS oauth_identities (
                    provider TEXT NOT NULL,
                    provider_user_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    PRIMARY KEY (provider, provider_user_id),
                    FOREIGN KEY (user_id) REFERENCES users(user_id)
                );

                CREATE TABLE IF NOT EXISTS oauth_desktop_tokens (
                    state TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    api_key TEXT NOT NULL,
                    relay_id TEXT,
                    created_at REAL NOT NULL,
                    expires_at REAL NOT NULL,
                    FOREIGN KEY (user_id) REFERENCES users(user_id)
                );

                CREATE TABLE IF NOT EXISTS telegram_channels (
                    channel_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    chat_id TEXT NOT NULL,
                    chat_title TEXT,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    risk_pct REAL NOT NULL DEFAULT 1.0,
                    max_trades_per_day INTEGER NOT NULL DEFAULT 10,
                    allowed_symbols TEXT,
                    script_name TEXT NOT NULL DEFAULT 'Telegram',
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    UNIQUE(user_id, chat_id),
                    FOREIGN KEY (user_id) REFERENCES users(user_id)
                );

                CREATE TABLE IF NOT EXISTS telegram_signal_log (
                    log_id TEXT PRIMARY KEY,
                    channel_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    telegram_message_id INTEGER,
                    raw_text TEXT NOT NULL,
                    parsed_action TEXT,
                    parsed_symbol TEXT,
                    parsed_entry REAL,
                    parsed_sl REAL,
                    parsed_tp TEXT,
                    parse_confidence REAL,
                    execution_status TEXT,
                    execution_detail TEXT,
                    command_id TEXT,
                    created_at REAL NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_tg_channels_chat
                    ON telegram_channels(chat_id);
                CREATE INDEX IF NOT EXISTS idx_tg_channels_user
                    ON telegram_channels(user_id);
                CREATE INDEX IF NOT EXISTS idx_tg_signal_log_channel
                    ON telegram_signal_log(channel_id, created_at);
                CREATE INDEX IF NOT EXISTS idx_tg_signal_log_user
                    ON telegram_signal_log(user_id, created_at);

                -- Section 5: Telegram account linking
                CREATE TABLE IF NOT EXISTS telegram_users (
                    telegram_user_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    private_chat_id TEXT NOT NULL,
                    username TEXT,
                    linked_at REAL NOT NULL,
                    FOREIGN KEY (user_id) REFERENCES users(user_id)
                );

                -- Section 5: One-time tokens for /start linking flow
                CREATE TABLE IF NOT EXISTS telegram_link_tokens (
                    token TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    expires_at REAL NOT NULL
                );

                -- Section 3: Per-user symbol whitelist
                CREATE TABLE IF NOT EXISTS user_allowed_symbols (
                    user_id TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    added_at REAL NOT NULL,
                    PRIMARY KEY (user_id, symbol),
                    FOREIGN KEY (user_id) REFERENCES users(user_id)
                );

                CREATE INDEX IF NOT EXISTS idx_telegram_users_user
                    ON telegram_users(user_id);

                CREATE TABLE IF NOT EXISTS invite_codes (
                    code TEXT PRIMARY KEY,
                    created_at REAL NOT NULL,
                    expires_at REAL,
                    used_at REAL,
                    used_by TEXT
                );

                CREATE TABLE IF NOT EXISTS signal_idempotency (
                    idem_key TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    PRIMARY KEY (idem_key, user_id)
                );

                CREATE TABLE IF NOT EXISTS user_subscriptions (
                    user_id TEXT PRIMARY KEY,
                    plan TEXT NOT NULL DEFAULT 'free',
                    started_at REAL NOT NULL,
                    expires_at REAL,
                    stripe_customer_id TEXT,
                    stripe_subscription_id TEXT,
                    FOREIGN KEY (user_id) REFERENCES users(user_id)
                );

                -- FIX 6: Indexes for tables created in migrate block
                CREATE INDEX IF NOT EXISTS idx_tg_signal_status ON telegram_signal_log(user_id, execution_status, created_at);
                CREATE INDEX IF NOT EXISTS idx_tg_signal_created ON telegram_signal_log(created_at);
                CREATE INDEX IF NOT EXISTS idx_oauth_tokens_expires ON oauth_desktop_tokens(expires_at);
                CREATE INDEX IF NOT EXISTS idx_signal_idem_created ON signal_idempotency(created_at);
                """
            )

    def _has_column(self, table: str, column: str) -> bool:
        with self.lock:
            rows = self.conn.execute(f"PRAGMA table_info({table})").fetchall()
        return any(row["name"] == column for row in rows)

    def _bootstrap_users_from_env(self):
        users_json = os.getenv("BRIDGE_USERS_JSON", "")
        if not users_json:
            logger.warning("BRIDGE_USERS_JSON is empty. No users are provisioned yet.")
            return

        try:
            users = json.loads(users_json)
            if not isinstance(users, dict):
                logger.error("BRIDGE_USERS_JSON must be a JSON object: {\"user\":\"api_key\"}")
                return
            for user_id, api_key in users.items():
                if not user_id or not api_key:
                    continue
                self.upsert_user(str(user_id), str(api_key))
            logger.info(f"Bootstrapped {len(users)} user(s) from BRIDGE_USERS_JSON")
        except json.JSONDecodeError as exc:
            logger.error(f"Invalid BRIDGE_USERS_JSON: {exc}")

    def _bootstrap_scripts_from_env(self):
        default_catalog = [
            {"script_code": "default-script", "script_name": "Default Strategy"}
        ]
        scripts_json = os.getenv("BRIDGE_SCRIPTS_JSON", "")
        scripts = default_catalog
        if scripts_json:
            try:
                parsed = json.loads(scripts_json)
                if isinstance(parsed, list) and parsed:
                    scripts = parsed
            except json.JSONDecodeError as exc:
                logger.warning(f"Invalid BRIDGE_SCRIPTS_JSON, using defaults: {exc}")

        for script in scripts:
            code = str(script.get("script_code", "")).strip()
            name = str(script.get("script_name", code)).strip()
            if code:
                self.upsert_script(code, name)

    def _bootstrap_user_script_assignments_from_env(self):
        assignments_json = os.getenv("BRIDGE_USER_SCRIPT_ASSIGNMENTS_JSON", "")
        if not assignments_json:
            return
        try:
            assignments = json.loads(assignments_json)
            if not isinstance(assignments, dict):
                return
            for user_id, scripts in assignments.items():
                if not isinstance(scripts, list):
                    continue
                for script_code in scripts:
                    self.assign_script_to_user(str(user_id), str(script_code))
        except json.JSONDecodeError as exc:
            logger.warning(f"Invalid BRIDGE_USER_SCRIPT_ASSIGNMENTS_JSON: {exc}")

    def upsert_user(self, user_id: str, api_key: str):
        api_key_hash = hash_secret(user_id, api_key)
        webhook_token = secrets.token_urlsafe(24)
        with self.lock, self.conn:
            self.conn.execute(
                """
                INSERT INTO users (user_id, api_key_hash, webhook_token, created_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET api_key_hash=excluded.api_key_hash
                """,
                (user_id, api_key_hash, webhook_token, time.time()),
            )
        self.ensure_user_settings(user_id)
        # Assign magic number if not set
        with self.lock:
            row = self.conn.execute("SELECT magic_number FROM users WHERE user_id = ?", (user_id,)).fetchone()
        if row and row["magic_number"] is None:
            magic = self._generate_unique_magic_number()
            with self.lock, self.conn:
                self.conn.execute("UPDATE users SET magic_number = ? WHERE user_id = ?", (magic, user_id))
        with self.lock, self.conn:
            self.conn.execute(
                "INSERT OR IGNORE INTO user_subscriptions (user_id, plan, started_at) VALUES (?, 'free', ?)",
                (user_id, time.time()),
            )

    def register_dashboard_user(self, user_id: str, password: str) -> str:
        if self.user_exists(user_id):
            raise ValueError("User already exists")
        api_key = secrets.token_urlsafe(24)
        webhook_token = secrets.token_urlsafe(24)
        api_key_hash = hash_secret(user_id, api_key)
        password_hash = generate_password_hash(password)
        now = time.time()
        with self.lock, self.conn:
            self.conn.execute(
                """
                INSERT INTO users (user_id, api_key_hash, password_hash, webhook_token, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (user_id, api_key_hash, password_hash, webhook_token, now),
            )
        self.ensure_user_settings(user_id)
        magic = self._generate_unique_magic_number()
        with self.lock, self.conn:
            self.conn.execute("UPDATE users SET magic_number = ? WHERE user_id = ?", (magic, user_id))
        with self.lock, self.conn:
            self.conn.execute(
                "INSERT OR IGNORE INTO user_subscriptions (user_id, plan, started_at) VALUES (?, 'free', ?)",
                (user_id, time.time()),
            )
        return api_key

    def regenerate_api_key(self, user_id: str) -> str:
        """Generate a new API key for an existing user."""
        api_key = secrets.token_urlsafe(24)
        api_key_hash = hash_secret(user_id, api_key)
        with self.lock, self.conn:
            self.conn.execute(
                "UPDATE users SET api_key_hash = ? WHERE user_id = ?",
                (api_key_hash, user_id),
            )
        return api_key

    def ensure_user_settings(self, user_id: str):
        with self.lock, self.conn:
            self.conn.execute(
                """
                INSERT INTO user_settings (user_id, updated_at)
                VALUES (?, ?)
                ON CONFLICT(user_id) DO NOTHING
                """,
                (user_id, time.time()),
            )

    def get_user_settings(self, user_id: str) -> dict:
        self.ensure_user_settings(user_id)
        with self.lock:
            row = self.conn.execute(
                """
                SELECT max_lot_size, rate_limit_max_trades, rate_limit_window_secs,
                       notifications_enabled, telegram_bot_token, telegram_chat_id,
                       discord_webhook_url, default_lot_size, default_sl_pips,
                       default_tp_pips, private_chat_id
                FROM user_settings
                WHERE user_id = ?
                """,
                (user_id,),
            ).fetchone()
        return dict(row) if row else {
            "max_lot_size": 0.5,
            "rate_limit_max_trades": 5,
            "rate_limit_window_secs": 60,
            "notifications_enabled": 0,
            "telegram_bot_token": "",
            "telegram_chat_id": "",
            "discord_webhook_url": "",
            "default_lot_size": None,
            "default_sl_pips": None,
            "default_tp_pips": None,
            "private_chat_id": None,
        }

    def update_user_settings(self, user_id: str, updates: dict):
        self.ensure_user_settings(user_id)
        allowed = {
            "max_lot_size",
            "rate_limit_max_trades",
            "rate_limit_window_secs",
            "notifications_enabled",
            "telegram_bot_token",
            "telegram_chat_id",
            "discord_webhook_url",
            "default_lot_size",
            "default_sl_pips",
            "default_tp_pips",
            "private_chat_id",
        }
        fields = [k for k in updates.keys() if k in allowed]
        if not fields:
            return

        set_clause = ", ".join([f"{f} = ?" for f in fields])
        values = [updates[f] for f in fields]
        values.extend([time.time(), user_id])
        with self.lock, self.conn:
            self.conn.execute(
                f"UPDATE user_settings SET {set_clause}, updated_at = ? WHERE user_id = ?",
                values,
            )

    def count_recent_script_commands(self, user_id: str, script_name: str, window_secs: int) -> int:
        since_ts = time.time() - max(1, int(window_secs))
        with self.lock:
            row = self.conn.execute(
                """
                SELECT COUNT(*) AS cnt
                FROM commands
                WHERE user_id = ?
                  AND script_name = ?
                  AND created_at >= ?
                  AND action IN ('BUY', 'SELL')
                """,
                (user_id, script_name, since_ts),
            ).fetchone()
        return int(row["cnt"]) if row else 0

    def get_or_create_webhook_token(self, user_id: str) -> str:
        with self.lock:
            row = self.conn.execute("SELECT webhook_token FROM users WHERE user_id = ?", (user_id,)).fetchone()
        if not row:
            return ""
        token = row["webhook_token"]
        if token:
            return token

        token = secrets.token_urlsafe(24)
        with self.lock, self.conn:
            self.conn.execute("UPDATE users SET webhook_token = ? WHERE user_id = ?", (token, user_id))
        return token

    def get_user_id_by_webhook_token(self, webhook_token: str) -> Optional[str]:
        with self.lock:
            row = self.conn.execute(
                "SELECT user_id FROM users WHERE webhook_token = ?",
                (webhook_token,),
            ).fetchone()
        return row["user_id"] if row else None

    def verify_dashboard_login(self, user_id: str, password: str) -> bool:
        with self.lock:
            row = self.conn.execute(
                "SELECT password_hash FROM users WHERE user_id = ?",
                (user_id,),
            ).fetchone()
        if not row or not row["password_hash"]:
            return False
        return check_password_hash(row["password_hash"], password)

    def get_user_by_oauth(self, provider: str, provider_user_id: str):
        """Return user_id for a linked OAuth identity, or None."""
        with self.lock:
            row = self.conn.execute(
                "SELECT user_id FROM oauth_identities WHERE provider = ? AND provider_user_id = ?",
                (provider, str(provider_user_id)),
            ).fetchone()
        return row["user_id"] if row else None

    def register_oauth_user(self, provider: str, provider_user_id: str, email: str) -> tuple:
        """Create a user from OAuth info and link the identity. Returns (user_id, api_key)."""
        # Derive a unique user_id from the email prefix
        base = (email.split("@")[0] if email else "user").lower()
        # Strip non-alphanumeric chars
        base = "".join(c for c in base if c.isalnum() or c in "-_")[:30] or "user"
        user_id = base
        suffix = 1
        while self.user_exists(user_id):
            user_id = f"{base}{suffix}"
            suffix += 1

        api_key = secrets.token_urlsafe(24)
        webhook_token = secrets.token_urlsafe(24)
        api_key_hash = hash_secret(user_id, api_key)
        now = time.time()
        with self.lock, self.conn:
            self.conn.execute(
                """
                INSERT INTO users (user_id, api_key_hash, webhook_token, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (user_id, api_key_hash, webhook_token, now),
            )
            self.conn.execute(
                "INSERT INTO oauth_identities (provider, provider_user_id, user_id, created_at) VALUES (?, ?, ?, ?)",
                (provider, str(provider_user_id), user_id, now),
            )
        self.ensure_user_settings(user_id)
        self.assign_script_to_user(user_id, "default-script")
        magic = self._generate_unique_magic_number()
        with self.lock, self.conn:
            self.conn.execute("UPDATE users SET magic_number = ? WHERE user_id = ?", (magic, user_id))
        with self.lock, self.conn:
            self.conn.execute(
                "INSERT OR IGNORE INTO user_subscriptions (user_id, plan, started_at) VALUES (?, 'free', ?)",
                (user_id, time.time()),
            )
        return user_id, api_key

    def upsert_desktop_token(self, state: str, user_id: str, api_key: str, relay_id: str = None, ttl: int = DESKTOP_OAUTH_STATE_TTL):
        """Store a short-lived token for desktop OAuth handoff."""
        now = time.time()
        expires_at = now + max(60, min(ttl, 900))
        with self.lock, self.conn:
            self.conn.execute(
                """
                INSERT INTO oauth_desktop_tokens (state, user_id, api_key, relay_id, created_at, expires_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(state) DO UPDATE SET user_id=excluded.user_id, api_key=excluded.api_key,
                    relay_id=excluded.relay_id, created_at=excluded.created_at, expires_at=excluded.expires_at
                """,
                (state, user_id, api_key, relay_id or "", now, expires_at),
            )

    def consume_desktop_token(self, state: str):
        """Consume and delete a desktop token if valid and unexpired.

        Returns (token_dict_or_None, expired_bool).
        """
        now = time.time()
        with self.lock, self.conn:
            row = self.conn.execute(
                "SELECT user_id, api_key, relay_id, expires_at FROM oauth_desktop_tokens WHERE state = ?",
                (state,),
            ).fetchone()
            if not row:
                return None, False
            if row[3] < now:
                self.conn.execute("DELETE FROM oauth_desktop_tokens WHERE state = ?", (state,))
                return None, True
            self.conn.execute("DELETE FROM oauth_desktop_tokens WHERE state = ?", (state,))
        return {"user_id": row[0], "api_key": row[1], "relay_id": row[2] or None}, False

    def link_oauth_identity(self, provider: str, provider_user_id: str, user_id: str):
        """Link an OAuth identity to an existing user."""
        with self.lock, self.conn:
            self.conn.execute(
                """
                INSERT OR IGNORE INTO oauth_identities (provider, provider_user_id, user_id, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (provider, str(provider_user_id), user_id, time.time()),
            )

    def upsert_script(self, script_code: str, script_name: str):
        with self.lock, self.conn:
            self.conn.execute(
                """
                INSERT INTO scripts (script_code, script_name, active, created_at)
                VALUES (?, ?, 1, ?)
                ON CONFLICT(script_code) DO UPDATE SET script_name=excluded.script_name, active=1
                """,
                (script_code, script_name, time.time()),
            )

    def list_scripts(self) -> list:
        with self.lock:
            rows = self.conn.execute(
                "SELECT script_code, script_name FROM scripts WHERE active = 1 ORDER BY script_name ASC"
            ).fetchall()
        return [dict(row) for row in rows]

    def list_all_scripts(self) -> list:
        with self.lock:
            rows = self.conn.execute(
                """
                SELECT script_code, script_name, active, created_at
                FROM scripts
                ORDER BY script_name ASC
                """
            ).fetchall()
        output = []
        for row in rows:
            output.append({
                "script_code": row["script_code"],
                "script_name": row["script_name"],
                "active": bool(row["active"]),
                "created_at": datetime.fromtimestamp(row["created_at"], tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
            })
        return output

    def script_exists(self, script_code: str) -> bool:
        with self.lock:
            row = self.conn.execute(
                "SELECT 1 FROM scripts WHERE script_code = ? AND active = 1",
                (script_code,),
            ).fetchone()
        return row is not None

    def set_script_active(self, script_code: str, active: bool) -> bool:
        with self.lock, self.conn:
            cursor = self.conn.execute(
                "UPDATE scripts SET active = ? WHERE script_code = ?",
                (1 if active else 0, script_code),
            )
        return cursor.rowcount > 0

    def upsert_managed_account(self, user_id: str, mt5_login: int, mt5_password: str, mt5_server: str, mt5_path: str = ""):
        password_enc = encrypt_secret(mt5_password)
        now = time.time()
        with self.lock, self.conn:
            self.conn.execute(
                """
                INSERT INTO managed_accounts (user_id, mt5_login, mt5_password_enc, mt5_server, mt5_path, enabled, updated_at)
                VALUES (?, ?, ?, ?, ?, 1, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    mt5_login=excluded.mt5_login,
                    mt5_password_enc=excluded.mt5_password_enc,
                    mt5_server=excluded.mt5_server,
                    mt5_path=excluded.mt5_path,
                    enabled=1,
                    updated_at=excluded.updated_at
                """,
                (user_id, mt5_login, password_enc, mt5_server, mt5_path or "", now),
            )
        managed_relay_id = f"{MANAGED_RELAY_PREFIX}{user_id}"
        self.register_relay(user_id, managed_relay_id, relay_type="managed-vps")

    def get_managed_account(self, user_id: str):
        with self.lock:
            row = self.conn.execute(
                """
                SELECT user_id, mt5_login, mt5_password_enc, mt5_server, mt5_path, enabled, updated_at
                FROM managed_accounts
                WHERE user_id = ?
                """,
                (user_id,),
            ).fetchone()
        return dict(row) if row else None

    def get_all_managed_accounts(self) -> list:
        """Return all enabled managed accounts (for session restore at startup)."""
        with self.lock:
            rows = self.conn.execute(
                """
                SELECT user_id, mt5_login, mt5_password_enc, mt5_server, mt5_path
                FROM managed_accounts WHERE enabled = 1
                """
            ).fetchall()
        return [dict(r) for r in rows]

    def is_managed_enabled(self, user_id: str) -> bool:
        row = self.get_managed_account(user_id)
        return bool(row and row.get("enabled") == 1)

    def assign_script_to_user(self, user_id: str, script_code: str):
        now = time.time()
        with self.lock, self.conn:
            self.conn.execute(
                """
                INSERT INTO user_scripts (user_id, script_code, purchased_at)
                VALUES (?, ?, ?)
                ON CONFLICT(user_id, script_code) DO NOTHING
                """,
                (user_id, script_code, now),
            )

    def get_user_scripts(self, user_id: str) -> list:
        with self.lock:
            rows = self.conn.execute(
                """
                SELECT us.script_code, s.script_name, us.purchased_at
                FROM user_scripts us
                JOIN scripts s ON s.script_code = us.script_code
                WHERE us.user_id = ?
                ORDER BY us.purchased_at DESC
                """,
                (user_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_users(self) -> list:
        with self.lock:
            rows = self.conn.execute(
                """
                SELECT user_id, created_at
                FROM users
                ORDER BY user_id ASC
                """
            ).fetchall()
        output = []
        for row in rows:
            output.append({
                "user_id": row["user_id"],
                "created_at": datetime.fromtimestamp(row["created_at"], tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
            })
        return output

    def get_all_user_script_assignments(self) -> list:
        with self.lock:
            rows = self.conn.execute(
                """
                SELECT us.user_id, us.script_code, s.script_name, us.purchased_at
                FROM user_scripts us
                JOIN scripts s ON s.script_code = us.script_code
                ORDER BY us.user_id ASC, us.purchased_at DESC
                """
            ).fetchall()
        return [
            {
                "user_id": row["user_id"],
                "script_code": row["script_code"],
                "script_name": row["script_name"],
                "purchased_at": datetime.fromtimestamp(row["purchased_at"], tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
            }
            for row in rows
        ]

    def get_dashboard_data(self, user_id: str) -> dict:
        relays = self.list_relays(user_id)
        relays_for_dashboard = {}
        for relay_id, relay_info in relays.items():
            relays_for_dashboard[relay_id] = {
                "state": relay_info["state"],
                "relay_type": relay_info["relay_type"],
                "metadata": relay_info.get("metadata", {}),
                "last_heartbeat_raw": relay_info["last_heartbeat"],
                "last_heartbeat": datetime.fromtimestamp(relay_info["last_heartbeat"], tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
            }

        user_scripts = self.get_user_scripts(user_id)
        if not user_scripts:
            self.assign_script_to_user(user_id, "default-script")
            user_scripts = self.get_user_scripts(user_id)

        script_metrics = []
        for script in user_scripts:
            script_name = script["script_name"]
            with self.lock:
                totals = self.conn.execute(
                    """
                    SELECT
                        COUNT(*) AS signals_count,
                        SUM(CASE WHEN status = ? THEN 1 ELSE 0 END) AS executed_count
                    FROM commands
                    WHERE user_id = ? AND script_name = ?
                    """,
                    (CommandStatus.EXECUTED.value, user_id, script_name),
                ).fetchone()

                recent_rows = self.conn.execute(
                    """
                    SELECT id, action, symbol, size, status, relay_id, created_at, executed_at, result_json
                    FROM commands
                    WHERE user_id = ? AND script_name = ?
                    ORDER BY created_at DESC
                    LIMIT 20
                    """,
                    (user_id, script_name),
                ).fetchall()

            recent_signals = []
            for row in recent_rows:
                result_data = json.loads(row["result_json"]) if row["result_json"] else {}
                recent_signals.append({
                    "id": row["id"],
                    "action": row["action"],
                    "symbol": row["symbol"],
                    "size": row["size"],
                    "status": row["status"],
                    "relay_id": row["relay_id"],
                    "error_message": result_data.get("error_message") or result_data.get("error") or "",
                    "created_at": datetime.fromtimestamp(row["created_at"], tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
                    "executed_at": datetime.fromtimestamp(row["executed_at"], tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC") if row["executed_at"] else "-",
                })

            script_metrics.append({
                "script_code": script["script_code"],
                "script_name": script_name,
                "purchased_at": datetime.fromtimestamp(script["purchased_at"], tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
                "signals_count": totals["signals_count"] or 0,
                "executed_count": totals["executed_count"] or 0,
                "recent_signals": recent_signals,
            })

        online_count = sum(1 for relay in relays_for_dashboard.values() if relay["state"] == RelayState.ONLINE.value)
        return {
            "user_id": user_id,
            "relay_total": len(relays_for_dashboard),
            "relay_online": online_count,
            "relay_offline": len(relays_for_dashboard) - online_count,
            "relays": relays_for_dashboard,
            "scripts": script_metrics,
        }

    def user_exists(self, user_id: str) -> bool:
        with self.lock:
            row = self.conn.execute("SELECT 1 FROM users WHERE user_id = ?", (user_id,)).fetchone()
        return row is not None

    def verify_api_key(self, user_id: str, api_key: str) -> bool:
        with self.lock:
            row = self.conn.execute("SELECT api_key_hash FROM users WHERE user_id = ?", (user_id,)).fetchone()
        if not row:
            return False
        expected = row["api_key_hash"]
        provided = hash_secret(user_id, api_key)
        return hmac.compare_digest(provided, expected)

    def register_relay(self, user_id: str, relay_id: str, relay_type: str) -> str:
        token = str(uuid.uuid4())
        token_hash = hash_secret(user_id, token)
        now = time.time()
        with self.lock, self.conn:
            self.conn.execute(
                """
                INSERT INTO relays (user_id, relay_id, token_hash, relay_type, state, last_heartbeat, registered_at, metadata_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id, relay_id) DO UPDATE SET
                    token_hash=excluded.token_hash,
                    relay_type=excluded.relay_type,
                    state=excluded.state,
                    last_heartbeat=excluded.last_heartbeat,
                    metadata_json=excluded.metadata_json
                """,
                (user_id, relay_id, token_hash, relay_type, RelayState.UNKNOWN.value, now, now, json.dumps({})),
            )
        logger.info(f"Relay registered: user={user_id}, relay={relay_id}, type={relay_type}")
        return token

    def verify_relay_token(self, user_id: str, relay_id: str, token: str) -> bool:
        with self.lock:
            row = self.conn.execute(
                "SELECT token_hash FROM relays WHERE user_id = ? AND relay_id = ?",
                (user_id, relay_id),
            ).fetchone()
        if not row:
            return False
        provided = hash_secret(user_id, token)
        return hmac.compare_digest(provided, row["token_hash"])

    def heartbeat(self, user_id: str, relay_id: str, metadata: dict = None) -> bool:
        metadata_json = json.dumps(metadata or {})
        now = time.time()
        with self.lock, self.conn:
            cursor = self.conn.execute(
                """
                UPDATE relays
                SET state = ?, last_heartbeat = ?, metadata_json = ?
                WHERE user_id = ? AND relay_id = ?
                """,
                (RelayState.ONLINE.value, now, metadata_json, user_id, relay_id),
            )
        return cursor.rowcount > 0

    def _relay_state_by_heartbeat(self, last_heartbeat: float) -> str:
        if (time.time() - last_heartbeat) > DEFAULT_HEARTBEAT_TIMEOUT:
            return RelayState.OFFLINE.value
        return RelayState.ONLINE.value

    def list_relays(self, user_id: str) -> dict:
        with self.lock:
            rows = self.conn.execute(
                """
                SELECT relay_id, relay_type, last_heartbeat, metadata_json
                FROM relays
                WHERE user_id = ?
                """,
                (user_id,),
            ).fetchall()
        output = {}
        for row in rows:
            output[row["relay_id"]] = {
                "state": self._relay_state_by_heartbeat(row["last_heartbeat"]),
                "last_heartbeat": row["last_heartbeat"],
                "relay_type": row["relay_type"],
                "metadata": json.loads(row["metadata_json"]) if row["metadata_json"] else {},
            }
        return output

    def enqueue(self, cmd: Command):
        with self.lock, self.conn:
            self.conn.execute(
                """
                INSERT INTO commands (
                    id, user_id, relay_id, action, symbol, size, sl, tp, script_name, status,
                    created_at, ttl, result_json, delivered_at, executed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    cmd.id,
                    cmd.user_id,
                    cmd.relay_id,
                    cmd.action,
                    cmd.symbol,
                    cmd.size,
                    cmd.sl,
                    cmd.tp,
                    cmd.script_name,
                    cmd.status.value,
                    cmd.created_at,
                    cmd.ttl,
                    None,
                    None,
                    None,
                ),
            )

    def dequeue(self, user_id: str, relay_id: str, limit: int = COMMAND_DEQUEUE_LIMIT):
        now = time.time()
        with self.lock, self.conn:
            expired_rows = self.conn.execute(
                """
                SELECT id FROM commands
                WHERE user_id = ? AND relay_id = ? AND status = ?
                  AND (? - created_at) > ttl
                """,
                (user_id, relay_id, CommandStatus.QUEUED.value, now),
            ).fetchall()
            for row in expired_rows:
                self.conn.execute(
                    """
                    UPDATE commands
                    SET status = ?, result_json = ?, executed_at = ?
                    WHERE id = ?
                    """,
                    (
                        CommandStatus.FAILED.value,
                        json.dumps({"error": "command expired"}),
                        now,
                        row["id"],
                    ),
                )

            rows = self.conn.execute(
                """
                SELECT * FROM commands
                WHERE user_id = ? AND relay_id = ? AND status = ?
                ORDER BY created_at ASC
                LIMIT ?
                """,
                (user_id, relay_id, CommandStatus.QUEUED.value, limit),
            ).fetchall()

            command_ids = [row["id"] for row in rows]
            if command_ids:
                placeholders = ",".join(["?"] * len(command_ids))
                self.conn.execute(
                    f"""
                    UPDATE commands
                    SET status = ?, delivered_at = ?
                    WHERE id IN ({placeholders})
                    """,
                    [CommandStatus.DELIVERED.value, now, *command_ids],
                )

        commands = [Command.from_row(row) for row in rows]
        for cmd in commands:
            cmd.status = CommandStatus.DELIVERED
            cmd.delivered_at = now
        return commands

    def get_command(self, cmd_id: str):
        with self.lock:
            row = self.conn.execute("SELECT * FROM commands WHERE id = ?", (cmd_id,)).fetchone()
        if not row:
            return None
        return Command.from_row(row)

    def update_result(self, user_id: str, relay_id: str, cmd_id: str, status: CommandStatus, result: dict) -> bool:
        with self.lock, self.conn:
            cursor = self.conn.execute(
                """
                UPDATE commands
                SET status = ?, executed_at = ?, result_json = ?
                WHERE id = ? AND user_id = ? AND relay_id = ?
                """,
                (status.value, time.time(), json.dumps(result or {}), cmd_id, user_id, relay_id),
            )
        return cursor.rowcount > 0

    # ── Telegram channel subscriptions ───────────────────────────────────

    def add_telegram_channel(self, channel_id: str, user_id: str, chat_id: str,
                             chat_title: str = None, risk_pct: float = 1.0,
                             max_trades_per_day: int = 10, allowed_symbols: str = None,
                             script_name: str = "Telegram") -> None:
        now = time.time()
        with self.lock, self.conn:
            self.conn.execute(
                """INSERT INTO telegram_channels
                   (channel_id, user_id, chat_id, chat_title, enabled, risk_pct,
                    max_trades_per_day, allowed_symbols, script_name, created_at, updated_at)
                   VALUES (?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?)""",
                (channel_id, user_id, chat_id, chat_title, risk_pct,
                 max_trades_per_day, allowed_symbols, script_name, now, now),
            )

    def get_telegram_channel(self, channel_id: str) -> dict | None:
        with self.lock:
            row = self.conn.execute(
                "SELECT * FROM telegram_channels WHERE channel_id = ?", (channel_id,)
            ).fetchone()
        return dict(row) if row else None

    def list_telegram_channels(self, user_id: str) -> list[dict]:
        with self.lock:
            rows = self.conn.execute(
                "SELECT * FROM telegram_channels WHERE user_id = ? ORDER BY created_at DESC",
                (user_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_subscriptions_for_chat(self, chat_id: str) -> list[dict]:
        """Get all enabled subscriptions for a given Telegram chat_id (fan-out)."""
        with self.lock:
            rows = self.conn.execute(
                "SELECT * FROM telegram_channels WHERE chat_id = ? AND enabled = 1",
                (chat_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def update_telegram_channel(self, channel_id: str, updates: dict) -> bool:
        allowed = {"chat_title", "enabled", "risk_pct", "max_trades_per_day",
                   "allowed_symbols", "script_name"}
        filtered = {k: v for k, v in updates.items() if k in allowed}
        if not filtered:
            return False
        filtered["updated_at"] = time.time()
        set_clause = ", ".join(f"{k} = ?" for k in filtered)
        values = list(filtered.values()) + [channel_id]
        with self.lock, self.conn:
            cursor = self.conn.execute(
                f"UPDATE telegram_channels SET {set_clause} WHERE channel_id = ?",
                values,
            )
        return cursor.rowcount > 0

    def delete_telegram_channel(self, channel_id: str) -> bool:
        with self.lock, self.conn:
            cursor = self.conn.execute(
                "DELETE FROM telegram_channels WHERE channel_id = ?", (channel_id,)
            )
        return cursor.rowcount > 0

    def count_channel_trades_today(self, channel_id: str) -> int:
        """Count executed signals for a channel in the last 24 hours."""
        cutoff = time.time() - 86400
        with self.lock:
            row = self.conn.execute(
                """SELECT COUNT(*) FROM telegram_signal_log
                   WHERE channel_id = ? AND execution_status = 'executed'
                   AND created_at >= ?""",
                (channel_id, cutoff),
            ).fetchone()
        return row[0] if row else 0

    # ── Telegram signal log ──────────────────────────────────────────────

    def add_telegram_signal_log(self, entry: dict) -> None:
        with self.lock, self.conn:
            self.conn.execute(
                """INSERT INTO telegram_signal_log
                   (log_id, channel_id, user_id, telegram_message_id, raw_text,
                    parsed_action, parsed_symbol, parsed_entry, parsed_sl, parsed_tp,
                    parse_confidence, execution_status, execution_detail, command_id, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (entry["log_id"], entry["channel_id"], entry["user_id"],
                 entry.get("telegram_message_id"), entry["raw_text"],
                 entry.get("parsed_action"), entry.get("parsed_symbol"),
                 entry.get("parsed_entry"), entry.get("parsed_sl"),
                 entry.get("parsed_tp"), entry.get("parse_confidence"),
                 entry.get("execution_status"), entry.get("execution_detail"),
                 entry.get("command_id"), entry["created_at"]),
            )

    def list_telegram_signal_log(self, user_id: str = None, channel_id: str = None,
                                  limit: int = 50) -> list[dict]:
        conditions = []
        params: list = []
        if user_id:
            conditions.append("user_id = ?")
            params.append(user_id)
        if channel_id:
            conditions.append("channel_id = ?")
            params.append(channel_id)
        where = "WHERE " + " AND ".join(conditions) if conditions else ""
        params.append(limit)
        with self.lock:
            rows = self.conn.execute(
                f"SELECT * FROM telegram_signal_log {where} ORDER BY created_at DESC LIMIT ?",
                params,
            ).fetchall()
        return [dict(r) for r in rows]

    def get_channel_open_symbols(self, user_id: str, channel_id: str) -> list[str]:
        """
        Get symbols of positions opened by a specific channel (for channel-scoped close).
        Looks at executed BUY/SELL signals from this channel that don't have a corresponding close.
        """
        with self.lock:
            rows = self.conn.execute(
                """SELECT DISTINCT parsed_symbol FROM telegram_signal_log
                   WHERE user_id = ? AND channel_id = ? AND execution_status = 'executed'
                   AND parsed_action IN ('BUY', 'SELL')
                   AND created_at >= ?
                   ORDER BY created_at DESC""",
                (user_id, channel_id, time.time() - 86400 * 7),  # last 7 days
            ).fetchall()
        return [r["parsed_symbol"] for r in rows if r["parsed_symbol"]]

    def get_channel_command_ids(self, user_id: str, channel_id: str) -> list[str]:
        """Get command IDs of executed trades from a specific channel."""
        with self.lock:
            rows = self.conn.execute(
                """SELECT command_id FROM telegram_signal_log
                   WHERE user_id = ? AND channel_id = ? AND execution_status = 'executed'
                   AND parsed_action IN ('BUY', 'SELL') AND command_id IS NOT NULL
                   AND created_at >= ?
                   ORDER BY created_at DESC""",
                (user_id, channel_id, time.time() - 86400 * 7),
            ).fetchall()
        return [r["command_id"] for r in rows if r["command_id"]]

    # ── Telegram account linking (Section 5) ─────────────────────────────

    def create_telegram_link_token(self, user_id: str, ttl: int = 600) -> str:
        """Create a one-time token for the /start linking flow. Valid for ttl seconds."""
        token = secrets.token_urlsafe(32)
        now = time.time()
        # Clean up old tokens for this user first
        with self.lock, self.conn:
            self.conn.execute("DELETE FROM telegram_link_tokens WHERE user_id = ?", (user_id,))
            self.conn.execute(
                "INSERT INTO telegram_link_tokens (token, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
                (token, user_id, now, now + ttl),
            )
        return token

    def consume_telegram_link_token(self, token: str):
        """Validate and delete a link token. Returns user_id or None."""
        now = time.time()
        with self.lock, self.conn:
            row = self.conn.execute(
                "SELECT user_id, expires_at FROM telegram_link_tokens WHERE token = ?",
                (token,),
            ).fetchone()
            if not row:
                return None
            if row["expires_at"] < now:
                self.conn.execute("DELETE FROM telegram_link_tokens WHERE token = ?", (token,))
                return None
            self.conn.execute("DELETE FROM telegram_link_tokens WHERE token = ?", (token,))
        return row["user_id"]

    def link_telegram_user(self, telegram_user_id: str, user_id: str,
                           private_chat_id: str, username: str = "") -> None:
        """Store the Telegram→account link and update private_chat_id in user_settings."""
        now = time.time()
        with self.lock, self.conn:
            self.conn.execute(
                """INSERT INTO telegram_users (telegram_user_id, user_id, private_chat_id, username, linked_at)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(telegram_user_id) DO UPDATE SET
                       user_id=excluded.user_id,
                       private_chat_id=excluded.private_chat_id,
                       username=excluded.username,
                       linked_at=excluded.linked_at""",
                (telegram_user_id, user_id, private_chat_id, username or "", now),
            )
            # Also persist private_chat_id in user_settings for _send_private()
            self.conn.execute(
                "UPDATE user_settings SET private_chat_id = ?, updated_at = ? WHERE user_id = ?",
                (private_chat_id, now, user_id),
            )

    def get_user_id_by_telegram_id(self, telegram_user_id: str):
        """Resolve a Telegram user_id to a PlatAlgo user_id."""
        with self.lock:
            row = self.conn.execute(
                "SELECT user_id FROM telegram_users WHERE telegram_user_id = ?",
                (telegram_user_id,),
            ).fetchone()
        return row["user_id"] if row else None

    def get_telegram_id_for_user(self, user_id: str):
        """Reverse lookup: PlatAlgo user_id → Telegram user_id."""
        with self.lock:
            row = self.conn.execute(
                "SELECT telegram_user_id FROM telegram_users WHERE user_id = ?",
                (user_id,),
            ).fetchone()
        return row["telegram_user_id"] if row else None

    def unlink_telegram_user(self, telegram_user_id: str) -> None:
        """Remove a Telegram link and clear private_chat_id from user_settings."""
        with self.lock, self.conn:
            row = self.conn.execute(
                "SELECT user_id FROM telegram_users WHERE telegram_user_id = ?",
                (telegram_user_id,),
            ).fetchone()
            if row:
                self.conn.execute(
                    "UPDATE user_settings SET private_chat_id = NULL, updated_at = ? WHERE user_id = ?",
                    (time.time(), row["user_id"]),
                )
            self.conn.execute(
                "DELETE FROM telegram_users WHERE telegram_user_id = ?", (telegram_user_id,)
            )

    def get_private_chat_id_for_user(self, user_id: str):
        """Get the private Telegram chat_id stored from the /start link flow."""
        with self.lock:
            row = self.conn.execute(
                "SELECT private_chat_id FROM user_settings WHERE user_id = ?",
                (user_id,),
            ).fetchone()
        return row["private_chat_id"] if row else None

    # ── User default settings (Section 2) ────────────────────────────────

    def get_user_defaults(self, user_id: str) -> dict:
        """Return default_lot_size, default_sl_pips, default_tp_pips for a user."""
        self.ensure_user_settings(user_id)
        with self.lock:
            row = self.conn.execute(
                "SELECT default_lot_size, default_sl_pips, default_tp_pips FROM user_settings WHERE user_id = ?",
                (user_id,),
            ).fetchone()
        return dict(row) if row else {"default_lot_size": None, "default_sl_pips": None, "default_tp_pips": None}

    def set_user_default(self, user_id: str, field: str, value) -> None:
        """Set a single default value (default_lot_size, default_sl_pips, default_tp_pips)."""
        allowed = {"default_lot_size", "default_sl_pips", "default_tp_pips"}
        if field not in allowed:
            raise ValueError(f"unknown default field: {field}")
        self.ensure_user_settings(user_id)
        with self.lock, self.conn:
            self.conn.execute(
                f"UPDATE user_settings SET {field} = ?, updated_at = ? WHERE user_id = ?",
                (value, time.time(), user_id),
            )

    # ── Per-user symbol whitelist (Section 3) ────────────────────────────

    def get_user_allowed_symbols(self, user_id: str) -> list:
        """Return the user's allowed symbols list. Empty = all symbols allowed."""
        with self.lock:
            rows = self.conn.execute(
                "SELECT symbol FROM user_allowed_symbols WHERE user_id = ? ORDER BY symbol",
                (user_id,),
            ).fetchall()
        return [r["symbol"] for r in rows]

    def add_user_allowed_symbol(self, user_id: str, symbol: str) -> None:
        with self.lock, self.conn:
            self.conn.execute(
                "INSERT OR IGNORE INTO user_allowed_symbols (user_id, symbol, added_at) VALUES (?, ?, ?)",
                (user_id, symbol.upper(), time.time()),
            )

    def remove_user_allowed_symbol(self, user_id: str, symbol: str) -> None:
        with self.lock, self.conn:
            self.conn.execute(
                "DELETE FROM user_allowed_symbols WHERE user_id = ? AND symbol = ?",
                (user_id, symbol.upper()),
            )

    # ── Telegram channel management (Sections 4 / 5 bot commands) ────────

    def get_channels_for_user(self, user_id: str) -> list:
        """Alias for list_telegram_channels."""
        return self.list_telegram_channels(user_id)

    def add_telegram_channel_simple(self, user_id: str, chat_id: str,
                                    chat_title: str = None) -> str:
        """Add a channel subscription for a user (simplified, auto-generates channel_id)."""
        channel_id = str(uuid.uuid4())
        self.add_telegram_channel(
            channel_id=channel_id,
            user_id=user_id,
            chat_id=chat_id,
            chat_title=chat_title or chat_id,
        )
        return channel_id

    def remove_telegram_channel(self, user_id: str, chat_id: str) -> bool:
        """Remove a channel subscription by user_id + chat_id."""
        with self.lock, self.conn:
            cursor = self.conn.execute(
                "DELETE FROM telegram_channels WHERE user_id = ? AND chat_id = ?",
                (user_id, chat_id),
            )
        return cursor.rowcount > 0

    # ── Admin helpers (Section 8) ─────────────────────────────────────────

    def get_all_users_summary(self) -> list:
        """Return a lightweight list of all users for admin /admin users command."""
        with self.lock:
            rows = self.conn.execute(
                """SELECT u.user_id, u.created_at, u.webhook_token,
                          CASE WHEN ma.user_id IS NOT NULL THEN 1 ELSE 0 END as managed,
                          ma.mt5_login, ma.mt5_server
                   FROM users u
                   LEFT JOIN managed_accounts ma ON u.user_id = ma.user_id AND ma.enabled = 1
                   ORDER BY u.created_at DESC""",
            ).fetchall()
        result = []
        for r in rows:
            result.append({
                "user_id": r["user_id"],
                "managed": bool(r["managed"]),
                "mt5_login": r["mt5_login"],
                "mt5_server": r["mt5_server"],
                "webhook_token": r["webhook_token"] or "",
                "created_at_str": datetime.fromtimestamp(r["created_at"], tz=timezone.utc).strftime("%Y-%m-%d"),
            })
        return result

    def get_recent_signal_logs(self, limit: int = 20) -> list:
        """Return recent signal logs across all users for admin view."""
        with self.lock:
            rows = self.conn.execute(
                """SELECT user_id, parsed_action, parsed_symbol, execution_status, created_at
                   FROM telegram_signal_log ORDER BY created_at DESC LIMIT ?""",
                (limit,),
            ).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            d["created_at"] = datetime.fromtimestamp(r["created_at"], tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S") if r["created_at"] else ""
            result.append(d)
        return result

    def get_platform_stats(self) -> dict:
        """Return aggregate platform statistics for admin."""
        cutoff_24h = time.time() - 86400
        with self.lock:
            total_users = self.conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
            managed_count = self.conn.execute(
                "SELECT COUNT(*) FROM managed_accounts WHERE enabled = 1"
            ).fetchone()[0]
            signals_today = self.conn.execute(
                "SELECT COUNT(*) FROM telegram_signal_log WHERE created_at >= ?",
                (cutoff_24h,),
            ).fetchone()[0]
            executed_today = self.conn.execute(
                "SELECT COUNT(*) FROM telegram_signal_log WHERE execution_status = 'executed' AND created_at >= ?",
                (cutoff_24h,),
            ).fetchone()[0]
            active_channels = self.conn.execute(
                "SELECT COUNT(*) FROM telegram_channels WHERE enabled = 1"
            ).fetchone()[0]
        return {
            "total_users": total_users,
            "managed_count": managed_count,
            "signals_today": signals_today,
            "executed_today": executed_today,
            "active_channels": active_channels,
        }

    def get_user_admin_info(self, user_id: str) -> dict | None:
        """Return detailed user info for admin inspection."""
        if not self.user_exists(user_id):
            return None
        with self.lock:
            settings_row = self.conn.execute(
                "SELECT max_lot_size FROM user_settings WHERE user_id = ?", (user_id,)
            ).fetchone()
            created_row = self.conn.execute(
                "SELECT created_at FROM users WHERE user_id = ?", (user_id,)
            ).fetchone()
            channel_count = self.conn.execute(
                "SELECT COUNT(*) FROM telegram_channels WHERE user_id = ?", (user_id,)
            ).fetchone()[0]
            signal_count = self.conn.execute(
                "SELECT COUNT(*) FROM telegram_signal_log WHERE user_id = ?", (user_id,)
            ).fetchone()[0]
            managed = self.conn.execute(
                "SELECT 1 FROM managed_accounts WHERE user_id = ? AND enabled = 1", (user_id,)
            ).fetchone() is not None
        return {
            "user_id": user_id,
            "managed": managed,
            "max_lot_size": settings_row["max_lot_size"] if settings_row else None,
            "channel_count": channel_count,
            "signal_count": signal_count,
            "created_at_str": datetime.fromtimestamp(
                created_row["created_at"], tz=timezone.utc
            ).strftime("%Y-%m-%d %H:%M UTC") if created_row else "?",
        }

    def admin_stop_managed_session(self, user_id: str) -> None:
        """Mark a managed account as disabled. The caller should also stop the session."""
        with self.lock, self.conn:
            self.conn.execute(
                "UPDATE managed_accounts SET enabled = 0, updated_at = ? WHERE user_id = ?",
                (time.time(), user_id),
            )

    # ── Invite Codes ─────────────────────────────────────────────────────────

    def create_invite_code(self, expires_hours=None) -> str:
        import secrets as _sec
        code = _sec.token_urlsafe(16)
        now = time.time()
        expires_at = now + expires_hours * 3600 if expires_hours else None
        with self.lock, self.conn:
            self.conn.execute(
                "INSERT INTO invite_codes (code, created_at, expires_at) VALUES (?, ?, ?)",
                (code, now, expires_at),
            )
        return code

    def validate_invite_code(self, code: str) -> tuple:
        """Returns (valid: bool, reason: str)"""
        with self.lock:
            row = self.conn.execute(
                "SELECT code, used_at, expires_at FROM invite_codes WHERE code = ?",
                (code,),
            ).fetchone()
        if not row:
            return False, "invalid code"
        if row["used_at"] is not None:
            return False, "already used"
        if row["expires_at"] is not None and row["expires_at"] < time.time():
            return False, "expired"
        return True, ""

    def consume_invite_code(self, code: str, user_id: str):
        with self.lock, self.conn:
            self.conn.execute(
                "UPDATE invite_codes SET used_at = ?, used_by = ? WHERE code = ?",
                (time.time(), user_id, code),
            )

    def list_invite_codes(self) -> list:
        with self.lock:
            rows = self.conn.execute(
                "SELECT code, created_at, expires_at, used_at, used_by FROM invite_codes ORDER BY created_at DESC"
            ).fetchall()
        result = []
        for r in rows:
            status = "available"
            if r["used_at"]:
                status = "used"
            elif r["expires_at"] and r["expires_at"] < time.time():
                status = "expired"
            result.append({
                "code": r["code"],
                "created_at": r["created_at"],
                "created_at_str": datetime.fromtimestamp(r["created_at"], tz=timezone.utc).strftime("%Y-%m-%d %H:%M") if r["created_at"] else "",
                "expires_at": r["expires_at"],
                "expires_at_str": datetime.fromtimestamp(r["expires_at"], tz=timezone.utc).strftime("%Y-%m-%d %H:%M") if r["expires_at"] else "",
                "used_at": r["used_at"],
                "used_at_str": datetime.fromtimestamp(r["used_at"], tz=timezone.utc).strftime("%Y-%m-%d %H:%M") if r["used_at"] else "",
                "used_by": r["used_by"],
                "status": status,
            })
        return result

    # ── Magic Numbers ─────────────────────────────────────────────────────────

    def _generate_unique_magic_number(self) -> int:
        import random as _random
        while True:
            magic = _random.randint(100000, 999999)
            row = self.conn.execute("SELECT 1 FROM users WHERE magic_number = ?", (magic,)).fetchone()
            if not row:
                return magic

    def get_user_magic_number(self, user_id: str) -> int:
        with self.lock:
            row = self.conn.execute("SELECT magic_number FROM users WHERE user_id = ?", (user_id,)).fetchone()
        if row and row["magic_number"]:
            return int(row["magic_number"])
        # Generate and save if missing
        magic = self._generate_unique_magic_number()
        with self.lock, self.conn:
            self.conn.execute("UPDATE users SET magic_number = ? WHERE user_id = ?", (magic, user_id))
        return magic

    # ── Signal Idempotency ────────────────────────────────────────────────────

    def check_idempotency(self, idem_key: str, user_id: str, ttl_secs: int = 120) -> bool:
        """Returns True if duplicate (already seen within ttl_secs)."""
        cutoff = time.time() - ttl_secs
        with self.lock:
            row = self.conn.execute(
                "SELECT 1 FROM signal_idempotency WHERE idem_key = ? AND user_id = ? AND created_at > ?",
                (idem_key, user_id, cutoff),
            ).fetchone()
        return row is not None

    def record_idempotency(self, idem_key: str, user_id: str):
        with self.lock, self.conn:
            self.conn.execute(
                "INSERT OR REPLACE INTO signal_idempotency (idem_key, user_id, created_at) VALUES (?, ?, ?)",
                (idem_key, user_id, time.time()),
            )

    def cleanup_idempotency(self, max_age_secs: int = 300):
        cutoff = time.time() - max_age_secs
        with self.lock, self.conn:
            self.conn.execute("DELETE FROM signal_idempotency WHERE created_at < ?", (cutoff,))

    # ── Circuit Breaker ───────────────────────────────────────────────────────

    def get_circuit_status(self, user_id: str) -> dict:
        self.ensure_user_settings(user_id)
        with self.lock:
            row = self.conn.execute(
                "SELECT circuit_broken, circuit_broken_at FROM user_settings WHERE user_id = ?",
                (user_id,),
            ).fetchone()
        if not row:
            return {"broken": False, "broken_at": None}
        return {"broken": bool(row["circuit_broken"]), "broken_at": row["circuit_broken_at"]}

    def set_circuit_broken(self, user_id: str, broken: bool):
        self.ensure_user_settings(user_id)
        now = time.time() if broken else None
        with self.lock, self.conn:
            self.conn.execute(
                "UPDATE user_settings SET circuit_broken = ?, circuit_broken_at = ?, updated_at = ? WHERE user_id = ?",
                (1 if broken else 0, now, time.time(), user_id),
            )

    def count_consecutive_losses(self, user_id: str, window_secs: int = 3600) -> int:
        cutoff = time.time() - window_secs
        with self.lock:
            rows = self.conn.execute(
                """SELECT status FROM commands WHERE user_id = ? AND created_at > ?
                   AND action IN ('BUY', 'SELL') ORDER BY created_at DESC LIMIT 20""",
                (user_id, cutoff),
            ).fetchall()
        count = 0
        for row in rows:
            if row["status"] == "failed":
                count += 1
            else:
                break  # stop at first non-failure
        return count

    def get_consecutive_loss_limit(self, user_id: str) -> int:
        self.ensure_user_settings(user_id)
        with self.lock:
            row = self.conn.execute(
                "SELECT consecutive_loss_limit FROM user_settings WHERE user_id = ?",
                (user_id,),
            ).fetchone()
        return int(row["consecutive_loss_limit"]) if row and row["consecutive_loss_limit"] else 5

    # ── Subscription Plans ────────────────────────────────────────────────────

    def get_user_plan(self, user_id: str) -> str:
        with self.lock:
            row = self.conn.execute(
                "SELECT plan FROM user_subscriptions WHERE user_id = ?",
                (user_id,),
            ).fetchone()
        return row["plan"] if row else "free"

    def is_plan_active(self, user_id: str) -> bool:
        with self.lock:
            row = self.conn.execute(
                "SELECT expires_at FROM user_subscriptions WHERE user_id = ?",
                (user_id,),
            ).fetchone()
        if not row:
            return True  # no subscription record = free plan = always active
        return row["expires_at"] is None or row["expires_at"] > time.time()


# Global store
store = BridgeStore(DB_PATH)
_start_time = time.time()
_log_startup_summary()
PENDING_DESKTOP_STATES = {}
_pending_state_lock = RLock()

# Persistent per-user MT5 sessions (initialized immediately, warm for every trade)
session_manager = SessionManager()
session_manager.load_from_store(store, decrypt_secret)
LAST_OFFLINE_NOTIFY = {}

# ── Section 9: In-memory rate limiter ─────────────────────────────────────────
_rate_buckets: dict = {}  # key → deque of timestamps
_rate_lock = threading.Lock()


def _rate_check(key: str, max_calls: int, window_secs: int) -> bool:
    """Return True if the call is allowed, False if the rate limit is exceeded."""
    now = time.time()
    cutoff = now - window_secs
    with _rate_lock:
        bucket = _rate_buckets.setdefault(key, _collections.deque())
        # Evict old entries
        while bucket and bucket[0] < cutoff:
            bucket.popleft()
        if len(bucket) >= max_calls:
            return False
        bucket.append(now)
    return True


def _process_signal_for_telegram(user_id: str, signal_data: dict) -> dict:
    """Process a parsed Telegram signal. Called from the bot manager background thread."""
    with app.test_request_context():
        response, status_code = _process_signal_for_user(user_id, signal_data)
        result = response.get_json()
        result["status_code"] = status_code
        return result


def _close_channel_positions(user_id: str, channel_id: str) -> dict:
    """
    Channel-scoped close: close only positions opened by signals from this channel.
    Finds symbols from the signal log and sends CLOSE commands for each.
    """
    symbols = store.get_channel_open_symbols(user_id, channel_id)
    if not symbols:
        return {"closed_count": 0, "detail": "no open positions from this channel"}

    closed_count = 0
    errors = []
    for symbol in symbols:
        try:
            with app.test_request_context():
                managed_mode = store.is_managed_enabled(user_id)
                target_relay = f"{MANAGED_RELAY_PREFIX}{user_id}" if managed_mode else None

                if not managed_mode:
                    relays = store.list_relays(user_id)
                    if relays:
                        target_relay = next(iter(relays.keys()))

                if not target_relay:
                    errors.append(f"no relay for {symbol}")
                    continue

                cmd = Command(user_id, target_relay, "CLOSE", symbol, 0.0,
                              script_name="Telegram-close")
                store.enqueue(cmd)

                if managed_mode:
                    cmd_dict = {"action": "CLOSE", "symbol": symbol, "size": 0.0}
                    result = session_manager.execute(user_id, cmd_dict)
                    status = (CommandStatus.EXECUTED if result.get("status") == "executed"
                              else CommandStatus.FAILED)
                    store.update_result(user_id, target_relay, cmd.id, status, result)
                    if status == CommandStatus.EXECUTED:
                        closed_count += 1
                    else:
                        errors.append(f"{symbol}: {result.get('error', 'failed')}")
                else:
                    closed_count += 1  # queued for relay
        except Exception as exc:
            errors.append(f"{symbol}: {exc}")

    notify_user(user_id, f"⚠️ Channel close: {closed_count} symbol(s) closed/queued")
    return {"closed_count": closed_count, "symbols": symbols, "errors": errors}


# Telegram signal bot — shared bot for all users
_tg_bot_token = os.getenv("TELEGRAM_SIGNAL_BOT_TOKEN", "").strip()
_openai_api_key = os.getenv("OPENAI_API_KEY", "").strip()

# Initialize LLM fallback if OpenAI key is configured
_llm_processor = None
if _openai_api_key:
    from telegram_llm_fallback import LLMFallback, LLMFallbackProcessor  # noqa: E402
    _llm = LLMFallback(api_key=_openai_api_key)
    _llm_processor = LLMFallbackProcessor(
        llm=_llm,
        execute_callback=_process_signal_for_telegram,
        confidence_threshold=0.5,
    )
    logger.info("LLM fallback configured (GPT-4o-mini)")

_admin_telegram_id = os.getenv("ADMIN_TELEGRAM_ID", "").strip()

from telegram_bot_manager import TelegramBotManager  # noqa: E402
telegram_manager = TelegramBotManager(
    store, app, _process_signal_for_telegram, _tg_bot_token,
    close_callback=_close_channel_positions,
    llm_processor=_llm_processor,
    admin_telegram_id=_admin_telegram_id or None,
)
telegram_manager.start()


def _send_notifications(user_id: str, message: str):
    """Send Telegram/Discord notifications synchronously — called from background worker."""
    settings = store.get_user_settings(user_id)
    if not settings.get("notifications_enabled"):
        return

    telegram_token = (settings.get("telegram_bot_token") or "").strip()
    telegram_chat_id = (settings.get("telegram_chat_id") or "").strip()
    discord_webhook_url = (settings.get("discord_webhook_url") or "").strip()

    if telegram_token and telegram_chat_id:
        try:
            requests.post(
                f"https://api.telegram.org/bot{telegram_token}/sendMessage",
                json={"chat_id": telegram_chat_id, "text": message},
                timeout=6,
            )
        except Exception as exc:
            logger.warning(f"Telegram notify failed for {user_id}: {exc}")

    if discord_webhook_url:
        try:
            requests.post(discord_webhook_url, json={"content": message}, timeout=6)
        except Exception as exc:
            logger.warning(f"Discord notify failed for {user_id}: {exc}")


# Async notification queue — decouples Telegram/Discord HTTP calls from signal processing
_notify_queue: "_queue.Queue[tuple]" = _queue.Queue(maxsize=500)

def _notification_worker():
    while True:
        try:
            user_id, message = _notify_queue.get(timeout=5)
        except _queue.Empty:
            continue
        try:
            _send_notifications(user_id, message)
        except Exception as exc:
            logger.warning(f"Notification worker error: {exc}")
        _notify_queue.task_done()

threading.Thread(target=_notification_worker, daemon=True, name="notify-worker").start()


def _idempotency_cleanup_worker():
    while True:
        time.sleep(300)  # 5 minutes
        try:
            store.cleanup_idempotency(max_age_secs=300)
        except Exception as exc:
            logger.warning(f"Idempotency cleanup error: {exc}")

threading.Thread(target=_idempotency_cleanup_worker, daemon=True, name="idem-cleanup").start()


_session_last_state: dict = {}      # user_id → bool (was connected)
_session_offline_notified: dict = {}  # user_id → timestamp of last offline notify

def _managed_heartbeat_worker():
    """
    Send periodic heartbeats for managed VPS sessions so they appear online
    in the dashboard and the MT5/Broker indicators go green in the app.

    Section 7: Detect offline/recovery transitions and send Telegram notifications
    to the user's private chat, throttled to once per hour for offline events.
    """
    while True:
        time.sleep(15)
        try:
            for user_id, session in list(session_manager._sessions.items()):
                connected = session.connected
                relay_id = f"{MANAGED_RELAY_PREFIX}{user_id}"
                metadata = {
                    "mt5_connected": connected,
                    "broker_connected": connected,
                    "managed": True,
                }
                store.heartbeat(user_id, relay_id, metadata)

                # Section 7: Session state change notifications
                prev = _session_last_state.get(user_id)
                if prev is not None and prev != connected:
                    now = time.time()
                    if connected:
                        # Recovery notification (always send)
                        msg = "🟢 MT5 session reconnected and ready to trade."
                        try:
                            telegram_manager.send_session_notification(user_id, msg)
                        except Exception:
                            pass
                        _session_offline_notified.pop(user_id, None)
                    else:
                        # Offline — throttle to once per hour
                        last_notified = _session_offline_notified.get(user_id, 0)
                        if now - last_notified >= 3600:
                            msg = "⚠️ MT5 session went offline. Attempting to reconnect…"
                            try:
                                telegram_manager.send_session_notification(user_id, msg)
                            except Exception:
                                pass
                            _session_offline_notified[user_id] = now
                _session_last_state[user_id] = connected
        except Exception as exc:
            logger.warning(f"Managed heartbeat worker error: {exc}")

threading.Thread(target=_managed_heartbeat_worker, daemon=True, name="managed-heartbeat").start()


# ── FIX 5: Relay Heartbeat Monitor ────────────────────────────────────────────

def _relay_heartbeat_monitor():
    """
    FIX 5: Periodically mark relays offline when their heartbeat exceeds the timeout.
    Runs every 60 seconds and logs a WARNING for any relay that just went offline.
    """
    _relay_last_state: dict = {}  # (user_id, relay_id) → was_online

    while True:
        time.sleep(60)
        try:
            cutoff = time.time() - DEFAULT_HEARTBEAT_TIMEOUT
            with store.lock:
                rows = store.conn.execute(
                    """
                    SELECT user_id, relay_id, state, last_heartbeat
                    FROM relays
                    WHERE last_heartbeat < ?
                      AND state != ?
                    """,
                    (cutoff, RelayState.OFFLINE.value),
                ).fetchall()

            for row in rows:
                uid = row["user_id"]
                rid = row["relay_id"]
                key = (uid, rid)
                was_online = _relay_last_state.get(key, True)
                if was_online:
                    logger.warning(
                        f"[HeartbeatMonitor] Relay {rid} for user {uid} went offline "
                        f"(last heartbeat {time.time() - row['last_heartbeat']:.0f}s ago)"
                    )
                _relay_last_state[key] = False

                with store.lock, store.conn:
                    store.conn.execute(
                        "UPDATE relays SET state = ? WHERE user_id = ? AND relay_id = ?",
                        (RelayState.OFFLINE.value, uid, rid),
                    )

            # Update tracked state for relays that ARE online
            with store.lock:
                online_rows = store.conn.execute(
                    "SELECT user_id, relay_id FROM relays WHERE state = ?",
                    (RelayState.ONLINE.value,),
                ).fetchall()
            for row in online_rows:
                _relay_last_state[(row["user_id"], row["relay_id"])] = True

        except Exception as exc:
            logger.warning(f"Relay heartbeat monitor error: {exc}")


threading.Thread(target=_relay_heartbeat_monitor, daemon=True, name="relay-heartbeat-monitor").start()


# ── FIX 3: OAuth Expired-State Cleanup ────────────────────────────────────────

def _cleanup_expired_states():
    """
    FIX 3: Periodically clean up expired OAuth desktop tokens and in-memory states.
    Runs every 5 minutes.
    """
    while True:
        time.sleep(300)  # 5 minutes
        try:
            now = time.time()
            # Clean DB tokens
            with store.lock, store.conn:
                store.conn.execute(
                    "DELETE FROM oauth_desktop_tokens WHERE expires_at < ?", (now,)
                )
            # Clean in-memory pending states
            with _pending_state_lock:
                expired_keys = [
                    k for k, v in PENDING_DESKTOP_STATES.items()
                    if (v["expires_at"] if isinstance(v, dict) else v) < now
                ]
                for k in expired_keys:
                    del PENDING_DESKTOP_STATES[k]
            if expired_keys:
                logger.info(f"Cleaned up {len(expired_keys)} expired OAuth desktop state(s)")
        except Exception as exc:
            logger.warning(f"OAuth state cleanup error: {exc}")


threading.Thread(target=_cleanup_expired_states, daemon=True, name="oauth-state-cleanup").start()


# ── FIX 14: Automated DB Backup ───────────────────────────────────────────────

def _db_backup_thread():
    """
    FIX 14: Daily hot backup of bridge.db using SQLite's backup() API.
    Keeps the last 7 backups.
    """
    while True:
        time.sleep(86400)  # 24 hours
        try:
            db_dir = os.path.dirname(os.path.abspath(DB_PATH))
            backup_name = f"bridge.db.{datetime.now(timezone.utc).strftime('%Y%m%d')}.bak"
            backup_path = os.path.join(db_dir, backup_name)
            import sqlite3 as _sqlite3
            with store.lock:
                dest = _sqlite3.connect(backup_path)
                try:
                    store.conn.backup(dest)
                finally:
                    dest.close()
            logger.info(f"[DBBackup] Hot backup written to {backup_path}")

            # Keep only the last 7 backup files
            bak_files = sorted(
                [f for f in os.listdir(db_dir) if f.startswith("bridge.db.") and f.endswith(".bak")],
                reverse=True,
            )
            for old_bak in bak_files[7:]:
                try:
                    os.remove(os.path.join(db_dir, old_bak))
                    logger.info(f"[DBBackup] Removed old backup: {old_bak}")
                except Exception as del_exc:
                    logger.warning(f"[DBBackup] Could not remove {old_bak}: {del_exc}")
        except Exception as exc:
            logger.error(f"[DBBackup] Backup failed: {exc}")


threading.Thread(target=_db_backup_thread, daemon=True, name="db-backup").start()


# ── FIX 1: Global shutdown event ──────────────────────────────────────────────

_shutdown_event = threading.Event()


def _graceful_shutdown(signum, frame):
    """
    FIX 1: SIGTERM handler — cleanly shut down all background resources.
    Gives threads 10 seconds to finish, then exits.
    """
    logger.info(f"[Shutdown] Received signal {signum} — initiating graceful shutdown")
    _shutdown_event.set()

    # Stop the Telegram bot
    try:
        telegram_manager.stop()
        logger.info("[Shutdown] Telegram bot stopped")
    except Exception as exc:
        logger.warning(f"[Shutdown] Error stopping Telegram manager: {exc}")

    # Shut down all MT5 sessions
    try:
        session_manager.shutdown_all(timeout=10)
        logger.info("[Shutdown] All MT5 sessions stopped")
    except Exception as exc:
        logger.warning(f"[Shutdown] Error stopping MT5 sessions: {exc}")

    # Close the DB connection cleanly
    try:
        with store.lock:
            store.conn.close()
        logger.info("[Shutdown] Database connection closed")
    except Exception as exc:
        logger.warning(f"[Shutdown] Error closing DB: {exc}")

    logger.info("[Shutdown] Graceful shutdown complete — exiting")
    sys.exit(0)


# Register SIGTERM handler at module level (for service mode)
try:
    _signal.signal(_signal.SIGTERM, _graceful_shutdown)
except (OSError, ValueError):
    # SIGTERM can't be set in some contexts (e.g. Windows non-main thread) — ignore
    pass


def notify_user(user_id: str, message: str):
    """Enqueue a notification — returns immediately, delivery is async."""
    try:
        _notify_queue.put_nowait((user_id, message))
    except _queue.Full:
        logger.warning(f"Notification queue full; dropping message for {user_id}")

# ==================== Auth Helpers ====================

def verify_api_key(user_id: str, api_key: str) -> bool:
    return store.verify_api_key(user_id, api_key)

def verify_relay_token(user_id: str, relay_id: str, token: str) -> bool:
    return store.verify_relay_token(user_id, relay_id, token)

def extract_user_id(request_obj) -> str:
    """Extract user_id from header."""
    return (request_obj.headers.get("X-User-ID") or "").strip()


def require_user_id():
    user_id = extract_user_id(request)
    if not user_id:
        return None, (jsonify({"error": "missing X-User-ID header"}), 400)
    return user_id, None


def require_user_auth(user_id: str):
    api_key = (request.headers.get("X-API-Key") or "").strip()
    relay_token = (request.headers.get("X-Relay-Token") or "").strip()
    relay_id = (request.headers.get("X-Relay-ID") or "").strip()

    if api_key:
        if not verify_api_key(user_id, api_key):
            return jsonify({"error": "unauthorized"}), 401
        return None

    # Accept relay token as fallback auth (desktop app uses token from relay/login)
    if relay_token and relay_id:
        if store.verify_relay_token(user_id, relay_id, relay_token):
            return None
        return jsonify({"error": "unauthorized"}), 401

    if REQUIRE_API_KEY:
        return jsonify({"error": "missing X-API-Key header"}), 401
    return None


def login_required(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        if "dashboard_user" not in session:
            return redirect(url_for("login_page"))
        return func(*args, **kwargs)
    return wrapper


def resolve_user_from_request() -> tuple:
    """
    Resolve authenticated user from either session cookie or X-User-ID/X-API-Key headers.
    Returns (user_id, error_response) — error_response is None on success.
    Accepts: session["dashboard_user"] OR X-User-ID + X-API-Key headers.
    """
    # Try session first (browser dashboard)
    if "dashboard_user" in session:
        return session["dashboard_user"], None

    # Try header auth (API / desktop app)
    user_id = (request.headers.get("X-User-ID") or "").strip()
    if not user_id:
        return None, (jsonify({"error": "missing X-User-ID header or session"}), 401)

    if not store.user_exists(user_id):
        return None, (jsonify({"error": "unauthorized"}), 401)

    err = require_user_auth(user_id)
    if err is not None:
        return None, err

    return user_id, None


def admin_login_required(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        if "admin_user" not in session:
            return redirect(url_for("admin_login_page"))
        return func(*args, **kwargs)
    return wrapper

# ==================== Request Logging (Section 9) ====================

@app.before_request
def _log_request():
    """Log incoming API requests (excluding health checks and static assets)."""
    if request.path in ("/health",) or request.path.startswith("/static"):
        return
    logger.debug(
        f"[REQ] {request.method} {request.path} "
        f"user={request.headers.get('X-User-ID', '-')} "
        f"ip={request.headers.get('X-Forwarded-For', request.remote_addr or '-').split(',')[0].strip()}"
    )


@app.after_request
def _add_security_headers(response):
    """Add security headers to all responses. FIX Phase 5."""
    # Content Security Policy — tight for API endpoints, permissive for dashboard pages
    if request.path.startswith("/admin") or request.path.startswith("/dashboard") or \
            request.path in ("/login", "/register", "/terms", "/privacy"):
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:;"
        )
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    # Remove any accidental server fingerprinting
    response.headers.remove("Server")
    return response


# ==================== Endpoints ====================

@app.route("/health", methods=["GET"])
def health():
    """Structured health check with component status."""
    uptime = int(time.time() - _start_time)

    # DB check
    db_ok = False
    try:
        with store.lock:
            store.conn.execute("SELECT 1").fetchone()
        db_ok = True
    except Exception:
        pass

    # Managed sessions
    managed_sessions = len(session_manager._sessions)
    connected_sessions = sum(1 for s in session_manager._sessions.values() if s.connected)

    # Telegram check
    telegram_ok = False
    try:
        from telegram_notifier import TelegramNotifier as _TN
        telegram_ok = True
    except Exception:
        pass

    # LLM check
    llm_configured = False
    try:
        if _llm_processor and _llm_processor._llm and _llm_processor._llm.is_configured:
            llm_configured = True
    except Exception:
        pass

    # Active dashboard sessions (approximate via store)
    try:
        with store.lock:
            active_sessions = store.conn.execute(
                "SELECT COUNT(*) FROM users"
            ).fetchone()[0]
    except Exception:
        active_sessions = 0

    overall_status = "online" if db_ok else "degraded"

    return jsonify({
        "status": overall_status,
        "bridge": "cloud-bridge",
        "version": APP_VERSION,
        "uptime_secs": uptime,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "components": {
            "db": {"status": "ok" if db_ok else "error"},
            "managed_sessions": {"total": managed_sessions, "connected": connected_sessions},
            "telegram": {"configured": telegram_ok},
            "llm": {"configured": llm_configured},
        },
        "sessions": {"registered_users": active_sessions},
        "require_api_key": REQUIRE_API_KEY,
    })


@app.route("/", methods=["GET"])
def home_page():
    if "dashboard_user" in session:
        return redirect(url_for("dashboard_page"))
    return redirect(url_for("login_page"))


@app.route("/register", methods=["GET", "POST"])
@_rate_limit("10 per minute")
def register_page():
    available_scripts = store.list_scripts()

    if request.method == "GET":
        return render_template("register.html", available_scripts=available_scripts)

    user_id = (request.form.get("user_id") or "").strip()
    password = request.form.get("password") or ""
    password_confirm = request.form.get("password_confirm") or ""

    invite_code = (request.form.get("invite_code") or "").strip()
    if not invite_code:
        flash("An invite code is required to register.", "error")
        return render_template("register.html", available_scripts=available_scripts), 400
    valid, reason = store.validate_invite_code(invite_code)
    if not valid:
        flash(f"Invalid or already used invite code: {reason}", "error")
        return render_template("register.html", available_scripts=available_scripts), 400

    if not user_id or len(user_id) < 3:
        flash("Username must be at least 3 characters.", "error")
        return render_template("register.html", available_scripts=available_scripts), 400
    if not password or len(password) < 8:
        flash("Password must be at least 8 characters.", "error")
        return render_template("register.html", available_scripts=available_scripts), 400
    if password != password_confirm:
        flash("Passwords do not match.", "error")
        return render_template("register.html", available_scripts=available_scripts), 400

    try:
        api_key = store.register_dashboard_user(user_id, password)
    except ValueError:
        flash("User already exists.", "error")
        return render_template("register.html", available_scripts=available_scripts), 400

    store.consume_invite_code(invite_code, user_id)
    store.assign_script_to_user(user_id, "default-script")

    session["dashboard_user"] = user_id
    session.permanent = True
    session["dashboard_api_key"] = api_key
    flash("Registration successful.", "success")
    return redirect(url_for("dashboard_page"))


@app.route("/login", methods=["GET", "POST"])
def login_page():
    if request.method == "GET":
        return render_template("login.html")

    # Section 9: rate limit login attempts — 5/min per IP
    client_ip = request.headers.get("X-Forwarded-For", request.remote_addr or "").split(",")[0].strip()
    if not _rate_check(f"login:{client_ip}", max_calls=5, window_secs=60):
        flash("Too many login attempts. Please wait a moment.", "error")
        from flask import make_response as _mkr2
        _login_resp = _mkr2(render_template("login.html"), 429)
        _login_resp.headers["Retry-After"] = "60"
        return _login_resp

    user_id = (request.form.get("user_id") or "").strip()
    password = request.form.get("password") or ""

    if not store.verify_dashboard_login(user_id, password):
        flash("Invalid username or password.", "error")
        return render_template("login.html"), 401

    session["dashboard_user"] = user_id
    session.permanent = True  # Section 9: respect session expiry
    flash("Signed in.", "success")
    return redirect(url_for("dashboard_page"))


@app.route("/logout", methods=["POST"])
def logout_page():
    session.clear()
    flash("Signed out.", "success")
    return redirect(url_for("login_page"))


# ==================== OAuth Routes ====================

@app.route("/auth/google")
@_rate_limit("20 per minute")
def google_login():
    if not google_oauth:
        flash("Google login is not configured.", "error")
        return redirect(url_for("login_page"))
    redirect_uri = url_for("google_callback", _external=True)
    desktop_state = request.args.get("desktop_state", "").strip()
    # Carry invite code through web OAuth flow via session
    invite_code = request.args.get("invite_code", "").strip()
    if invite_code:
        session["pending_invite_code"] = invite_code
    return google_oauth.authorize_redirect(redirect_uri, state=desktop_state or None)


@app.route("/auth/google/callback")
def google_callback():
    if not google_oauth:
        flash("Google login is not configured.", "error")
        return redirect(url_for("login_page"))
    try:
        token = google_oauth.authorize_access_token()
    except Exception as e:
        logger.warning(f"Google OAuth error: {e}")
        flash("Google login failed. Please try again.", "error")
        return redirect(url_for("login_page"))

    userinfo = token.get("userinfo") or {}
    provider_id = str(userinfo.get("sub", ""))
    email = userinfo.get("email", "")
    state = (request.args.get("state") or "").strip()
    is_desktop = state.startswith("desktop:")

    if not provider_id:
        flash("Could not retrieve Google account info.", "error")
        return redirect(url_for("login_page"))

    user_id = store.get_user_by_oauth("google", provider_id)
    api_key = None
    if not user_id:
        # New registration — require a valid invite code
        if is_desktop:
            with _pending_state_lock:
                pending_entry = PENDING_DESKTOP_STATES.get(state, {})
            invite_code = pending_entry.get("invite_code", "") if isinstance(pending_entry, dict) else ""
        else:
            invite_code = session.pop("pending_invite_code", "")
        if not invite_code:
            flash("An invite code is required to create a new account. Please register at app.platalgo.com/register.", "error")
            return redirect(url_for("register_page"))
        valid, reason = store.validate_invite_code(invite_code)
        if not valid:
            flash(f"Invalid or already used invite code: {reason}", "error")
            return redirect(url_for("register_page"))
        user_id, api_key = store.register_oauth_user("google", provider_id, email)
        store.consume_invite_code(invite_code, user_id)
    else:
        api_key = store.regenerate_api_key(user_id)

    if is_desktop:
        store.upsert_desktop_token(state, user_id, api_key, ttl=DESKTOP_OAUTH_STATE_TTL)
        with _pending_state_lock:
            PENDING_DESKTOP_STATES.pop(state, None)
        return render_template("oauth_success.html",
                               provider_name="Google", user_id=user_id)

    session["dashboard_user"] = user_id
    session.permanent = True
    session["dashboard_api_key"] = api_key
    flash("Signed in with Google.", "success")
    return redirect(url_for("dashboard_page"))


@app.route("/auth/facebook")
@_rate_limit("20 per minute")
def facebook_login():
    if not facebook_oauth:
        flash("Facebook login is not configured.", "error")
        return redirect(url_for("login_page"))
    redirect_uri = url_for("facebook_callback", _external=True)
    desktop_state = request.args.get("desktop_state", "").strip()
    invite_code = request.args.get("invite_code", "").strip()
    if invite_code:
        session["pending_invite_code"] = invite_code
    return facebook_oauth.authorize_redirect(redirect_uri, state=desktop_state or None)


@app.route("/auth/facebook/callback")
def facebook_callback():
    if not facebook_oauth:
        flash("Facebook login is not configured.", "error")
        return redirect(url_for("login_page"))
    try:
        token = facebook_oauth.authorize_access_token()
    except Exception as e:
        logger.warning(f"Facebook OAuth error: {e}")
        flash("Facebook login failed. Please try again.", "error")
        return redirect(url_for("login_page"))

    try:
        resp = facebook_oauth.get("me?fields=id,name", token=token)
        userinfo = resp.json()
    except Exception as e:
        logger.warning(f"Facebook userinfo error: {e}")
        flash("Could not retrieve Facebook account info.", "error")
        return redirect(url_for("login_page"))

    provider_id = str(userinfo.get("id", ""))
    name = userinfo.get("name", "")
    # Use first name as basis for username since email scope is not requested
    email = (name.split()[0] + "@facebook") if name else ""
    state = (request.args.get("state") or "").strip()
    is_desktop = state.startswith("desktop:")

    if not provider_id:
        flash("Could not retrieve Facebook account info.", "error")
        return redirect(url_for("login_page"))

    user_id = store.get_user_by_oauth("facebook", provider_id)
    api_key = None
    if not user_id:
        # New registration — require a valid invite code
        if is_desktop:
            with _pending_state_lock:
                pending_entry = PENDING_DESKTOP_STATES.get(state, {})
            invite_code = pending_entry.get("invite_code", "") if isinstance(pending_entry, dict) else ""
        else:
            invite_code = session.pop("pending_invite_code", "")
        if not invite_code:
            flash("An invite code is required to create a new account. Please register at app.platalgo.com/register.", "error")
            return redirect(url_for("register_page"))
        valid, reason = store.validate_invite_code(invite_code)
        if not valid:
            flash(f"Invalid or already used invite code: {reason}", "error")
            return redirect(url_for("register_page"))
        user_id, api_key = store.register_oauth_user("facebook", provider_id, email)
        store.consume_invite_code(invite_code, user_id)
    else:
        api_key = store.regenerate_api_key(user_id)

    if is_desktop:
        store.upsert_desktop_token(state, user_id, api_key, ttl=DESKTOP_OAUTH_STATE_TTL)
        with _pending_state_lock:
            PENDING_DESKTOP_STATES.pop(state, None)
        return render_template("oauth_success.html",
                               provider_name="Facebook", user_id=user_id)

    session["dashboard_user"] = user_id
    session.permanent = True
    session["dashboard_api_key"] = api_key
    flash("Signed in with Facebook.", "success")
    return redirect(url_for("dashboard_page"))


@app.route("/auth/desktop/start", methods=["POST"])
def auth_desktop_start():
    data = request.get_json(silent=True) or {}
    provider = (data.get("provider") or "").strip().lower()
    if provider not in ("google", "facebook"):
        return jsonify({"error": "unsupported provider"}), 400
    if provider == "google" and not google_oauth:
        return jsonify({"error": "google oauth not configured"}), 503
    if provider == "facebook" and not facebook_oauth:
        return jsonify({"error": "facebook oauth not configured"}), 503
    invite_code = (data.get("invite_code") or "").strip()
    state = f"desktop:{uuid.uuid4().hex}"
    login_route = "google_login" if provider == "google" else "facebook_login"
    auth_url = url_for(login_route, _external=True, desktop_state=state)
    expires_at = time.time() + DESKTOP_OAUTH_STATE_TTL
    with _pending_state_lock:
        # Store expires_at and invite_code together so the callback can validate new registrations
        PENDING_DESKTOP_STATES[state] = {"expires_at": expires_at, "invite_code": invite_code}
    return jsonify({"auth_url": auth_url, "state": state, "expires_in": DESKTOP_OAUTH_STATE_TTL})


@app.route("/auth/desktop/consume/<state>", methods=["GET"])
def auth_desktop_consume(state: str):
    state = (state or "").strip()
    token, expired = store.consume_desktop_token(state)
    if token:
        with _pending_state_lock:
            PENDING_DESKTOP_STATES.pop(state, None)
        return jsonify(token)
    if expired:
        with _pending_state_lock:
            PENDING_DESKTOP_STATES.pop(state, None)
        return jsonify({"error": "expired"}), 410

    pending_entry = None
    with _pending_state_lock:
        pending_entry = PENDING_DESKTOP_STATES.get(state)
        if pending_entry:
            expires_at = pending_entry["expires_at"] if isinstance(pending_entry, dict) else pending_entry
            if time.time() > expires_at:
                PENDING_DESKTOP_STATES.pop(state, None)
                pending_entry = None
                expired = True

    if expired:
        return jsonify({"error": "expired"}), 410
    if pending_entry:
        return jsonify({"status": "pending"}), 202
    return jsonify({"error": "not found"}), 404


@app.route("/dashboard", methods=["GET"])
@login_required
def dashboard_page():
    user_id = session["dashboard_user"]
    dashboard = store.get_dashboard_data(user_id)
    settings = store.get_user_settings(user_id)
    dashboard_api_key = session.get("dashboard_api_key")
    webhook_token = store.get_or_create_webhook_token(user_id)
    webhook_url = f"{get_public_base_url()}/signal/{webhook_token}"
    scripts = store.get_user_scripts(user_id)
    circuit_status = store.get_circuit_status(user_id)
    circuit_broken = circuit_status.get("broken", False)
    return render_template(
        "dashboard.html",
        dashboard=dashboard,
        settings=settings,
        dashboard_api_key=dashboard_api_key,
        webhook_url=webhook_url,
        scripts=scripts,
        circuit_broken=circuit_broken,
    )


@app.route("/dashboard/regenerate-api-key", methods=["POST"])
@login_required
def regenerate_api_key_route():
    user_id = session["dashboard_user"]
    new_api_key = store.regenerate_api_key(user_id)
    session["dashboard_api_key"] = new_api_key
    flash("New API key generated. Store it now - it won't be shown again.", "success")
    return redirect(url_for("dashboard_page"))


@app.route("/dashboard/rotate-webhook", methods=["POST"])
@login_required
def rotate_webhook_route():
    user_id = session["dashboard_user"]
    import secrets as _sec
    new_token = _sec.token_urlsafe(24)
    with store.lock, store.conn:
        store.conn.execute(
            "UPDATE users SET webhook_token = ? WHERE user_id = ?",
            (new_token, user_id),
        )
    flash("Webhook URL rotated. Update your TradingView alerts.", "success")
    return redirect(url_for("dashboard_page"))


@app.route("/api/rotate-webhook", methods=["POST"])
def api_rotate_webhook():
    """API-key authenticated webhook rotation (used by Electron app)."""
    user_id, err = require_user_id()
    if err:
        return err
    auth_err = require_user_auth(user_id)
    if auth_err:
        return auth_err
    import secrets as _sec
    new_token = _sec.token_urlsafe(24)
    with store.lock, store.conn:
        store.conn.execute(
            "UPDATE users SET webhook_token = ? WHERE user_id = ?",
            (new_token, user_id),
        )
    root_url = get_public_base_url()
    return jsonify({"status": "rotated", "webhook_url": f"{root_url}/signal/{new_token}"})


@app.route("/dashboard/analytics", methods=["GET"])
def dashboard_analytics():
    """Signal performance analytics endpoint."""
    user_id, err = require_user_id()
    if err:
        return err
    auth_err = require_user_auth(user_id)
    if auth_err:
        return auth_err

    now = time.time()
    today_cutoff = now - 86400
    week_cutoff = now - 7 * 86400

    with store.lock:
        total_signals = store.conn.execute(
            "SELECT COUNT(*) FROM commands WHERE user_id = ?",
            (user_id,),
        ).fetchone()[0]

        executed = store.conn.execute(
            "SELECT COUNT(*) FROM commands WHERE user_id = ? AND status = 'executed'",
            (user_id,),
        ).fetchone()[0]

        failed = store.conn.execute(
            "SELECT COUNT(*) FROM commands WHERE user_id = ? AND status = 'failed'",
            (user_id,),
        ).fetchone()[0]

        signals_today = store.conn.execute(
            "SELECT COUNT(*) FROM commands WHERE user_id = ? AND created_at > ?",
            (user_id, today_cutoff),
        ).fetchone()[0]

        signals_this_week = store.conn.execute(
            "SELECT COUNT(*) FROM commands WHERE user_id = ? AND created_at > ?",
            (user_id, week_cutoff),
        ).fetchone()[0]

        by_symbol_rows = store.conn.execute(
            """
            SELECT symbol,
                   COUNT(*) AS total,
                   SUM(CASE WHEN status = 'executed' THEN 1 ELSE 0 END) AS executed,
                   SUM(CASE WHEN status = 'failed'   THEN 1 ELSE 0 END) AS failed
            FROM commands
            WHERE user_id = ?
            GROUP BY symbol
            ORDER BY total DESC
            LIMIT 20
            """,
            (user_id,),
        ).fetchall()

        by_script_rows = store.conn.execute(
            """
            SELECT script_name,
                   COUNT(*) AS total,
                   SUM(CASE WHEN status = 'executed' THEN 1 ELSE 0 END) AS executed,
                   SUM(CASE WHEN status = 'failed'   THEN 1 ELSE 0 END) AS failed
            FROM commands
            WHERE user_id = ?
            GROUP BY script_name
            ORDER BY total DESC
            LIMIT 20
            """,
            (user_id,),
        ).fetchall()

        recent_24h_rows = store.conn.execute(
            """
            SELECT action, symbol, size, status, created_at, script_name
            FROM commands
            WHERE user_id = ? AND created_at > ?
            ORDER BY created_at DESC
            LIMIT 50
            """,
            (user_id, today_cutoff),
        ).fetchall()

    win_rate = round(executed / total_signals * 100, 1) if total_signals else 0.0

    return jsonify({
        "win_rate": win_rate,
        "total_signals": total_signals,
        "executed": executed,
        "failed": failed,
        "signals_today": signals_today,
        "signals_this_week": signals_this_week,
        "by_symbol": [dict(r) for r in by_symbol_rows],
        "by_script": [dict(r) for r in by_script_rows],
        "recent_24h": [dict(r) for r in recent_24h_rows],
    })


@app.route("/admin/login", methods=["GET", "POST"])
def admin_login_page():
    if request.method == "GET":
        return render_template("admin_login.html")

    if not ADMIN_PASSWORD_HASH and not ADMIN_PASSWORD:
        flash("Admin credentials are not configured on server.", "error")
        return render_template("admin_login.html"), 503

    username = (request.form.get("username") or "").strip()
    password = request.form.get("password") or ""
    if not verify_admin_credentials(username, password):
        flash("Invalid admin credentials.", "error")
        return render_template("admin_login.html"), 401

    session["admin_user"] = username
    flash("Admin signed in.", "success")
    return redirect(url_for("admin_scripts_page"))


@app.route("/admin/logout", methods=["POST"])
def admin_logout_page():
    session.pop("admin_user", None)
    flash("Admin signed out.", "success")
    return redirect(url_for("admin_login_page"))


@app.route("/admin/scripts", methods=["GET", "POST"])
@admin_login_required
def admin_scripts_page():
    if request.method == "POST":
        user_id = (request.form.get("user_id") or "").strip()
        script_codes = request.form.getlist("script_codes")

        if not user_id:
            flash("User is required.", "error")
            return redirect(url_for("admin_scripts_page"))
        if not script_codes:
            flash("Select at least one script.", "error")
            return redirect(url_for("admin_scripts_page"))
        if not store.user_exists(user_id):
            flash("Selected user does not exist.", "error")
            return redirect(url_for("admin_scripts_page"))

        assigned = []
        for script_code in script_codes:
            if store.script_exists(script_code):
                store.assign_script_to_user(user_id, script_code)
                assigned.append(script_code)
        
        if assigned:
            flash(f"Assigned {len(assigned)} script(s) to '{user_id}': {', '.join(assigned)}", "success")
        else:
            flash("No valid scripts selected.", "error")
        return redirect(url_for("admin_scripts_page"))

    users = store.list_users()
    scripts = store.list_scripts()
    assignments = store.get_all_user_script_assignments()
    return render_template(
        "admin_scripts.html",
        users=users,
        scripts=scripts,
        assignments=assignments,
        admin_user=session.get("admin_user"),
    )


@app.route("/admin/catalog", methods=["GET", "POST"])
@admin_login_required
def admin_catalog_page():
    if request.method == "POST":
        action = (request.form.get("action") or "save").strip().lower()
        target_code = (request.form.get("target_script_code") or "").strip()

        if action in ("deactivate", "activate"):
            if not target_code:
                flash("Script code is required for this action.", "error")
                return redirect(url_for("admin_catalog_page"))

            if action == "deactivate":
                changed = store.set_script_active(target_code, False)
                if changed:
                    flash(f"Script deactivated: {target_code}", "success")
                else:
                    flash("Script not found.", "error")
            else:
                changed = store.set_script_active(target_code, True)
                if changed:
                    flash(f"Script activated: {target_code}", "success")
                else:
                    flash("Script not found.", "error")
            return redirect(url_for("admin_catalog_page"))

        script_code = (request.form.get("script_code") or "").strip()
        script_name = (request.form.get("script_name") or "").strip()

        if not script_code or not script_name:
            flash("Script code and script name are required.", "error")
            return redirect(url_for("admin_catalog_page"))

        store.upsert_script(script_code, script_name)
        flash(f"Script saved: {script_name} ({script_code}).", "success")
        return redirect(url_for("admin_catalog_page"))

    scripts = store.list_all_scripts()
    return render_template(
        "admin_catalog.html",
        scripts=scripts,
        admin_user=session.get("admin_user"),
    )


@app.route("/admin/invite-codes", methods=["GET", "POST"])
@admin_login_required
def admin_invite_codes_page():
    if request.method == "POST":
        action = (request.form.get("action") or "create").strip().lower()
        code = (request.form.get("code") or "").strip()
        if action == "create":
            expires_hours_str = (request.form.get("expires_hours") or "").strip()
            expires_hours = int(expires_hours_str) if expires_hours_str.isdigit() else None
            new_code = store.create_invite_code(expires_hours=expires_hours)
            flash(f"Invite code created: {new_code}", "success")
        elif action == "delete" and code:
            with store.lock:
                store.conn.execute("DELETE FROM invite_codes WHERE code = ?", (code,))
                store.conn.commit()
            flash(f"Invite code deleted: {code}", "success")
        return redirect(url_for("admin_invite_codes_page"))

    codes = store.list_invite_codes()
    return render_template(
        "admin_invite_codes.html",
        codes=codes,
        admin_user=session.get("admin_user"),
        now_ts=time.time(),
    )


@app.route("/admin/invite-codes/<code>", methods=["DELETE"])
@admin_login_required
def admin_invite_code_delete(code):
    with store.lock:
        store.conn.execute("DELETE FROM invite_codes WHERE code = ?", (code,))
        store.conn.commit()
    return jsonify({"status": "deleted", "code": code})


@app.route("/admin/api/invite-codes", methods=["GET", "POST"])
@admin_login_required
def admin_api_invite_codes():
    if request.method == "POST":
        data = request.get_json(silent=True) or {}
        expires_hours = data.get("expires_hours")
        if expires_hours is not None:
            expires_hours = int(expires_hours)
        code = store.create_invite_code(expires_hours=expires_hours)
        return jsonify({"status": "created", "code": code})
    codes = store.list_invite_codes()
    return jsonify({"invite_codes": codes})


@app.route("/admin", methods=["GET"])
@admin_login_required
def admin_overview_page():
    """Admin dashboard overview."""
    try:
        with store.lock:
            user_count = store.conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
            relay_count = store.conn.execute("SELECT COUNT(*) FROM relays").fetchone()[0]
            signal_count = store.conn.execute("SELECT COUNT(*) FROM commands").fetchone()[0]
            managed_count = store.conn.execute(
                "SELECT COUNT(*) FROM managed_accounts WHERE enabled=1"
            ).fetchone()[0]
    except Exception:
        user_count = relay_count = signal_count = managed_count = 0
    # Count live MT5 sessions
    try:
        all_sessions = session_manager.get_all_sessions_status()
        active_mt5 = sum(1 for s in all_sessions.values() if s.get("connected"))
    except Exception:
        active_mt5 = 0
    uptime = int(time.time() - _start_time)
    return render_template(
        "admin_dashboard.html",
        admin_user=session.get("admin_user"),
        user_count=user_count,
        relay_count=relay_count,
        signal_count=signal_count,
        managed_count=managed_count,
        active_mt5=active_mt5,
        uptime_secs=uptime,
        version=APP_VERSION,
    )


@app.route("/admin/users", methods=["GET"])
@admin_login_required
def admin_users_page():
    users = store.get_all_users_summary()
    # Attach live MT5 session status to each managed user
    for u in users:
        if u["managed"]:
            status = session_manager.session_status(u["user_id"])
            u["mt5_connected"] = status.get("connected", False)
        else:
            u["mt5_connected"] = None
    return render_template("admin_users.html", users=users, admin_user=session.get("admin_user"))


@app.route("/admin/users/<user_id>/restart-session", methods=["POST"])
@admin_login_required
def admin_restart_session(user_id: str):
    """Force-restart a user's managed MT5 session."""
    acct = store.get_managed_account(user_id)
    if not acct:
        flash(f"No managed account for {user_id}.", "error")
        return redirect(url_for("admin_users_page"))
    try:
        # FIX 4: Reset the circuit breaker before restarting so the supervisor can retry
        session_manager.reset_circuit(user_id)
        password = decrypt_secret(acct["mt5_password_enc"])
        session_manager.start_session(
            user_id,
            int(acct["mt5_login"]),
            password,
            acct["mt5_server"],
            acct.get("mt5_path") or None,
        )
        flash(f"MT5 session restarted for {user_id}.", "success")
    except Exception as exc:
        flash(f"Restart failed: {exc}", "error")
    return redirect(url_for("admin_users_page"))


@app.route("/admin/users/<user_id>/regen-token", methods=["POST"])
@admin_login_required
def admin_regen_token(user_id: str):
    """Generate a new webhook token for a user."""
    import secrets as _secrets
    new_token = _secrets.token_urlsafe(24)
    with store.lock:
        store.conn.execute("UPDATE users SET webhook_token = ? WHERE user_id = ?", (new_token, user_id))
        store.conn.commit()
    flash(f"New webhook token generated for {user_id}.", "success")
    return redirect(url_for("admin_users_page"))


@app.route("/admin/users/<user_id>/suspend", methods=["POST"])
@admin_login_required
def admin_suspend_user(user_id: str):
    """Suspend or unsuspend a user account."""
    action = (request.form.get("action") or "suspend").strip().lower()
    suspended = (action == "suspend")
    try:
        with store.lock:
            # Add suspended column if it doesn't exist (migration-safe)
            try:
                store.conn.execute("ALTER TABLE users ADD COLUMN suspended INTEGER DEFAULT 0")
                store.conn.commit()
            except Exception:
                pass
            store.conn.execute(
                "UPDATE users SET suspended = ? WHERE user_id = ?",
                (1 if suspended else 0, user_id),
            )
            store.conn.commit()
        action_label = "suspended" if suspended else "unsuspended"
        flash(f"User {user_id} {action_label}.", "success")
    except Exception as e:
        flash(f"Failed to update user: {e}", "error")
    return redirect(url_for("admin_users_page"))


@app.route("/admin/signals", methods=["GET"])
@admin_login_required
def admin_signals_page():
    signals = store.get_recent_signal_logs(limit=100)
    return render_template("admin_signals.html", signals=signals, admin_user=session.get("admin_user"))


def _process_signal_for_user(user_id: str, data: dict):
    import re as _re
    if data is None:
        raw_body = request.get_data(as_text=True).strip()
        if raw_body:
            try:
                data = json.loads(raw_body)
            except json.JSONDecodeError:
                return jsonify({"error": "invalid JSON payload"}), 400
    data = data or {}

    # FIX 2: Idempotency check
    # Explicit key always takes precedence (user-provided)
    idem_key = (data.get("idempotency_key") or "").strip()
    if idem_key:
        if store.check_idempotency(idem_key, user_id, ttl_secs=120):
            return jsonify({"status": "duplicate", "code": "DUPLICATE_SIGNAL",
                            "message": "signal already processed — idempotency key matched"}), 200
        store.record_idempotency(idem_key, user_id)
    else:
        # Content-hash dedup: prevent identical signals arriving within 10 seconds
        _action_raw = (data.get("action") or "").strip().upper()
        _symbol_raw = (data.get("symbol") or "").strip().upper()[:20]
        _size_raw = data.get("size", data.get("lot_size", 0.1))
        _sl_raw = data.get("sl", data.get("stop_loss"))
        _tp_raw = data.get("tp", data.get("take_profit"))
        _content_hash = hashlib.sha256(
            json.dumps(
                {"action": _action_raw, "symbol": _symbol_raw,
                 "size": str(_size_raw), "sl": str(_sl_raw), "tp": str(_tp_raw)},
                sort_keys=True,
            ).encode()
        ).hexdigest()[:16]
        _content_idem_key = f"hash:{_content_hash}"
        if store.check_idempotency(_content_idem_key, user_id, ttl_secs=10):
            return jsonify({"status": "duplicate", "code": "DUPLICATE_SIGNAL",
                            "message": "identical signal received within 10 seconds"}), 200
        store.record_idempotency(_content_idem_key, user_id)

    action = (data.get("action") or "").strip().upper()
    symbol = (data.get("symbol") or "").strip().upper()[:20]

    # Validate action
    if action not in ("BUY", "SELL", "CLOSE", "CLOSE_ALL", "CLOSE_BUY", "CLOSE_SELL"):
        return jsonify({"error": f"invalid action: {action}. Must be BUY, SELL, or CLOSE"}), 400

    # Backtest skip
    if data.get("is_backtest") or data.get("backtest"):
        return jsonify({"status": "skipped", "reason": "backtest signal"}), 200

    # Validate symbol
    if action in ("BUY", "SELL") and not symbol:
        return jsonify({"error": "missing symbol"}), 400
    if symbol and len(symbol) > 20:
        return jsonify({"error": "symbol too long (max 20 chars)"}), 400

    # Sanitize comment
    comment = (data.get("comment") or "").strip()
    comment = _re.sub(r'[^a-zA-Z0-9 \-]', '', comment)[:100]

    # Sanitize script_name
    script_name = str(
        data.get("script_name") or data.get("script") or data.get("strategy") or "Uncategorized"
    ).strip()
    script_name = _re.sub(r'[^a-zA-Z0-9 \-_]', '', script_name)[:100] or "Uncategorized"

    sl = data.get("sl", data.get("stop_loss"))
    tp = data.get("tp", data.get("take_profit"))

    # Validate SL/TP if provided
    if sl is not None:
        try:
            sl = float(sl)
            if sl <= 0:
                return jsonify({"error": "sl must be positive"}), 400
        except (TypeError, ValueError):
            sl = None

    if tp is not None:
        try:
            tp = float(tp)
            if tp <= 0:
                return jsonify({"error": "tp must be positive"}), 400
        except (TypeError, ValueError):
            tp = None

    # Circuit breaker check
    circuit = store.get_circuit_status(user_id)
    if circuit.get("broken"):
        return jsonify({"error": "Trading paused: circuit breaker active. Visit your dashboard to reset.", "code": "CIRCUIT_BREAKER"}), 403

    # Support lot_size_pct (percentage of equity) or legacy lot_size (absolute lots)
    lot_size_pct_raw = data.get("lot_size_pct")
    if lot_size_pct_raw is not None:
        try:
            size = float(lot_size_pct_raw)
        except (TypeError, ValueError):
            size = 1.0
        # Negative guard — treat as percentage passed through to relay/worker
        size = max(0.1, min(size, 100.0))
        # Store as negative to signal "percentage mode" to the relay/worker
        size = -size
    else:
        try:
            size = float(data.get("size", data.get("lot_size", 0.1)))
        except (TypeError, ValueError):
            size = 0.1

    # Section 2: pips-based SL/TP defaults (passed from Telegram bot when signal has no absolute SL/TP)
    sl_pips = data.get("sl_pips")
    tp_pips = data.get("tp_pips")

    settings = store.get_user_settings(user_id)
    max_lot_size = float(settings.get("max_lot_size") or 0.5)

    # Apply user/system defaults for pips
    if action in ("BUY", "SELL"):
        user_defaults = store.get_user_defaults(user_id)
        if sl is None and sl_pips is None:
            default_sl = user_defaults.get("default_sl_pips") or SYSTEM_DEFAULT_SL_PIPS
            sl_pips = default_sl
        if tp is None and tp_pips is None:
            default_tp = user_defaults.get("default_tp_pips") or SYSTEM_DEFAULT_TP_PIPS
            tp_pips = default_tp
        # Apply user default lot size if not specified
        if (lot_size_pct_raw is None) and size == 0.1:
            if user_defaults.get("default_lot_size"):
                size = float(user_defaults["default_lot_size"])

    # Only enforce max-lot check for absolute lot sizes (positive).
    # Negative size = percentage mode — the relay/worker converts to lots and enforces its own limits.
    if action in ("BUY", "SELL") and size > 0 and size > max_lot_size:
        msg = f"🔴 Trade rejected: lot size {size} exceeds max {max_lot_size}."
        notify_user(user_id, msg)
        return jsonify({"error": "max lot size exceeded", "max_lot_size": max_lot_size}), 400

    limit_count = int(settings.get("rate_limit_max_trades") or 5)
    limit_window = int(settings.get("rate_limit_window_secs") or 60)
    if action in ("BUY", "SELL"):
        recent_count = store.count_recent_script_commands(user_id, script_name, limit_window)
        if recent_count >= limit_count:
            msg = (
                f"⚠️ Rate limit: script '{script_name}' hit {recent_count} trades in "
                f"{limit_window}s. Execution paused."
            )
            notify_user(user_id, msg)
            from flask import make_response as _mkr3
            _rl_resp = _mkr3(
                jsonify({"error": "rate limit exceeded", "script": script_name}), 429
            )
            _rl_resp.headers["Retry-After"] = "60"
            return _rl_resp

    target_relay = None
    managed_mode = store.is_managed_enabled(user_id)

    if managed_mode:
        target_relay = f"{MANAGED_RELAY_PREFIX}{user_id}"
    else:
        # Get user's relay(s)
        relays = store.list_relays(user_id)
        if not relays:
            return jsonify({"error": "no relay registered for user"}), 400

        # For now, route to the first online relay; in production, load-balance or use user preference
        for relay_id, relay_info in relays.items():
            if relay_info["state"] == "online":
                target_relay = relay_id
                break

        if not target_relay:
            # No online relay; queue for later delivery
            target_relay = list(relays.keys())[0]
            logger.info(f"No online relay for user {user_id}; queuing command")
            key = (user_id, target_relay)
            now = time.time()
            if (now - LAST_OFFLINE_NOTIFY.get(key, 0)) > 300:
                notify_user(user_id, f"🔴 Relay offline for user {user_id}. Commands are queued.")
                LAST_OFFLINE_NOTIFY[key] = now

    # Create command and enqueue
    cmd = Command(user_id, target_relay, action, symbol, size, sl, tp, script_name=script_name)
    cmd.magic = store.get_user_magic_number(user_id)
    cmd.max_lot_size = max_lot_size
    store.enqueue(cmd)

    if managed_mode:
        magic = store.get_user_magic_number(user_id)
        cmd_dict = {
            "action": action, "symbol": symbol, "size": size or 0.1, "sl": sl, "tp": tp,
            "magic": magic,
            "max_lot_size": max_lot_size,
        }
        if sl_pips and sl is None:
            cmd_dict["sl_pips"] = float(sl_pips)
        if tp_pips and tp is None:
            cmd_dict["tp_pips"] = float(tp_pips)
        result = session_manager.execute(user_id, cmd_dict)
        result["mode"] = "managed-vps"
        status = CommandStatus.EXECUTED if result.get("status") == "executed" else CommandStatus.FAILED
        store.update_result(user_id, target_relay, cmd.id, status, result)
        if status == CommandStatus.EXECUTED:
            notify_user(user_id, f"🟢 {action} {size} {symbol} executed.")
        else:
            # FIX 11: Use user-friendly error message with actionable hint
            _retcode = result.get("retcode")
            if _retcode:
                _short_msg, _hint = _mt5_user_friendly_error(_retcode)
                _fail_msg = f"🔴 Trade failed: {_short_msg}. {_hint}"
            else:
                _fail_msg = f"🔴 Trade failed: {result.get('error_message') or result.get('error') or 'Unknown error'}"
            notify_user(user_id, _fail_msg)
            # Circuit breaker: check consecutive losses
            if action in ("BUY", "SELL"):
                losses = store.count_consecutive_losses(user_id)
                limit = store.get_consecutive_loss_limit(user_id)
                if losses >= limit:
                    store.set_circuit_broken(user_id, True)
                    cb_msg = f"🔴 Circuit breaker activated: {limit} consecutive trade failures. Trading paused. Visit dashboard to reset."
                    try:
                        telegram_manager.send_session_notification(user_id, cb_msg)
                    except Exception:
                        pass
                    notify_user(user_id, cb_msg)
        return jsonify({
            "status": result.get("status", "failed"),
            "mode": "managed-vps",
            "command_id": cmd.id,
            "relay_id": target_relay,
            "result": result,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }), 200 if status == CommandStatus.EXECUTED else 500

    logger.info(
        f"Signal received: user={user_id}, relay={target_relay}, script={script_name}, action={action}, symbol={symbol}"
    )
    return jsonify({
        "status": "queued",
        "command_id": cmd.id,
        "relay_id": target_relay,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }), 202


@app.route("/signal", methods=["POST"])
def receive_signal():
    """
    Receive TradingView alert with user_id/api_key in header or body.
    POST /signal
    """
    data = request.get_json(silent=True)
    user_id = (request.headers.get("X-User-ID") or (data or {}).get("user_id") or "").strip()
    api_key = (request.headers.get("X-API-Key") or (data or {}).get("api_key") or "").strip()

    if not user_id:
        return jsonify({"error": "missing user_id (header X-User-ID or body user_id)"}), 400
    if REQUIRE_API_KEY and not api_key:
        return jsonify({"error": "missing api_key (header X-API-Key or body api_key)"}), 401
    if api_key and not verify_api_key(user_id, api_key):
        return jsonify({"error": "unauthorized"}), 401

    # Section 9: rate limiting — 10 signals per minute per API key
    rl_key = f"signal:{user_id}"
    if not _rate_check(rl_key, max_calls=10, window_secs=60):
        from flask import make_response
        resp = make_response(jsonify({"error": "rate limit exceeded", "code": "RATE_LIMIT"}), 429)
        resp.headers["Retry-After"] = "60"
        return resp

    return _process_signal_for_user(user_id, data)


@app.route("/signal/<webhook_token>", methods=["POST"])
def receive_signal_by_token(webhook_token):
    """
    Receive TradingView alert by unique per-user webhook URL.
    POST /signal/<webhook_token>
    """
    user_id = store.get_user_id_by_webhook_token((webhook_token or "").strip())
    if not user_id:
        return jsonify({"error": "invalid webhook token"}), 404

    # Rate-limit per resolved user_id (same budget as the API-key endpoint)
    if not _rate_check(f"signal:{user_id}", max_calls=10, window_secs=60):
        from flask import make_response as _mkr
        _resp = _mkr(jsonify({"error": "rate limit exceeded", "code": "RATE_LIMIT"}), 429)
        _resp.headers["Retry-After"] = "60"
        return _resp

    data = request.get_json(silent=True)
    return _process_signal_for_user(user_id, data)

@app.route("/relay/register", methods=["POST"])
@_rate_limit("10 per minute")
def relay_register():
    """
    Register a relay.
    POST /relay/register
    Headers: X-User-ID
    Body: {relay_id, relay_type}
    """
    user_id, err = require_user_id()
    if err:
        return err
    auth_err = require_user_auth(user_id)
    if auth_err:
        return auth_err

    if not store.user_exists(user_id):
        return jsonify({"error": "user not provisioned"}), 403

    data = request.get_json() or {}
    relay_id = data.get("relay_id", f"relay-{uuid.uuid4().hex[:8]}")
    relay_type = data.get("relay_type", "self-hosted")  # or "managed"

    token = store.register_relay(user_id, relay_id, relay_type)

    return jsonify({
        "status": "registered",
        "relay_id": relay_id,
        "token": token,
        "heartbeat_interval": 10,  # seconds
        "poll_timeout": 5,  # seconds
    }), 201


@app.route("/account/register", methods=["POST"])
def account_register():
    """
    Create a new user account from the desktop app.
    POST /account/register
    Body: {user_id, password, invite_code}
    Returns: {status, user_id, api_key}
    """
    data = request.get_json(silent=True) or {}
    user_id = (data.get("user_id") or "").strip()
    password = data.get("password") or ""
    invite_code = (data.get("invite_code") or "").strip()

    if not user_id or len(user_id) < 3:
        return jsonify({"error": "Username must be at least 3 characters"}), 400
    if not password or len(password) < 8:
        return jsonify({"error": "Password must be at least 8 characters"}), 400
    if not invite_code:
        return jsonify({"error": "An invite code is required"}), 400

    valid, reason = store.validate_invite_code(invite_code)
    if not valid:
        return jsonify({"error": f"Invalid or already used invite code: {reason}"}), 400

    try:
        api_key = store.register_dashboard_user(user_id, password)
    except ValueError:
        return jsonify({"error": "Username already taken"}), 409

    store.consume_invite_code(invite_code, user_id)
    store.assign_script_to_user(user_id, "default-script")
    return jsonify({"status": "ok", "user_id": user_id, "api_key": api_key}), 201


@app.route("/relay/login", methods=["POST"])
def relay_login():
    """
    Relay login using website credentials.
    POST /relay/login
    Body: {user_id, password, relay_id?, relay_type?}
    """
    data = request.get_json(silent=True) or {}
    user_id = (data.get("user_id") or "").strip()
    password = data.get("password") or ""
    relay_id = (data.get("relay_id") or f"relay-{uuid.uuid4().hex[:8]}").strip()
    relay_type = (data.get("relay_type") or "self-hosted").strip()

    if not user_id or not password:
        return jsonify({"error": "missing user_id or password"}), 400

    if not store.verify_dashboard_login(user_id, password):
        return jsonify({"error": "invalid credentials"}), 401

    if not store.user_exists(user_id):
        return jsonify({"error": "user not found"}), 404

    token = store.register_relay(user_id, relay_id, relay_type)
    api_key = store.regenerate_api_key(user_id)
    return jsonify({
        "status": "authenticated",
        "user_id": user_id,
        "relay_id": relay_id,
        "token": token,
        "api_key": api_key,
        "heartbeat_interval": 10,
        "poll_timeout": 25,
    }), 200


@app.route("/managed/setup", methods=["POST"])
@_rate_limit("10 per minute")
def managed_setup():
    """
    One-time setup for VPS-managed execution for a user.
    Headers: X-User-ID, X-API-Key
    Body: {mt5_login, mt5_password, mt5_server, mt5_path?}
    """
    user_id, err = require_user_id()
    if err:
        return err
    auth_err = require_user_auth(user_id)
    if auth_err:
        return auth_err

    data = request.get_json(silent=True) or {}
    mt5_login = data.get("mt5_login")
    mt5_password = data.get("mt5_password")
    mt5_server = data.get("mt5_server")
    mt5_path = data.get("mt5_path", "")

    if not mt5_login or not mt5_password or not mt5_server:
        return jsonify({"error": "missing mt5_login, mt5_password, or mt5_server"}), 400

    try:
        mt5_login_int = int(mt5_login)
    except (ValueError, TypeError):
        return jsonify({"error": f"MT5 Account Number must be a number, got: {mt5_login!r}"}), 400

    try:
        store.upsert_managed_account(user_id, mt5_login_int, str(mt5_password), str(mt5_server), str(mt5_path or ""))
    except RuntimeError as exc:
        logger.error(f"managed_setup upsert error for {user_id}: {exc}")
        return jsonify({"error": "failed to save managed account configuration"}), 500

    # Start (or restart) the persistent MT5 session immediately — warm before first trade
    session_manager.start_session(
        user_id, mt5_login_int, str(mt5_password), str(mt5_server), str(mt5_path) or None
    )

    return jsonify({
        "status": "managed_setup_complete",
        "user_id": user_id,
        "managed_execution": True,
    }), 200


@app.route("/managed/setup/login", methods=["POST"])
@_rate_limit("10 per minute")
def managed_setup_login():
    """
    One-time setup for VPS-managed execution authenticated by dashboard login.
    Body: {user_id, password, mt5_login, mt5_password, mt5_server, mt5_path?}
    """
    data = request.get_json(silent=True) or {}
    user_id = (data.get("user_id") or "").strip()
    password = data.get("password") or ""
    mt5_login = data.get("mt5_login")
    mt5_password = data.get("mt5_password")
    mt5_server = data.get("mt5_server")
    mt5_path = data.get("mt5_path", "")

    if not user_id or not password:
        return jsonify({"error": "missing user_id or password"}), 400
    if not store.verify_dashboard_login(user_id, password):
        return jsonify({"error": "invalid credentials"}), 401
    if not mt5_login or not mt5_password or not mt5_server:
        return jsonify({"error": "missing mt5_login, mt5_password, or mt5_server"}), 400

    try:
        mt5_login_int = int(mt5_login)
    except (ValueError, TypeError):
        return jsonify({"error": f"MT5 Account Number must be a number, got: {mt5_login!r}"}), 400

    try:
        store.upsert_managed_account(user_id, mt5_login_int, str(mt5_password), str(mt5_server), str(mt5_path or ""))
    except RuntimeError as exc:
        logger.error(f"managed_setup_login upsert error for {user_id}: {exc}")
        return jsonify({"error": "failed to save managed account configuration"}), 500

    # Start (or restart) the persistent MT5 session immediately — warm before first trade
    session_manager.start_session(
        user_id, mt5_login_int, str(mt5_password), str(mt5_server), str(mt5_path) or None
    )

    return jsonify({"status": "managed_setup_complete", "user_id": user_id, "managed_execution": True}), 200


@app.route("/managed/status", methods=["GET"])
def managed_status():
    user_id, err = require_user_id()
    if err:
        return err
    auth_err = require_user_auth(user_id)
    if auth_err:
        return auth_err

    account = store.get_managed_account(user_id)
    session = session_manager.session_status(user_id)
    return jsonify({
        "user_id": user_id,
        "managed_execution": bool(account and account.get("enabled") == 1),
        "configured": bool(account),
        "connected": session.get("connected", False),
        "updated_at": account.get("updated_at") if account else None,
    })

@app.route("/relay/heartbeat", methods=["POST"])
def relay_heartbeat():
    """
    Relay sends heartbeat.
    POST /relay/heartbeat
    Headers: X-User-ID, X-Relay-ID, X-Relay-Token
    Body: {metadata}
    """
    user_id, err = require_user_id()
    if err:
        return err
    relay_id = request.headers.get("X-Relay-ID", "")
    token = request.headers.get("X-Relay-Token", "")
    data = request.get_json() or {}

    if not relay_id or not token:
        return jsonify({"error": "missing relay ID or token"}), 400

    if not verify_relay_token(user_id, relay_id, token):
        return jsonify({"error": "unauthorized"}), 401

    if not store.heartbeat(user_id, relay_id, data.get("metadata")):
        return jsonify({"error": "relay not found"}), 404

    # Include server-side MT5 session status for managed/VPS users
    session = session_manager.session_status(user_id)
    return jsonify({
        "status": "ack",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "vps_mt5_connected": session.get("connected", False),
        "vps_active": session.get("active", False),
    })

@app.route("/relay/poll", methods=["POST"])
def relay_poll():
    """
    Relay polls for commands.
    POST /relay/poll
    Headers: X-User-ID, X-Relay-ID, X-Relay-Token
    Returns: {commands: []}
    """
    user_id, err = require_user_id()
    if err:
        return err
    relay_id = request.headers.get("X-Relay-ID", "")
    token = request.headers.get("X-Relay-Token", "")

    if not relay_id or not token:
        return jsonify({"error": "missing relay ID or token"}), 400

    if not verify_relay_token(user_id, relay_id, token):
        return jsonify({"error": "unauthorized"}), 401

    wait_seconds = request.args.get("wait", "0").strip()
    try:
        wait_seconds = max(0, min(int(wait_seconds), 25))
    except ValueError:
        wait_seconds = 0

    cmds = []
    if wait_seconds > 0:
        # Non-blocking poll with early exit
        # Use smaller sleep intervals for responsiveness
        poll_interval = 0.01  # 10ms — reduces command delivery latency from up to 100ms to up to 10ms
        deadline = time.time() + wait_seconds
        while time.time() < deadline:
            cmds = store.dequeue(user_id, relay_id, COMMAND_DEQUEUE_LIMIT)
            if cmds:
                break
            # Sleep in small intervals to avoid blocking too long
            remaining = deadline - time.time()
            if remaining <= 0:
                break
            time.sleep(min(poll_interval, remaining))
    else:
        cmds = store.dequeue(user_id, relay_id, COMMAND_DEQUEUE_LIMIT)

    commands_data = [cmd.to_dict() for cmd in cmds]

    logger.info(f"Relay poll: user={user_id}, relay={relay_id}, commands={len(commands_data)}")
    return jsonify({
        "commands": commands_data,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })

@app.route("/relay/result", methods=["POST"])
def relay_result():
    """
    Relay reports command execution result.
    POST /relay/result
    Headers: X-User-ID, X-Relay-ID, X-Relay-Token
    Body: {command_id, status, result}
    """
    user_id, err = require_user_id()
    if err:
        return err
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
    if not store.update_result(user_id, relay_id, cmd_id, status, result):
        return jsonify({"error": "command not found"}), 404

    if status == CommandStatus.EXECUTED:
        notify_user(user_id, f"🟢 Trade executed (relay): {result.get('order_id', '')}")
    elif status == CommandStatus.FAILED:
        # FIX 11: User-friendly error with hint
        _r_retcode = result.get("retcode")
        if _r_retcode:
            _r_short, _r_hint = _mt5_user_friendly_error(_r_retcode)
            notify_user(user_id, f"🔴 Trade failed (relay): {_r_short}. {_r_hint}")
        else:
            notify_user(user_id, f"🔴 Trade failed (relay): {result.get('error_message') or result.get('error') or 'Unknown error'}")

    logger.info(f"Relay result: command={cmd_id}, status={status.value}")
    return jsonify({
        "status": "ack",
        "command_id": cmd_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })

@app.route("/commands/<cmd_id>", methods=["GET"])
def get_command(cmd_id):
    """
    Get command status.
    GET /commands/{id}
    Headers: X-User-ID
    """
    user_id, err = require_user_id()
    if err:
        return err
    auth_err = require_user_auth(user_id)
    if auth_err:
        return auth_err

    cmd = store.get_command(cmd_id)

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
    user_id, err = require_user_id()
    if err:
        return err
    auth_err = require_user_auth(user_id)
    if auth_err:
        return auth_err

    relays = store.list_relays(user_id)
    return jsonify({
        "relays": relays,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })

@app.route("/stats", methods=["GET"])
def get_stats():
    """
    Get bridge statistics.
    GET /stats
    """
    user_id, err = require_user_id()
    if err:
        return err
    auth_err = require_user_auth(user_id)
    if auth_err:
        return auth_err

    relays = store.list_relays(user_id)
    online_count = sum(1 for r in relays.values() if r["state"] == "online")

    return jsonify({
        "user_id": user_id,
        "relays": {
            "total": len(relays),
            "online": online_count,
            "offline": len(relays) - online_count,
        },
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })



@app.route("/version", methods=["GET"])
def get_version():
    if RELAY_MANIFEST_URL:
        now = time.time()
        if now - _manifest_cache["ts"] > 300 or not _manifest_cache["data"]:
            try:
                r = requests.get(RELAY_MANIFEST_URL, timeout=5)
                if r.status_code == 200:
                    _manifest_cache["data"] = r.json()
                    _manifest_cache["ts"] = now
            except Exception:
                pass
        if _manifest_cache["data"]:
            return jsonify(_manifest_cache["data"])
    # Fallback to env vars
    return jsonify({
        "version": APP_VERSION,
        "app_version": APP_VERSION,
        "windows_url": RELAY_DOWNLOAD_URL,
        "mac_url": RELAY_DOWNLOAD_URL,
        "relay_download_url": RELAY_DOWNLOAD_URL,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })


@app.route("/settings", methods=["GET", "POST"])
def user_settings_api():
    user_id, err = require_user_id()
    if err:
        return err
    auth_err = require_user_auth(user_id)
    if auth_err:
        return auth_err

    if request.method == "GET":
        return jsonify(store.get_user_settings(user_id))

    data = request.get_json(silent=True) or {}
    updates = {}
    if "max_lot_size" in data:
        updates["max_lot_size"] = float(data.get("max_lot_size") or 0.5)
    if "rate_limit_max_trades" in data:
        updates["rate_limit_max_trades"] = int(data.get("rate_limit_max_trades") or 5)
    if "rate_limit_window_secs" in data:
        updates["rate_limit_window_secs"] = int(data.get("rate_limit_window_secs") or 60)
    if "notifications_enabled" in data:
        updates["notifications_enabled"] = 1 if bool(data.get("notifications_enabled")) else 0
    if "telegram_bot_token" in data:
        updates["telegram_bot_token"] = (data.get("telegram_bot_token") or "").strip()
    if "telegram_chat_id" in data:
        updates["telegram_chat_id"] = (data.get("telegram_chat_id") or "").strip()
    if "discord_webhook_url" in data:
        updates["discord_webhook_url"] = (data.get("discord_webhook_url") or "").strip()
    # Section 2: default lot/SL/TP
    if "default_lot_size" in data:
        v = data["default_lot_size"]
        updates["default_lot_size"] = float(v) if v else None
    if "default_sl_pips" in data:
        v = data["default_sl_pips"]
        updates["default_sl_pips"] = float(v) if v else None
    if "default_tp_pips" in data:
        v = data["default_tp_pips"]
        updates["default_tp_pips"] = float(v) if v else None

    store.update_user_settings(user_id, updates)
    return jsonify(store.get_user_settings(user_id))


# ==================== Telegram Account Linking (Section 5) ====================

@app.route("/telegram/link", methods=["POST"])
def telegram_link():
    """
    Generate a one-time link token so the user can send /start <token> to the bot.
    POST /telegram/link  (requires auth)
    Returns: { "link_url": "https://t.me/<bot>?start=<token>", "token": "..." }
    """
    user_id, err = resolve_user_from_request()
    if err is not None:
        return err

    token = store.create_telegram_link_token(user_id)
    bot_username = telegram_manager.bot_username
    if bot_username:
        link_url = f"https://t.me/{bot_username}?start={token}"
    else:
        link_url = None

    return jsonify({
        "token": token,
        "link_url": link_url,
        "expires_in": 600,
    })


# ==================== Symbol Filter API (Section 3) ====================

@app.route("/api/user/symbols", methods=["GET", "POST", "DELETE"])
def user_symbols_api():
    """Manage per-user symbol whitelist."""
    user_id, err = resolve_user_from_request()
    if err is not None:
        return err

    if request.method == "GET":
        symbols = store.get_user_allowed_symbols(user_id)
        return jsonify({"symbols": symbols, "filter_active": bool(symbols)})

    data = request.get_json(silent=True) or {}
    symbol = (data.get("symbol") or "").strip().upper()
    if not symbol:
        return jsonify({"error": "symbol is required"}), 400

    if request.method == "POST":
        store.add_user_allowed_symbol(user_id, symbol)
        return jsonify({"symbols": store.get_user_allowed_symbols(user_id)})
    else:  # DELETE
        store.remove_user_allowed_symbol(user_id, symbol)
        return jsonify({"symbols": store.get_user_allowed_symbols(user_id)})


# ==================== Telegram Signal Channel API ====================

@app.route("/api/telegram/channels", methods=["GET", "POST"])
def telegram_channels_api():
    user_id, err = resolve_user_from_request()
    if err is not None:
        return err
    if request.method == "GET":
        channels = store.list_telegram_channels(user_id)
        return jsonify({
            "channels": channels,
            "bot_username": telegram_manager.bot_username,
            "bot_running": telegram_manager.is_running,
            "llm_configured": _llm_processor is not None,
            "llm_running": _llm_processor.is_running if _llm_processor else False,
            "llm_stats": _llm_processor.stats if _llm_processor else None,
        })

    # POST — add a new channel
    data = request.get_json(silent=True) or {}
    chat_id = str(data.get("chat_id", "")).strip()
    if not chat_id:
        return jsonify({"error": "chat_id is required"}), 400

    # Verify bot access to the channel
    if telegram_manager.is_running:
        try:
            chat_info = telegram_manager.verify_channel_access(chat_id)
            chat_title = chat_info.get("title", "")
        except Exception as exc:
            return jsonify({"error": f"Bot cannot access this channel: {exc}"}), 400
    else:
        chat_title = data.get("chat_title", "")

    # FIX 8: Validate allowed_symbols
    raw_symbols = data.get("allowed_symbols")
    validated_symbols_str = None
    if raw_symbols is not None:
        if isinstance(raw_symbols, str):
            try:
                sym_list = json.loads(raw_symbols)
            except (json.JSONDecodeError, ValueError):
                sym_list = [s.strip() for s in raw_symbols.split(",") if s.strip()]
        elif isinstance(raw_symbols, list):
            sym_list = raw_symbols
        else:
            sym_list = []
        if len(sym_list) > 50:
            return jsonify({"error": "allowed_symbols: max 50 symbols"}), 400
        _sym_re = _re_top.compile(r'^[A-Z0-9]{2,10}$')
        cleaned = []
        for sym in sym_list:
            s = str(sym).strip().upper()
            if not _sym_re.match(s):
                return jsonify({"error": f"allowed_symbols: invalid symbol '{sym}' (must be 2-10 alphanumeric chars)"}), 400
            cleaned.append(s)
        validated_symbols_str = json.dumps(cleaned) if cleaned else None

    channel_id = str(uuid.uuid4())
    try:
        store.add_telegram_channel(
            channel_id=channel_id,
            user_id=user_id,
            chat_id=chat_id,
            chat_title=chat_title,
            risk_pct=float(data.get("risk_pct", 1.0)),
            max_trades_per_day=int(data.get("max_trades_per_day", 10)),
            allowed_symbols=validated_symbols_str,
            script_name=data.get("script_name", "Telegram"),
        )
    except Exception as exc:
        if "UNIQUE constraint" in str(exc):
            return jsonify({"error": "Channel already connected"}), 409
        raise
    return jsonify({"channel_id": channel_id, "chat_title": chat_title}), 201


@app.route("/api/telegram/channels/<channel_id>", methods=["PUT", "DELETE"])
def telegram_channel_manage_api(channel_id):
    user_id, err = resolve_user_from_request()
    if err is not None:
        return err
    channel = store.get_telegram_channel(channel_id)
    if not channel or channel["user_id"] != user_id:
        return jsonify({"error": "not found"}), 404

    if request.method == "DELETE":
        store.delete_telegram_channel(channel_id)
        return jsonify({"deleted": True})

    # PUT — update config
    data = request.get_json(silent=True) or {}
    # FIX 8: Validate allowed_symbols on update too
    if "allowed_symbols" in data and data["allowed_symbols"] is not None:
        raw_syms = data["allowed_symbols"]
        if isinstance(raw_syms, str):
            try:
                sym_list2 = json.loads(raw_syms)
            except (json.JSONDecodeError, ValueError):
                sym_list2 = [s.strip() for s in raw_syms.split(",") if s.strip()]
        elif isinstance(raw_syms, list):
            sym_list2 = raw_syms
        else:
            sym_list2 = []
        if len(sym_list2) > 50:
            return jsonify({"error": "allowed_symbols: max 50 symbols"}), 400
        _sym_re2 = _re_top.compile(r'^[A-Z0-9]{2,10}$')
        cleaned2 = []
        for sym2 in sym_list2:
            s2 = str(sym2).strip().upper()
            if not _sym_re2.match(s2):
                return jsonify({"error": f"allowed_symbols: invalid symbol '{sym2}'"}), 400
            cleaned2.append(s2)
        data = dict(data)
        data["allowed_symbols"] = json.dumps(cleaned2) if cleaned2 else None
    store.update_telegram_channel(channel_id, data)
    return jsonify(store.get_telegram_channel(channel_id))


@app.route("/api/telegram/channels/<channel_id>/toggle", methods=["POST"])
def telegram_channel_toggle_api(channel_id):
    user_id, err = resolve_user_from_request()
    if err is not None:
        return err
    channel = store.get_telegram_channel(channel_id)
    if not channel or channel["user_id"] != user_id:
        return jsonify({"error": "not found"}), 404
    new_enabled = 0 if channel["enabled"] else 1
    store.update_telegram_channel(channel_id, {"enabled": new_enabled})
    return jsonify({"enabled": bool(new_enabled)})


@app.route("/api/telegram/signals", methods=["GET"])
def telegram_signals_api():
    user_id, err = resolve_user_from_request()
    if err is not None:
        return err
    channel_id = request.args.get("channel_id")
    limit = min(int(request.args.get("limit", 50)), 200)
    logs = store.list_telegram_signal_log(user_id=user_id, channel_id=channel_id, limit=limit)
    return jsonify({"signals": logs})


@app.route("/api/telegram/test-parse", methods=["POST"])
def telegram_test_parse_api():
    _, err = resolve_user_from_request()
    if err is not None:
        return err
    data = request.get_json(silent=True) or {}
    text = data.get("text", "")
    use_llm = data.get("use_llm", False)
    if not text:
        return jsonify({"error": "text is required"}), 400

    from telegram_signal_parser import parse_telegram_message
    result = parse_telegram_message(text)
    response = {
        "action": result.action,
        "symbol": result.symbol,
        "entry": result.entry,
        "sl": result.sl,
        "tp_list": result.tp_list,
        "confidence": result.confidence,
        "skip_reason": result.skip_reason,
        "management_type": result.management_type,
        "parser": "regex",
    }

    # If regex parser failed or low confidence, and LLM requested, try LLM
    if use_llm and _llm_processor and _llm_processor._llm.is_configured:
        if result.skip_reason or result.confidence < 0.7:
            llm_result = _llm_processor._llm.parse_signal_text(text)
            response["llm_result"] = {
                "action": llm_result.action,
                "symbol": llm_result.symbol,
                "entry": llm_result.entry,
                "sl": llm_result.sl,
                "tp_list": llm_result.tp_list,
                "confidence": llm_result.confidence,
                "reasoning": llm_result.reasoning,
                "error": llm_result.error,
                "parser": "gpt-4o-mini",
            }

    return jsonify(response)


@app.route("/dashboard/summary/login", methods=["POST"])
def dashboard_summary_login():
    data = request.get_json(silent=True) or {}
    user_id = (data.get("user_id") or "").strip()
    password = data.get("password") or ""
    api_key = (data.get("api_key") or "").strip()

    if not user_id:
        return jsonify({"error": "missing user_id"}), 400

    authed = False
    if password:
        authed = store.verify_dashboard_login(user_id, password)
    elif api_key:
        authed = store.verify_api_key(user_id, api_key)

    if not authed:
        return jsonify({"error": "invalid credentials"}), 401

    dashboard = store.get_dashboard_data(user_id)
    webhook_token = store.get_or_create_webhook_token(user_id)
    root_url = get_public_base_url()

    # Generate (or regenerate) an API key for this session
    # NOTE: api_key is not stored in plaintext; we regenerate on each login.
    # The desktop app caches the returned api_key in the OS keyring.
    stored_api_key = store.regenerate_api_key(user_id)

    # Managed connection / broker status
    managed_connected = False
    broker_connected = False
    if store.is_managed_enabled(user_id):
        try:
            ping_result = session_manager.execute(user_id, {"_action": "ping"})
            managed_connected = ping_result.get("connected", False)
            broker_connected = managed_connected
        except Exception:
            pass

    # Circuit breaker status
    circuit_status = store.get_circuit_status(user_id)
    circuit_broken = circuit_status.get("broken", False)

    # User plan & magic number
    plan = store.get_user_plan(user_id)
    magic_number = store.get_user_magic_number(user_id)

    return jsonify({
        "dashboard": dashboard,
        "webhook_url": f"{root_url}/signal/{webhook_token}",
        "api_key": stored_api_key,
        "managed_connected": managed_connected,
        "broker_connected": broker_connected,
        "circuit_broken": circuit_broken,
        "magic_number": magic_number,
        "plan": plan,
        "settings": store.get_user_settings(user_id),
    })


@app.route("/panic/close-all", methods=["POST"])
def panic_close_all():
    user_id, err = require_user_id()
    if err:
        return err
    auth_err = require_user_auth(user_id)
    if auth_err:
        return auth_err

    managed_mode = store.is_managed_enabled(user_id)
    target_relay = f"{MANAGED_RELAY_PREFIX}{user_id}" if managed_mode else None

    if not managed_mode:
        relays = store.list_relays(user_id)
        if not relays:
            return jsonify({"error": "no relay available"}), 400
        target_relay = next(iter(relays.keys()))

    cmd = Command(user_id, target_relay, "CLOSE_ALL", "", 0.0, None, None, script_name="panic")
    store.enqueue(cmd)

    if managed_mode:
        cmd_dict = {"action": "CLOSE_ALL", "symbol": "", "size": 0.0, "sl": None, "tp": None}
        result = session_manager.execute(user_id, cmd_dict)
        result["mode"] = "managed-vps"
        status = CommandStatus.EXECUTED if result.get("status") == "executed" else CommandStatus.FAILED
        store.update_result(user_id, target_relay, cmd.id, status, result)
        notify_user(user_id, f"⚠️ Panic close-all executed: {result.get('status')}")
        return jsonify({"status": result.get("status"), "result": result})

    notify_user(user_id, "⚠️ Panic close-all queued to relay")
    return jsonify({"status": "queued", "command_id": cmd.id})


# ==================== Analytics (Section 3) ====================

@app.route("/api/analytics", methods=["GET"])
def analytics_api():
    user_id, err = resolve_user_from_request()
    if err is not None:
        return err

    days = min(int(request.args.get("days", 30)), 90)
    cutoff = time.time() - days * 86400

    with store.lock:
        total = store.conn.execute(
            "SELECT COUNT(*) FROM commands WHERE user_id = ? AND created_at > ?",
            (user_id, cutoff),
        ).fetchone()[0]
        executed = store.conn.execute(
            "SELECT COUNT(*) FROM commands WHERE user_id = ? AND created_at > ? AND status = 'executed'",
            (user_id, cutoff),
        ).fetchone()[0]
        failed = store.conn.execute(
            "SELECT COUNT(*) FROM commands WHERE user_id = ? AND created_at > ? AND status = 'failed'",
            (user_id, cutoff),
        ).fetchone()[0]
        by_script = store.conn.execute(
            """SELECT script_name, COUNT(*) as cnt,
               SUM(CASE WHEN status='executed' THEN 1 ELSE 0 END) as ok,
               SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END) as fail
               FROM commands WHERE user_id = ? AND created_at > ?
               GROUP BY script_name ORDER BY cnt DESC LIMIT 10""",
            (user_id, cutoff),
        ).fetchall()

    return jsonify({
        "period_days": days,
        "total_signals": total,
        "executed": executed,
        "failed": failed,
        "success_rate": round(executed / total * 100, 1) if total else 0,
        "by_script": [{"script": r["script_name"], "total": r["cnt"], "executed": r["ok"], "failed": r["fail"]} for r in by_script],
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })


# ==================== Trade Export (Section 6) ====================

@app.route("/api/export/trades", methods=["GET"])
def export_trades():
    user_id, err = resolve_user_from_request()
    if err is not None:
        return err

    days = min(int(request.args.get("days", 30)), 365)
    cutoff = time.time() - days * 86400

    with store.lock:
        rows = store.conn.execute(
            """SELECT action, symbol, size, sl, tp, script_name, status, created_at, result_json
               FROM commands WHERE user_id = ? AND created_at > ?
               ORDER BY created_at DESC""",
            (user_id, cutoff),
        ).fetchall()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["timestamp", "action", "symbol", "size", "sl", "tp", "script", "status", "result"])
    for row in rows:
        ts = datetime.fromtimestamp(row["created_at"], tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        result = ""
        if row["result_json"]:
            try:
                r = json.loads(row["result_json"])
                result = r.get("error_message") or r.get("error") or r.get("status") or ""
            except Exception:
                pass
        writer.writerow([ts, row["action"], row["symbol"], row["size"], row["sl"], row["tp"],
                         row["script_name"], row["status"], result])

    output.seek(0)
    from flask import Response
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename=trades_{user_id}_{days}d.csv"},
    )


@app.route("/dashboard/export-trades", methods=["GET"])
@login_required
def dashboard_export_trades():
    """Session-authenticated trade journal CSV export from web dashboard."""
    user_id = session["dashboard_user"]
    days = min(int(request.args.get("days", 30)), 365)
    cutoff = time.time() - days * 86400

    with store.lock:
        rows = store.conn.execute(
            """SELECT action, symbol, size, sl, tp, script_name, status, created_at, result_json
               FROM commands WHERE user_id = ? AND created_at > ?
               ORDER BY created_at DESC""",
            (user_id, cutoff),
        ).fetchall()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["timestamp", "action", "symbol", "size", "sl", "tp", "script", "status", "result"])
    for row in rows:
        ts = datetime.fromtimestamp(row["created_at"], tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        result = ""
        if row["result_json"]:
            try:
                r = json.loads(row["result_json"])
                result = r.get("error_message") or r.get("error") or r.get("status") or ""
            except Exception:
                pass
        writer.writerow([ts, row["action"], row["symbol"], row["size"], row["sl"], row["tp"],
                         row["script_name"], row["status"], result])

    output.seek(0)
    from flask import Response as _Resp
    return _Resp(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename=trades_{user_id}_{days}d.csv"},
    )


# ==================== Circuit Breaker Reset (Section 7) ====================

@app.route("/api/circuit-breaker/reset", methods=["POST"])
def reset_circuit_breaker():
    user_id, err = resolve_user_from_request()
    if err is not None:
        return err
    store.set_circuit_broken(user_id, False)
    return jsonify({"status": "reset", "message": "circuit breaker cleared"})


@app.route("/dashboard/reset-circuit-breaker", methods=["POST"])
@login_required
def dashboard_reset_circuit_breaker():
    """Session-authenticated circuit breaker reset from web dashboard."""
    user_id = session["dashboard_user"]
    store.set_circuit_broken(user_id, False)
    flash("Circuit breaker reset. Trading is now enabled.", "success")
    return redirect(url_for("dashboard_page"))


# ==================== MT5 Account Info (Section 8) ====================

@app.route("/api/mt5/account-info", methods=["GET"])
@app.route("/managed/account-info", methods=["GET"])
def mt5_account_info():
    user_id, err = resolve_user_from_request()
    if err is not None:
        return err

    managed_mode = store.is_managed_enabled(user_id)
    if not managed_mode:
        return jsonify({"error": "managed mode not enabled"}), 400

    try:
        result = session_manager.execute(user_id, {"_action": "ACCOUNT_INFO"})
        return jsonify(result)
    except Exception as exc:
        logger.error(f"mt5_account_info error for {user_id}: {exc}")
        return jsonify({"error": "failed to retrieve account info"}), 500


# ==================== Direct Trade Execution (Section 9) ====================

@app.route("/api/trade", methods=["POST"])
def api_trade():
    """Execute a trade directly via managed session or queue via relay."""
    user_id, err = resolve_user_from_request()
    if err is not None:
        return err

    data = request.get_json(silent=True) or {}
    action = (data.get("action") or "").strip().upper()
    symbol = (data.get("symbol") or "").strip().upper()[:20]
    size = data.get("size", 0.0)

    if not action:
        return jsonify({"error": "missing action"}), 400

    # Idempotency check (explicit key or content-hash)
    idem_key = (data.get("idempotency_key") or "").strip()
    if idem_key:
        if store.check_idempotency(idem_key, user_id, ttl_secs=120):
            return jsonify({"status": "duplicate", "idempotency_key": idem_key}), 200
        store.record_idempotency(idem_key, user_id)
    else:
        _content_hash = hashlib.sha256(
            json.dumps({"action": action, "symbol": symbol, "size": str(size)},
                       sort_keys=True).encode()
        ).hexdigest()[:16]
        _content_key = f"hash:{_content_hash}"
        if store.check_idempotency(_content_key, user_id, ttl_secs=10):
            return jsonify({"status": "duplicate"}), 200
        store.record_idempotency(_content_key, user_id)

    if store.is_managed_enabled(user_id):
        cmd = {"action": action, "symbol": symbol, "size": size}
        if data.get("sl") is not None:
            cmd["sl"] = data["sl"]
        if data.get("tp") is not None:
            cmd["tp"] = data["tp"]
        try:
            result = session_manager.execute(user_id, cmd)
            return jsonify(result)
        except Exception as exc:
            logger.error(f"api_trade managed error for {user_id}: {exc}")
            return jsonify({"error": "trade execution failed"}), 500

    # Relay path — queue as command
    relays = store.list_relays(user_id)
    if not relays:
        return jsonify({"error": "no relay registered"}), 400
    target_relay = next((rid for rid, r in relays.items() if r.get("state") == "online"), None)
    if not target_relay:
        return jsonify({"error": "no relay online"}), 503
    command = Command(user_id, target_relay, action, symbol, size)
    store.enqueue(command)
    return jsonify({
        "status": "queued",
        "command_id": command.id,
        "relay_id": target_relay,
    }), 202


# ==================== Terms & Privacy (Section 10) ====================

@app.route("/terms", methods=["GET"])
def terms_page():
    return render_template("terms.html")


@app.route("/privacy", methods=["GET"])
def privacy_page():
    return render_template("privacy.html")


@app.errorhandler(404)
def not_found(e):
    return jsonify({"error": "endpoint not found"}), 404

@app.errorhandler(429)
def rate_limited(e):
    return jsonify({"error": "too many requests — slow down", "retry_after": getattr(e, "retry_after", None)}), 429

@app.errorhandler(500)
def server_error(e):
    logger.error(f"Server error: {e}")
    return jsonify({"error": "internal server error"}), 500

# ==================== Main ====================

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Cloud Bridge Service")
    parser.add_argument("--port", type=int, default=int(os.getenv("CLOUD_BRIDGE_PORT", "80")))
    parser.add_argument("--host", default=os.getenv("CLOUD_BRIDGE_HOST", "0.0.0.0"))
    parser.add_argument("--workers", type=int, default=1, help="Number of workers (requires gunicorn)")
    args = parser.parse_args()

    port = args.port
    host = args.host

    # FIX 1: Also register SIGINT for clean Ctrl-C in dev
    try:
        _signal.signal(_signal.SIGINT, _graceful_shutdown)
    except (OSError, ValueError):
        pass

    # Re-validate at startup
    validate_startup_config()

    logger.info(f"Starting Cloud Bridge on {host}:{port}")

    if DEV_MODE:
        logger.info("Running in DEVELOPMENT mode (Flask debug server)")
        app.run(host=host, port=port, debug=True, threaded=True)
    else:
        logger.info("Running in PRODUCTION mode (waitress)")
        from waitress import serve
        serve(app, host=host, port=port, threads=8)
