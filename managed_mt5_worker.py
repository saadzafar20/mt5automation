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
import shutil
import subprocess
import sys
import threading
import time
from typing import Any, Dict, Optional

# ---------------------------------------------------------------------------
# Windows-only helper: spawn a process in the active interactive user session
# (Session 1) instead of the service's Session 0.  This is required so the
# mt5_subprocess_worker can connect to MetaTrader5 with trade_allowed=True,
# which the terminal only grants to processes in an interactive session.
# ---------------------------------------------------------------------------

def _spawn_in_session1(cmd: list, cwd: str):
    """
    Spawn *cmd* in the active Windows interactive session (Session 1) using
    WTSQueryUserToken + CreateProcessAsUser.  Returns a Popen-compatible
    object with .stdin, .stdout, .stderr, .pid, .poll(), .wait(), .kill()
    attributes, or None if no interactive session exists / the call fails.

    Only available on Windows; returns None immediately on other platforms.
    """
    if sys.platform != "win32":
        return None

    import ctypes
    import ctypes.wintypes as wt
    import msvcrt

    try:
        KERNEL32  = ctypes.windll.kernel32
        ADVAPI32  = ctypes.windll.advapi32
        WTSAPI32  = ctypes.windll.wtsapi32
        USERENV   = ctypes.windll.userenv

        # ── Structures ────────────────────────────────────────────────────
        class SECURITY_ATTRIBUTES(ctypes.Structure):
            _fields_ = [("nLength", wt.DWORD),
                         ("lpSecurityDescriptor", wt.LPVOID),
                         ("bInheritHandle", wt.BOOL)]

        class STARTUPINFOW(ctypes.Structure):
            _fields_ = [
                ("cb",              wt.DWORD),
                ("lpReserved",      wt.LPWSTR),
                ("lpDesktop",       wt.LPWSTR),
                ("lpTitle",         wt.LPWSTR),
                ("dwX",             wt.DWORD), ("dwY",            wt.DWORD),
                ("dwXSize",         wt.DWORD), ("dwYSize",        wt.DWORD),
                ("dwXCountChars",   wt.DWORD), ("dwYCountChars",  wt.DWORD),
                ("dwFillAttribute", wt.DWORD),
                ("dwFlags",         wt.DWORD),
                ("wShowWindow",     wt.WORD),
                ("cbReserved2",     wt.WORD),
                ("lpReserved2",     ctypes.c_char_p),
                ("hStdInput",       wt.HANDLE),
                ("hStdOutput",      wt.HANDLE),
                ("hStdError",       wt.HANDLE),
            ]

        class PROCESS_INFORMATION(ctypes.Structure):
            _fields_ = [("hProcess", wt.HANDLE), ("hThread", wt.HANDLE),
                         ("dwProcessId", wt.DWORD), ("dwThreadId", wt.DWORD)]

        STARTF_USESTDHANDLES  = 0x100
        STARTF_USESHOWWINDOW  = 0x001
        SW_HIDE               = 0
        CREATE_UNICODE_ENV    = 0x400
        HANDLE_FLAG_INHERIT   = 0x1
        TOKEN_ALL_ACCESS      = 0xF01FF
        SecurityImpersonation = 2
        TokenPrimary          = 1

        def _make_pipe():
            """Return (read_h, write_h) — both inheritable."""
            sa = SECURITY_ATTRIBUTES()
            sa.nLength = ctypes.sizeof(sa)
            sa.bInheritHandle = True
            r, w = wt.HANDLE(), wt.HANDLE()
            if not KERNEL32.CreatePipe(ctypes.byref(r), ctypes.byref(w),
                                        ctypes.byref(sa), 0):
                raise ctypes.WinError()
            return r, w

        # ── Get the active console session ────────────────────────────────
        session_id = KERNEL32.WTSGetActiveConsoleSessionId()
        if session_id == 0xFFFFFFFF:
            return None  # no interactive session

        # ── Obtain the user's primary token ───────────────────────────────
        hToken = wt.HANDLE()
        if not WTSAPI32.WTSQueryUserToken(session_id, ctypes.byref(hToken)):
            logger.debug("_spawn_in_session1: WTSQueryUserToken failed "
                         f"(err={ctypes.GetLastError()})")
            return None

        hDupToken = wt.HANDLE()
        if not ADVAPI32.DuplicateTokenEx(
                hToken, TOKEN_ALL_ACCESS, None,
                SecurityImpersonation, TokenPrimary,
                ctypes.byref(hDupToken)):
            KERNEL32.CloseHandle(hToken)
            return None
        KERNEL32.CloseHandle(hToken)

        # ── Build user environment block ──────────────────────────────────
        hEnv = ctypes.c_void_p()
        USERENV.CreateEnvironmentBlock(ctypes.byref(hEnv), hDupToken, False)

        # ── Create stdin/stdout/stderr pipes ──────────────────────────────
        stdin_r,  stdin_w  = _make_pipe()
        stdout_r, stdout_w = _make_pipe()
        stderr_r, stderr_w = _make_pipe()

        # Make the parent-side handles non-inheritable so only the
        # child-side ends are passed through.
        for h in (stdin_w, stdout_r, stderr_r):
            KERNEL32.SetHandleInformation(h, HANDLE_FLAG_INHERIT, 0)

        # ── STARTUPINFO ───────────────────────────────────────────────────
        si = STARTUPINFOW()
        si.cb         = ctypes.sizeof(si)
        si.lpDesktop  = "winsta0\\default"
        si.dwFlags    = STARTF_USESTDHANDLES | STARTF_USESHOWWINDOW
        si.wShowWindow = SW_HIDE
        si.hStdInput  = stdin_r
        si.hStdOutput = stdout_w
        si.hStdError  = stderr_w

        pi = PROCESS_INFORMATION()

        # Build command string (quote paths with spaces)
        import shlex
        cmd_str = subprocess.list2cmdline(cmd)

        ok = ADVAPI32.CreateProcessAsUserW(
            hDupToken,
            None,                    # lpApplicationName
            cmd_str,                 # lpCommandLine
            None, None,              # process / thread attributes
            True,                    # bInheritHandles
            CREATE_UNICODE_ENV,      # dwCreationFlags
            hEnv if hEnv.value else None,
            cwd,                     # lpCurrentDirectory
            ctypes.byref(si),
            ctypes.byref(pi),
        )

        # Close handles we no longer need
        KERNEL32.CloseHandle(hDupToken)
        KERNEL32.CloseHandle(stdin_r)
        KERNEL32.CloseHandle(stdout_w)
        KERNEL32.CloseHandle(stderr_w)
        if hEnv.value:
            USERENV.DestroyEnvironmentBlock(hEnv)

        if not ok:
            err = ctypes.GetLastError()
            logger.warning(f"_spawn_in_session1: CreateProcessAsUser failed (err={err})")
            KERNEL32.CloseHandle(stdin_w)
            KERNEL32.CloseHandle(stdout_r)
            KERNEL32.CloseHandle(stderr_r)
            return None

        # ── Wrap raw handles as Python file objects ───────────────────────
        stdin_fd  = msvcrt.open_osfhandle(stdin_w.value,  os.O_WRONLY | os.O_TEXT)
        stdout_fd = msvcrt.open_osfhandle(stdout_r.value, os.O_RDONLY | os.O_TEXT)
        stderr_fd = msvcrt.open_osfhandle(stderr_r.value, os.O_RDONLY | os.O_TEXT)

        stdin_file  = open(stdin_fd,  "w", encoding="utf-8", buffering=1)
        stdout_file = open(stdout_fd, "r", encoding="utf-8", buffering=1,
                           errors="replace")
        stderr_file = open(stderr_fd, "r", encoding="utf-8", buffering=1,
                           errors="replace")

        hProcess = pi.hProcess
        pid      = pi.dwProcessId
        KERNEL32.CloseHandle(pi.hThread)

        # ── Minimal Popen-compatible wrapper ──────────────────────────────
        class _ProcWrapper:
            def __init__(self):
                self.stdin  = stdin_file
                self.stdout = stdout_file
                self.stderr = stderr_file
                self.pid    = pid
                self._hProc = hProcess
                self._rc    = None

            def poll(self):
                if self._rc is not None:
                    return self._rc
                rc = wt.DWORD(259)  # STILL_ACTIVE
                KERNEL32.GetExitCodeProcess(self._hProc, ctypes.byref(rc))
                if rc.value != 259:
                    self._rc = rc.value
                    KERNEL32.CloseHandle(self._hProc)
                return self._rc

            def wait(self, timeout=None):
                ms = int(timeout * 1000) if timeout is not None else 0xFFFFFFFF
                KERNEL32.WaitForSingleObject(self._hProc, ms)
                return self.poll()

            def kill(self):
                try:
                    KERNEL32.TerminateProcess(self._hProc, 1)
                except Exception:
                    pass

            @property
            def returncode(self):
                return self.poll()

        logger.info(f"_spawn_in_session1: launched pid={pid} in session {session_id}")
        return _ProcWrapper()

    except Exception as exc:
        logger.warning(f"_spawn_in_session1: unexpected error: {exc}")
        return None

logger = logging.getLogger(__name__)

TRADE_TIMEOUT_SECS      = 20
RECONNECT_DELAY_SECS    = 5
HEALTH_CHECK_INTERVAL_SECS = 10
MT5_USERS_BASE_DIR      = r"C:\mt5_users"
MT5_TEMPLATE_DIR        = os.path.join(MT5_USERS_BASE_DIR, "_template")


def _provision_user_dir(user_id: str, data_dir: str):
    """
    Ensure the user's MT5 data directory exists with a full portable installation.

    On first call for a new user:
      1. If C:\\mt5_users\\_template\\ exists, copy it to data_dir (gives the user
         their own terminal64.exe + broker config in portable mode).
      2. If no template exists, just mkdir data_dir — the subprocess will fall
         back to the shared system MT5 installation.

    On subsequent calls the directory already exists so this returns immediately.
    """
    if os.path.exists(data_dir):
        return  # already provisioned

    if os.path.isdir(MT5_TEMPLATE_DIR):
        logger.info(f"[{user_id}] Provisioning MT5 from template → {data_dir}")
        try:
            shutil.copytree(MT5_TEMPLATE_DIR, data_dir)
            logger.info(f"[{user_id}] MT5 directory provisioned successfully")
            return
        except Exception as exc:
            logger.error(f"[{user_id}] copytree failed: {exc} — falling back to mkdir")
    else:
        logger.warning(
            f"[{user_id}] Template dir not found ({MT5_TEMPLATE_DIR}); "
            "using system MT5 installation — create the template for full isolation"
        )

    os.makedirs(data_dir, exist_ok=True)


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

        # FIX 4: Circuit breaker for subprocess restart backoff
        self._circuit_open = False
        self._consecutive_failures = 0

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

    def reset_circuit(self):
        """Reset the circuit breaker to allow subprocess restart attempts."""
        self._circuit_open = False
        self._consecutive_failures = 0
        logger.info(f"[{self.user_id}] Circuit breaker reset")

    # ── Subprocess management ─────────────────────────────────────────────────

    def _start_subprocess(self) -> bool:
        """Spawn the worker subprocess, send init params, wait for ready."""
        # Provision the user's MT5 directory from the template if needed.
        # Idempotent — fast no-op when directory already exists.
        _provision_user_dir(self.user_id, self._data_dir)

        worker = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "mt5_subprocess_worker.py",
        )
        worker_cmd = [sys.executable, "-u", worker]
        cwd = os.path.dirname(os.path.abspath(__file__))
        try:
            # Prefer Session 1 (interactive user session) so MT5 grants
            # trade_allowed=True.  Fall back to regular Popen if unavailable.
            proc = _spawn_in_session1(worker_cmd, cwd)
            if proc is None:
                logger.debug(f"[{self.user_id}] Session 1 spawn unavailable — using regular Popen")
                proc = subprocess.Popen(
                    worker_cmd,
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

            # Wait up to 120 s for MT5 to authenticate.
            # The subprocess may send intermediate "connecting" messages while
            # the terminal is starting — keep reading until "ready" or timeout.
            deadline = time.time() + 120
            while time.time() < deadline:
                remaining = max(1.0, deadline - time.time())
                msg = self._read_json_timeout(remaining)
                if msg.get("_timed_out"):
                    break
                if msg.get("status") == "ready":
                    logger.info(f"[{self.user_id}] MT5 subprocess ready: {msg.get('account', '?')}")
                    return True
                if msg.get("status") == "connecting":
                    logger.debug(f"[{self.user_id}] MT5 connecting: {msg.get('error', '')}")
                    continue  # keep waiting
                # Any other message (error, unexpected) — give up
                break

            logger.error(f"[{self.user_id}] Subprocess did not become ready within 120s: {msg}")
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
        """Supervisor: start subprocess, monitor it, restart if it exits.

        FIX 4: Exponential backoff (5s → 5min) and circuit breaker after 5 failures.
        FIX 16: Log connection state changes with timestamps.
        """
        while not self._stopped:
            # FIX 4: Circuit breaker — stop retrying when open
            if self._circuit_open:
                time.sleep(HEALTH_CHECK_INTERVAL_SECS)
                continue

            prev_connected = self._connected
            self._connected = False

            # FIX 16: log disconnection when state changes
            if prev_connected:
                logger.info(
                    f"[{self.user_id}] MT5 state changed: connected → disconnected "
                    f"at {time.strftime('%Y-%m-%dT%H:%M:%S', time.gmtime())}"
                )

            if self._start_subprocess():
                self._consecutive_failures = 0

                prev_state = self._connected
                self._connected = True

                # FIX 16: log connection when state changes
                if not prev_state:
                    logger.info(
                        f"[{self.user_id}] MT5 state changed: disconnected → connected "
                        f"at {time.strftime('%Y-%m-%dT%H:%M:%S', time.gmtime())}"
                    )

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

                self._consecutive_failures += 1
                # FIX 4: Exponential backoff, max 300s (5 min)
                delay = min(5 * (2 ** (self._consecutive_failures - 1)), 300)

                if self._consecutive_failures >= 5:
                    self._circuit_open = True
                    logger.error(
                        f"[{self.user_id}] Circuit breaker OPEN after "
                        f"{self._consecutive_failures} consecutive failures — "
                        "stopping restart attempts. Call reset_circuit() to retry."
                    )
                    continue

                logger.warning(
                    f"[{self.user_id}] Subprocess start failed "
                    f"(attempt {self._consecutive_failures}), retrying in {delay}s"
                )
                time.sleep(delay)

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

    def shutdown_all(self, timeout: float = 10.0):
        """Cleanly shut down all active MT5 sessions. Waits up to `timeout` seconds."""
        with self._lock:
            sessions = dict(self._sessions)
        logger.info(f"SessionManager.shutdown_all: stopping {len(sessions)} session(s)")
        for user_id, session in sessions.items():
            try:
                session.shutdown()
                logger.info(f"Session stopped for user {user_id}")
            except Exception as exc:
                logger.warning(f"Error stopping session for {user_id}: {exc}")
        # Give threads time to exit
        deadline = time.time() + timeout
        for user_id, session in sessions.items():
            remaining = deadline - time.time()
            if remaining > 0:
                session._thread.join(timeout=remaining)
        with self._lock:
            self._sessions.clear()
        logger.info("SessionManager.shutdown_all: complete")

    def start_session(self, user_id: str, login: int, password: str,
                      server: str, path: Optional[str] = None):
        """Create (or replace) the persistent MT5 session for a user.

        If the session already exists, is connected, and the credentials are
        unchanged, this is a no-op — avoids unnecessary restarts that can
        trigger broker-side rate-limiting on successive login attempts.
        """
        with self._lock:
            existing = self._sessions.get(user_id)
            if existing and existing.connected:
                same_creds = (
                    existing._login == int(login)
                    and existing._password == str(password)
                    and existing._server == str(server)
                )
                if same_creds:
                    logger.info(
                        f"[{user_id}] start_session: already connected with same "
                        "credentials — skipping restart"
                    )
                    return
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
            return {"active": False, "connected": False, "circuit_open": False}
        return {
            "active": True,
            "connected": session.connected,
            "circuit_open": session._circuit_open,
        }

    def reset_circuit(self, user_id: str):
        """Reset the circuit breaker for a user's session."""
        with self._lock:
            session = self._sessions.get(user_id)
        if session:
            session.reset_circuit()

    def get_all_sessions_status(self) -> Dict[str, Any]:
        """Return {user_id: {active, connected}} for all sessions."""
        with self._lock:
            snapshot = dict(self._sessions)
        return {uid: {"active": True, "connected": s.connected} for uid, s in snapshot.items()}

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
