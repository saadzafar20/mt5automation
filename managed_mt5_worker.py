"""Persistent per-user MT5 session for managed VPS execution.

Each managed user gets one dedicated thread. MT5 is initialized immediately
when the session starts (not on first trade), so every trade is instant.

The thread stays alive permanently, reconnecting automatically if the
broker connection drops.
"""

import logging
import os
import queue
import threading
import time
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

TRADE_TIMEOUT_SECS = 20
RECONNECT_DELAY_SECS = 5
HEALTH_CHECK_INTERVAL_SECS = 5


class MT5UserSession:
    """
    One per managed user. Owns a single dedicated thread that:
      1. Calls mt5.initialize() immediately at startup (warm, ready to trade)
      2. Loops waiting for trade commands on an internal queue
      3. Reconnects automatically if the broker connection drops
    """

    def __init__(self, user_id: str, login: int, password: str,
                 server: str, path: Optional[str] = None):
        self.user_id  = user_id
        self._login   = int(login)
        self._password = str(password)
        self._server  = str(server) if server else None
        self._path    = path or None

        self._queue     = queue.Queue()
        self._connected = False
        self._stopped   = False

        self._thread = threading.Thread(
            target=self._run,
            name=f"mt5-session-{user_id}",
            daemon=True,
        )
        self._thread.start()

    # ── Public API ────────────────────────────────────────────────────────────

    def execute(self, command: Dict[str, Any]) -> Dict[str, Any]:
        """Queue a trade command and block until the session thread executes it."""
        if self._stopped:
            return {"status": "failed", "error": "session is shut down"}

        result_box: list = []
        done = threading.Event()
        self._queue.put((command, result_box, done))

        if done.wait(timeout=TRADE_TIMEOUT_SECS):
            return result_box[0]
        return {"status": "failed", "error": "trade execution timed out"}

    @property
    def connected(self) -> bool:
        return self._connected

    def update_credentials(self, login: int, password: str,
                           server: str, path: Optional[str] = None):
        """Restart the session with new credentials (called after cred update)."""
        self._login    = int(login)
        self._password = str(password)
        self._server   = str(server) if server else None
        self._path     = path or None
        # Signal the thread to reconnect by poisoning the queue
        self._queue.put(("_reconnect", None, None))

    def shutdown(self):
        """Cleanly stop the session thread."""
        self._stopped = True
        self._queue.put(("_stop", None, None))

    # ── Thread body ───────────────────────────────────────────────────────────

    def _run(self):
        try:
            import MetaTrader5 as mt5
        except ImportError:
            logger.error(f"[{self.user_id}] MetaTrader5 not available — session cannot start")
            return

        while not self._stopped:
            # ── Connect ───────────────────────────────────────────────────────
            self._connected = self._connect(mt5)
            if not self._connected:
                time.sleep(RECONNECT_DELAY_SECS)
                continue

            # ── Trade loop ────────────────────────────────────────────────────
            while not self._stopped:
                try:
                    item = self._queue.get(timeout=HEALTH_CHECK_INTERVAL_SECS)
                except queue.Empty:
                    # Periodic health check — ensure broker connection is alive
                    if not self._is_alive(mt5):
                        logger.warning(f"[{self.user_id}] MT5 broker connection lost, reconnecting")
                        self._connected = False
                        break
                    continue

                cmd, result_box, done = item

                # Control signals
                if cmd == "_stop":
                    self._connected = False
                    return
                if cmd == "_reconnect":
                    self._connected = False
                    try:
                        mt5.shutdown()
                    except Exception:
                        pass
                    break

                # Execute trade
                try:
                    from mt5_order_utils import execute_command
                    result = execute_command(mt5, cmd, comment_prefix="managed-vps")
                except Exception as exc:
                    logger.exception(f"[{self.user_id}] Trade execution error")
                    result = {"status": "failed", "error": str(exc)}
                    self._connected = False

                result_box.append(result)
                done.set()

                if not self._connected:
                    break

        try:
            import MetaTrader5 as mt5
            mt5.shutdown()
        except Exception:
            pass

    def _connect(self, mt5) -> bool:
        """Initialize MT5 with user credentials. Returns True on success."""
        try:
            # Shut down any stale session cleanly first
            try:
                if mt5.terminal_info() is not None:
                    mt5.shutdown()
            except Exception:
                pass

            # Auto-detect MT5 path on the VPS if not explicitly provided
            path = self._path
            if not path:
                for candidate in [
                    r"C:\Program Files\MetaTrader 5\terminal64.exe",
                    r"C:\Program Files (x86)\MetaTrader 5\terminal64.exe",
                ]:
                    if os.path.exists(candidate):
                        path = candidate
                        break

            init_kwargs: dict = {
                "login":    self._login,
                "password": self._password,
                "server":   self._server,
            }
            if path:                # omit path entirely when not found
                init_kwargs["path"] = path
            ok = mt5.initialize(**init_kwargs)
            if ok:
                ok = self._ensure_autotrading(mt5, init_kwargs)
            if ok:
                info = mt5.account_info()
                name = f"account {info.login} on {info.server}" if info else "unknown account"
                logger.info(f"[{self.user_id}] MT5 connected: {name}")
            else:
                logger.warning(f"[{self.user_id}] MT5 init failed: {mt5.last_error()}")
            return ok
        except Exception as exc:
            logger.error(f"[{self.user_id}] MT5 connect error: {exc}")
            return False

    def _ensure_autotrading(self, mt5, init_kwargs: dict) -> bool:
        """If AutoTrading is disabled in the terminal config, patch it and reinitialize."""
        import configparser

        term = mt5.terminal_info()
        if term is None:
            return False
        if getattr(term, "trade_allowed", True):
            return True  # already enabled, nothing to do

        # Locate and patch common.ini to enable expert advisors
        data_path = getattr(term, "data_path", None)
        if not data_path:
            logger.warning(f"[{self.user_id}] AutoTrading disabled but cannot find terminal data_path")
            return True  # still connected, attempt trade anyway

        config_path = os.path.join(data_path, "config", "common.ini")
        try:
            cfg = configparser.ConfigParser()
            cfg.read(config_path)
            section = "Common" if cfg.has_section("Common") else None
            if section is None:
                cfg.add_section("Common")
                section = "Common"
            cfg.set(section, "ExpertAdvisorsEnabled", "1")
            with open(config_path, "w") as f:
                cfg.write(f)
            logger.info(f"[{self.user_id}] Patched {config_path} — ExpertAdvisorsEnabled=1; restarting terminal")
        except Exception as exc:
            logger.warning(f"[{self.user_id}] Could not patch common.ini: {exc}")
            return True  # proceed anyway

        # Restart the terminal so the new config takes effect
        try:
            mt5.shutdown()
        except Exception:
            pass
        time.sleep(2)
        ok = mt5.initialize(**init_kwargs)
        if ok:
            term = mt5.terminal_info()
            allowed = getattr(term, "trade_allowed", False)
            logger.info(f"[{self.user_id}] After config patch — trade_allowed={allowed}")
        return ok

    def _is_alive(self, mt5) -> bool:
        """Return True if MT5 terminal is still connected to the broker."""
        try:
            term = mt5.terminal_info()
            return term is not None and bool(getattr(term, "connected", False))
        except Exception:
            return False


class SessionManager:
    """
    Maintains one MT5UserSession per managed user.
    Sessions are started immediately when credentials are registered,
    and restored from the database when the bridge restarts.
    """

    def __init__(self):
        self._sessions: Dict[str, MT5UserSession] = {}
        self._lock = threading.Lock()

    def start_session(self, user_id: str, login: int, password: str,
                      server: str, path: Optional[str] = None):
        """Create (or replace) the persistent MT5 session for a user."""
        with self._lock:
            existing = self._sessions.get(user_id)
            if existing:
                existing.shutdown()
            session = MT5UserSession(user_id, login, password, server, path)
            self._sessions[user_id] = session
        logger.info(f"MT5 session initializing for user {user_id} (account {login})")

    def stop_session(self, user_id: str):
        """Shut down and remove a user's session."""
        with self._lock:
            session = self._sessions.pop(user_id, None)
        if session:
            session.shutdown()
            logger.info(f"MT5 session stopped for user {user_id}")

    def execute(self, user_id: str, command: Dict[str, Any]) -> Dict[str, Any]:
        """Dispatch a trade to the user's persistent MT5 session."""
        with self._lock:
            session = self._sessions.get(user_id)
        if not session:
            return {"status": "failed", "error": "no active MT5 session for this user"}
        return session.execute(command)

    def session_status(self, user_id: str) -> Dict[str, Any]:
        with self._lock:
            session = self._sessions.get(user_id)
        if not session:
            return {"active": False, "connected": False}
        return {"active": True, "connected": session.connected}

    def load_from_store(self, store, decrypt_fn):
        """
        At bridge startup: restore sessions for all enabled managed accounts.
        `store`      — BridgeStore instance
        `decrypt_fn` — callable that decrypts a stored password
        """
        accounts = store.get_all_managed_accounts()
        started = 0
        for acct in accounts:
            try:
                password = decrypt_fn(acct["mt5_password_enc"])
                self.start_session(
                    acct["user_id"],
                    int(acct["mt5_login"]),
                    password,
                    acct["mt5_server"],
                    acct.get("mt5_path") or None,
                )
                started += 1
            except Exception as exc:
                logger.error(f"Failed to restore MT5 session for {acct['user_id']}: {exc}")
        logger.info(f"Restored {started}/{len(accounts)} managed MT5 sessions from database")
