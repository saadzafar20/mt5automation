"""Persistent per-user MT5 session for managed VPS execution.

Each managed user gets one dedicated subprocess running mt5_subprocess_worker.py.
Because MetaTrader5 is a single-connection-per-process library, running each user
in a subprocess gives complete isolation — sessions never interfere with each other.

Each subprocess uses portable mode with a user-specific data directory, so config,
logs, and terminal state are fully separated. AutoTrading is pre-enabled by writing
config/common.ini before every connection attempt.

Communication with the subprocess uses JSON lines on stdin/stdout.
"""

import json
import logging
import os
import subprocess
import sys
import threading
import time
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

TRADE_TIMEOUT_SECS      = 20
RECONNECT_DELAY_SECS    = 5
HEALTH_CHECK_INTERVAL_SECS = 10
MT5_USERS_BASE_DIR      = r"C:\mt5_users"


class MT5UserSession:
    """
    One per managed user. Owns a dedicated subprocess (mt5_subprocess_worker.py)
    that manages one MT5 terminal connection in an isolated data directory.

    The supervisor thread (_run) watches the subprocess and restarts it if it exits.
    All trade commands go through execute(), which serialises stdin/stdout I/O with
    a lock so concurrent callers never interleave messages.
    """

    def __init__(self, user_id: str, login: int, password: str,
                 server: str, path: Optional[str] = None):
        self.user_id   = user_id
        self._login    = int(login)
        self._password = str(password)
        self._server   = str(server)
        self._path     = path or None

        # Isolated data directory for this user's MT5 terminal
        self._data_dir = os.path.join(MT5_USERS_BASE_DIR, user_id)

        self._connected = False
        self._stopped   = False
        self._proc: Optional[subprocess.Popen] = None
        self._io_lock   = threading.Lock()  # serialise stdin write + stdout read

        self._thread = threading.Thread(
            target=self._run,
            name=f"mt5-session-{user_id}",
            daemon=True,
        )
        self._thread.start()

    # ── Public API ────────────────────────────────────────────────────────────

    def execute(self, command: Dict[str, Any]) -> Dict[str, Any]:
        """Send a trade command to the subprocess and return the result."""
        if self._stopped:
            return {"status": "failed", "error": "session is shut down"}
        if not self._connected or not self._proc or self._proc.poll() is not None:
            return {"status": "failed", "error": "MT5 session not connected"}

        with self._io_lock:
            try:
                self._proc.stdin.write(json.dumps(command) + "\n")
                self._proc.stdin.flush()
            except Exception as e:
                self._connected = False
                return {"status": "failed", "error": f"write error: {e}"}

            result = self._read_json_timeout(TRADE_TIMEOUT_SECS)

            # Subprocess reconnected internally and sent a stale "ready" message
            # before processing our command — read one more response.
            if result.get("status") == "ready":
                logger.info(f"[{self.user_id}] Consumed stale 'ready' — reading trade result")
                result = self._read_json_timeout(TRADE_TIMEOUT_SECS)

        # If we timed out the subprocess may be in an inconsistent state — kill it
        if result.get("_timed_out"):
            logger.warning(f"[{self.user_id}] Trade timed out — restarting subprocess")
            self._kill_subprocess()
            self._connected = False
            return {"status": "failed", "error": "trade execution timed out"}

        return result

    @property
    def connected(self) -> bool:
        return self._connected

    def update_credentials(self, login: int, password: str,
                           server: str, path: Optional[str] = None):
        """Restart the session with new credentials."""
        self._login    = int(login)
        self._password = str(password)
        self._server   = str(server)
        self._path     = path or None
        self._kill_subprocess()  # supervisor will restart with new creds

    def shutdown(self):
        """Cleanly stop this session."""
        self._stopped = True
        self._kill_subprocess()

    # ── Subprocess management ─────────────────────────────────────────────────

    def _start_subprocess(self) -> bool:
        """Spawn the worker subprocess, send init params, wait for ready."""
        worker = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "mt5_subprocess_worker.py",
        )
        try:
            proc = subprocess.Popen(
                [sys.executable, "-u", worker],   # -u = unbuffered stdout
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            self._proc = proc

            # Send init params
            init_params: dict = {
                "user_id":  self.user_id,
                "login":    self._login,
                "password": self._password,
                "server":   self._server,
                "data_dir": self._data_dir,
            }
            if self._path:
                init_params["path"] = self._path

            proc.stdin.write(json.dumps(init_params) + "\n")
            proc.stdin.flush()

            # Start a thread to drain stderr so the pipe never blocks
            threading.Thread(
                target=self._drain_stderr,
                args=(proc,),
                daemon=True,
            ).start()

            # Wait up to 120 s for MT5 to start and authenticate
            msg = self._read_json_timeout(120)
            if msg.get("status") == "ready":
                logger.info(f"[{self.user_id}] MT5 subprocess ready: {msg.get('account', '?')}")
                return True

            logger.error(f"[{self.user_id}] Subprocess did not become ready: {msg}")
            return False

        except Exception as e:
            logger.error(f"[{self.user_id}] Failed to start subprocess: {e}")
            return False

    def _kill_subprocess(self):
        """Ask the subprocess to stop gracefully; kill if it doesn't comply."""
        proc = self._proc
        if not proc or proc.poll() is not None:
            return
        try:
            proc.stdin.write(json.dumps({"_action": "shutdown"}) + "\n")
            proc.stdin.flush()
            proc.wait(timeout=5)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

    def _drain_stderr(self, proc: subprocess.Popen):
        """Read subprocess stderr and forward to logger (prevents pipe blocking)."""
        try:
            for line in proc.stderr:
                line = line.rstrip()
                if line:
                    logger.info(f"[{self.user_id}] worker: {line}")
        except Exception:
            pass

    def _read_json_timeout(self, timeout: float) -> dict:
        """
        Read one JSON line from subprocess stdout within `timeout` seconds.
        Returns {"_timed_out": True} if deadline exceeded.
        """
        result: list = [None]
        proc = self._proc

        def _read():
            try:
                result[0] = proc.stdout.readline()
            except Exception:
                pass

        t = threading.Thread(target=_read, daemon=True)
        t.start()
        t.join(timeout=timeout)

        line = result[0]
        if line is None:
            return {"_timed_out": True}
        if not line:
            self._connected = False
            return {"status": "failed", "error": "subprocess stdout closed"}
        try:
            return json.loads(line)
        except Exception:
            return {"status": "failed", "error": f"bad JSON from worker: {line[:120]}"}

    # ── Supervisor thread ─────────────────────────────────────────────────────

    def _run(self):
        """Supervisor: start subprocess, monitor it, restart if it exits."""
        while not self._stopped:
            self._connected = False

            if self._start_subprocess():
                self._connected = True
                logger.info(f"[{self.user_id}] MT5 session active (pid {self._proc.pid})")

                # Poll until subprocess exits or we are stopped
                while not self._stopped:
                    time.sleep(HEALTH_CHECK_INTERVAL_SECS)
                    if self._proc.poll() is not None:
                        logger.warning(
                            f"[{self.user_id}] Subprocess exited "
                            f"(code {self._proc.returncode}) — restarting"
                        )
                        self._connected = False
                        break
            else:
                try:
                    if self._proc:
                        self._proc.kill()
                except Exception:
                    pass
                time.sleep(RECONNECT_DELAY_SECS)

        logger.info(f"[{self.user_id}] Session supervisor stopped")


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
        for i, acct in enumerate(accounts):
            try:
                # Stagger startup by 15 s per user so MT5 terminals don't all
                # compete for initialization resources simultaneously.
                if i > 0:
                    time.sleep(15)
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
