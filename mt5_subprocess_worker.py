"""
Per-user MT5 subprocess worker.

Launched by MT5UserSession as a dedicated subprocess. Because MetaTrader5
is a single-connection-per-process library, running each user in its own
subprocess gives full isolation — users never interfere with each other.

Each user also gets their own data directory (portable mode) so their
terminal config, logs, and credentials are fully separated.

Protocol: JSON lines on stdin/stdout.
  - Parent sends init params as first stdin line.
  - Worker replies {"status": "ready", "account": "..."} when connected.
  - Parent sends trade commands as JSON lines; worker replies with results.
  - Parent sends {"_action": "shutdown"} to stop cleanly.
  - stderr is used for diagnostic logging (captured by parent).
"""

import json
import os
import sys
import threading
import time

# Ensure stderr is UTF-8 so log messages with non-ASCII characters (e.g. em-dash)
# are correctly encoded.  The parent process opens the pipe with encoding="utf-8";
# without this, the subprocess defaults to the system ANSI codepage (cp1252 on
# most Windows installs) which causes UnicodeDecodeError in the parent's
# _drain_stderr thread, silently dropping all subsequent log lines.
if sys.platform == "win32" and hasattr(sys.stderr, "reconfigure"):
    try:
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

RECONNECT_DELAY = 5   # seconds between reconnect attempts
KEEPALIVE_INTERVAL = 30  # seconds between keep-alive pings


# ── Helpers ───────────────────────────────────────────────────────────────────


def _send(obj: dict):
    """Write one JSON line to stdout (read by parent process)."""
    print(json.dumps(obj), flush=True)


def _log(msg: str):
    """Write diagnostic message to stderr (captured by parent for logging)."""
    print(msg, file=sys.stderr, flush=True)


def _find_terminal() -> str | None:
    """Locate the MT5 terminal64.exe on the system."""
    candidates = [
        r"C:\Program Files\MetaTrader 5\terminal64.exe",
        r"C:\Program Files (x86)\MetaTrader 5\terminal64.exe",
    ]
    for p in candidates:
        if os.path.exists(p):
            return p
    return None


def _setup_user_terminal(data_dir: str, base_exe: str) -> str:
    """
    Return the base terminal exe path directly.
    Using the main installation avoids IPC instability from copied/portable
    terminal processes crashing on MetaQuotes-Demo.
    """
    os.makedirs(data_dir, exist_ok=True)
    return base_exe


def _write_autotrading_config(data_dir: str):
    """
    Ensure config/common.ini has ExpertAdvisorsEnabled=1 so MT5 starts with
    AutoTrading enabled.

    Strategy:
    1. Try to read the existing file as UTF-16 (standard MT5 format).
    2. If readable and well-formed, patch ExpertAdvisorsEnabled in-place.
    3. If unreadable / corrupted (file is huge or encoding fails), write a
       minimal clean file — never persist corrupted data back to disk.

    Only writes when MT5 is NOT running (no file lock).  If a write error
    occurs we log and skip — a missing setting is not fatal (the /expert
    command-line flag and the Win32 toggle handle AutoTrading at runtime).
    """
    config_dir = os.path.join(data_dir, "config")
    os.makedirs(config_dir, exist_ok=True)
    config_path = os.path.join(config_dir, "common.ini")

    MAX_SIZE = 64 * 1024  # 64 KB — anything larger is corrupt

    try:
        file_size = os.path.getsize(config_path) if os.path.exists(config_path) else 0
    except OSError:
        file_size = 0

    lines = []
    if 0 < file_size <= MAX_SIZE:
        for enc in ("utf-16", "utf-16-le"):
            try:
                with open(config_path, "r", encoding=enc) as f:
                    raw = f.read()
                lines = raw.splitlines()
                break
            except (UnicodeDecodeError, UnicodeError, OSError):
                lines = []

    if not lines:
        # File missing, empty, or corrupt — start from a minimal clean config.
        if file_size > MAX_SIZE:
            _log(f"common.ini is {file_size} bytes (corrupt) — resetting to clean file")
        lines = ["[Common]", "ExpertAdvisorsEnabled=1"]
        content = "\r\n".join(lines) + "\r\n"
        with open(config_path, "w", encoding="utf-16") as f:
            f.write(content)
        _log(f"Wrote clean autotrading config → {config_path}")
        return

    # Patch or add ExpertAdvisorsEnabled under [Common].
    found = False
    for i, line in enumerate(lines):
        if line.strip().lower().startswith("expertadvisorsenabled"):
            lines[i] = "ExpertAdvisorsEnabled=1"
            found = True
            break
    if not found:
        if not any(l.strip() == "[Common]" for l in lines):
            lines.insert(0, "[Common]")
        lines.append("ExpertAdvisorsEnabled=1")

    content = "\r\n".join(lines) + "\r\n"
    with open(config_path, "w", encoding="utf-16") as f:
        f.write(content)
    _log(f"Patched autotrading config → {config_path}")


def _kill_user_terminal(user_exe: str):
    """
    Kill the terminal64.exe process running from this user's specific exe path.
    Uses PowerShell to match by full path so we never kill another user's terminal
    or the system-wide installation.
    """
    try:
        import subprocess as _sp
        exe_escaped = user_exe.replace("'", "''")
        _sp.run(
            [
                "powershell", "-NonInteractive", "-Command",
                f"Get-Process -Name terminal64 -ErrorAction SilentlyContinue "
                f"| Where-Object {{$_.Path -eq '{exe_escaped}'}} "
                f"| Stop-Process -Force",
            ],
            capture_output=True,
            timeout=10,
        )
        _log(f"Killed terminal process at {user_exe}")
    except Exception as e:
        _log(f"Warning: could not kill terminal process: {e}")


def _start_user_terminal(user_exe: str, data_dir: str):
    """
    Start the terminal in portable mode with /expert flag (enables AutoTrading).
    MT5's /expert command-line switch turns on the AutoTrading button at startup,
    bypassing the common.ini ExpertAdvisorsEnabled setting that is unreliable in
    Session 0 / headless mode.

    We start the terminal here then let mt5.initialize() attach to the running
    process — passing the same path to initialize() connects without re-launching.
    """
    try:
        import subprocess as _sp
        _sp.Popen(
            [user_exe, "/portable", "/expert"],
            cwd=data_dir,
            creationflags=getattr(_sp, "DETACHED_PROCESS", 0x00000008),
        )
        _log(f"Started terminal with /expert: {user_exe}")
        time.sleep(8)  # allow MT5 to fully start and read config before init
    except Exception as e:
        _log(f"Warning: could not start terminal with /expert: {e}")


# Common symbols to pre-select after connection so they are immediately tradable.
# Kept to widely-used instruments to avoid overwhelming the broker's symbol feed.
_COMMON_SYMBOLS = [
    # Forex majors
    "EURUSD", "GBPUSD", "USDJPY", "USDCHF", "USDCAD", "AUDUSD", "NZDUSD",
    # Forex minors
    "EURGBP", "EURJPY", "EURCHF", "EURCAD", "EURAUD", "EURNZD",
    "GBPJPY", "GBPCHF", "GBPCAD", "GBPAUD", "GBPNZD",
    "AUDJPY", "AUDCAD", "AUDCHF", "AUDNZD",
    "CADJPY", "CADCHF", "NZDJPY", "CHFJPY",
    # Metals
    "XAUUSD", "XAGUSD", "XPTUSD", "XPDUSD",
    # Indices (common CFD names)
    "US30", "US500", "US100", "UK100", "DE40", "FR40", "JP225", "AU200",
    "NAS100", "SPX500", "DJI30", "DAX40",
    # Energy
    "USOIL", "UKOIL", "NGAS",
    # Crypto
    "BTCUSD", "ETHUSD", "LTCUSD", "XRPUSD",
]


def _select_common_symbols(mt5, user_id: str):
    """
    Pre-subscribe to common symbols so they are immediately available for trading.
    Each symbol is selected individually; failures are silently skipped.
    """
    selected = 0
    for sym in _COMMON_SYMBOLS:
        try:
            if mt5.symbol_select(sym, True):
                selected += 1
        except Exception:
            pass
    _log(f"[{user_id}] Pre-selected {selected}/{len(_COMMON_SYMBOLS)} common symbols")


def _enable_autotrading_win32(mt5, user_id: str) -> bool:
    """
    Send WM_COMMAND 32851 to the MT5 main window to toggle AutoTrading ON.
    Works when this process runs in the same Windows session as the MT5 window
    (Session 1 via _spawn_in_session1 in managed_mt5_worker.py).

    Waits up to 30 s for the window to appear (MT5 may still be loading its UI
    when Python's named-pipe IPC is already up).  Returns True if trade_allowed
    became True, False otherwise.
    """
    try:
        import ctypes
        user32 = ctypes.windll.user32
        WM_COMMAND = 0x0111
        AUTOTRADING_CMD = 32851  # toolbar button command ID (from Toolbar_488 in terminal.ini)

        # Enumerate all top-level windows for diagnostics when FindWindow fails.
        def _list_windows():
            titles = []
            @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
            def _cb(h, _):
                buf = ctypes.create_unicode_buffer(256)
                user32.GetWindowTextW(h, buf, 256)
                if buf.value:
                    titles.append(buf.value)
                return True
            user32.EnumWindows(_cb, None)
            return titles

        # Wait up to 30 s for MT5 main window to appear.
        hwnd = None
        for attempt in range(6):
            hwnd = user32.FindWindowW("MetaQuotes::MetaTrader::5.00", None)
            if hwnd:
                break
            _log(f"[{user_id}] Waiting for MT5 window... (attempt {attempt + 1}/6)")
            time.sleep(5)

        if not hwnd:
            visible = _list_windows()
            _log(f"[{user_id}] MT5 main window not found after 30 s -- visible top-level windows: {visible[:20]}")
            return False

        # Re-check trade_allowed before toggling: MT5 may have enabled AutoTrading
        # itself (e.g. /expert flag) while we were waiting for the window.  Toggling
        # an already-ON button would turn it OFF.
        term = mt5.terminal_info()
        current_ta = getattr(term, "trade_allowed", False) if term else False
        if current_ta:
            _log(f"[{user_id}] trade_allowed became True while waiting -- skipping toggle")
            return True

        _log(f"[{user_id}] Found MT5 window hwnd={hwnd:#x}, sending AutoTrading toggle (cmd {AUTOTRADING_CMD})")
        user32.PostMessageW(hwnd, WM_COMMAND, AUTOTRADING_CMD, 0)
        time.sleep(1)

        term = mt5.terminal_info()
        trade_allowed = getattr(term, "trade_allowed", False) if term else False
        _log(f"[{user_id}] After AutoTrading toggle: trade_allowed={trade_allowed}")
        return trade_allowed
    except Exception as e:
        _log(f"[{user_id}] Win32 AutoTrading enable error: {e}")
        return False


def _is_connected(mt5) -> bool:
    try:
        # account_info() is more reliable than terminal_info().connected
        # in headless/portable mode — it actually round-trips to the broker.
        return mt5.account_info() is not None
    except Exception:
        return False


# ── Main worker loop ──────────────────────────────────────────────────────────


def main():
    try:
        import MetaTrader5 as mt5
    except ImportError:
        _send({"status": "error", "error": "MetaTrader5 package not installed"})
        return

    # ── Read init params ──────────────────────────────────────────────────────
    try:
        params = json.loads(sys.stdin.readline())
    except Exception as e:
        _send({"status": "error", "error": f"Bad init params: {e}"})
        return

    user_id  = params["user_id"]
    login    = int(params["login"])
    password = str(params["password"])
    server   = str(params["server"])
    data_dir = params["data_dir"]

    # ── Set up isolated terminal directory ────────────────────────────────────
    base_exe = params.get("path") or _find_terminal()
    if base_exe:
        terminal_exe = _setup_user_terminal(data_dir, base_exe)
    else:
        terminal_exe = None
        _log(f"[{user_id}] MT5 terminal not found — will try without path")

    # ── Add project dir to sys.path for mt5_order_utils ──────────────────────
    project_dir = os.path.dirname(os.path.abspath(__file__))
    if project_dir not in sys.path:
        sys.path.insert(0, project_dir)

    # ── Write autotrading config once before any terminal starts ─────────────
    # Only write once at startup (not every reconnect loop) to avoid corrupting
    # the file while MT5 has it locked or growing it on every retry.
    user_exe = os.path.join(data_dir, "terminal64.exe")
    has_user_exe = os.path.exists(user_exe)
    if has_user_exe:
        try:
            _write_autotrading_config(data_dir)
        except Exception as e:
            _log(f"[{user_id}] Warning: could not write autotrading config: {e}")

    # ── Connect + command loop ────────────────────────────────────────────────
    # How many loops we've waited for a terminal to appear without starting one
    # ourselves.  After NO_TERMINAL_FALLBACK_LOOPS attempts we start a fallback
    # terminal (may end up in Session 0 if the VPS has no interactive login).
    NO_TERMINAL_FALLBACK_LOOPS = 24  # 24 × RECONNECT_DELAY = ~2 min
    _no_terminal_wait_count = 0

    while True:
        user_exe = os.path.join(data_dir, "terminal64.exe")
        has_user_exe = os.path.exists(user_exe)

        # Shut down stale Python ↔ MT5 session (does NOT kill the terminal process).
        try:
            if mt5.terminal_info() is not None:
                mt5.shutdown()
        except Exception:
            pass

        # Build init kwargs — attach to a running terminal if one exists.
        init_kwargs: dict = {
            "login":    login,
            "password": password,
            "server":   server,
        }
        if has_user_exe:
            init_kwargs["path"]     = user_exe
            init_kwargs["portable"] = True

        ok = mt5.initialize(**init_kwargs)

        if ok:
            # Connected — check AutoTrading state but do NOT kill the terminal.
            # If it was started by the startup-folder bat in Session 1,
            # trade_allowed will be True here even though this process is in
            # Session 0 (named-pipe IPC is cross-session).
            term = mt5.terminal_info()
            trade_allowed = getattr(term, "trade_allowed", False) if term else False
            actual_data_path = getattr(term, "data_path", None) if term else None
            _log(f"[{user_id}] Connected: trade_allowed={trade_allowed}, data_path={actual_data_path}")
            _no_terminal_wait_count = 0
            if not trade_allowed:
                _log(f"[{user_id}] trade_allowed=False — attempting Win32 AutoTrading enable")
                if _enable_autotrading_win32(mt5, user_id):
                    _log(f"[{user_id}] AutoTrading enabled via Win32")
                else:
                    _log(f"[{user_id}] WARNING: trade_allowed=False and Win32 toggle failed — trades will fail retcode 10027")
        else:
            # No terminal running yet.  Wait for the startup-folder script to
            # launch one in the interactive session rather than starting our own
            # (which would land in Session 0 and keep AutoTrading disabled).
            err = mt5.last_error()
            _no_terminal_wait_count += 1
            if has_user_exe and _no_terminal_wait_count > NO_TERMINAL_FALLBACK_LOOPS:
                # Fallback: no terminal appeared after ~2 min.  Start one here.
                # This happens when there is no active interactive session on the
                # VPS (e.g. fresh install before auto-login is set up).
                _log(f"[{user_id}] No terminal after {_no_terminal_wait_count} waits — starting fallback terminal")
                _start_user_terminal(user_exe, data_dir)
                _no_terminal_wait_count = 0
                ok = mt5.initialize(**init_kwargs)
            else:
                _log(f"[{user_id}] No terminal yet (wait {_no_terminal_wait_count}/{NO_TERMINAL_FALLBACK_LOOPS}) — {err}")
                _send({"status": "connecting", "error": str(err)})
                time.sleep(RECONNECT_DELAY)
                continue

            if not ok:
                err = mt5.last_error()
                _log(f"[{user_id}] MT5 init failed: {err} — retrying in {RECONNECT_DELAY}s")
                _send({"status": "connecting", "error": str(err)})
                time.sleep(RECONNECT_DELAY)
                continue

        # ── Pre-select common symbols ─────────────────────────────────────────
        _select_common_symbols(mt5, user_id)

        # Wait for account_info to reflect the authenticated account (login != 0).
        # MT5 initialize() can return True before the account sync completes.
        info = None
        for _attempt in range(15):
            info = mt5.account_info()
            if info and info.login != 0:
                break
            time.sleep(1)
        if not info or info.login == 0:
            _log(f"[{user_id}] Account not authenticated after init (login=0) — retrying")
            mt5.shutdown()
            time.sleep(RECONNECT_DELAY)
            continue
        account_str = f"{info.login} on {info.server}"
        _log(f"[{user_id}] Connected: {account_str}")
        _send({"status": "ready", "account": account_str})

        # ── Keep-alive thread ─────────────────────────────────────────────────
        # MT5 connections time out when idle. Ping account_info() periodically
        # to keep the broker connection alive between trades.
        _stop_keepalive = threading.Event()
        _keepalive_lost = threading.Event()
        _keepalive_fail_count = [0]  # mutable container so inner fn can write it

        def _keepalive():
            while not _stop_keepalive.wait(KEEPALIVE_INTERVAL):
                try:
                    info = mt5.account_info()
                    if info and info.login != 0:
                        _keepalive_fail_count[0] = 0  # reset on success
                    else:
                        _keepalive_fail_count[0] += 1
                        _log(f"[{user_id}] Keepalive: account_info None/0 (fail {_keepalive_fail_count[0]})")
                        if _keepalive_fail_count[0] >= 3:
                            _log(f"[{user_id}] Keepalive: 3 consecutive failures — exiting for supervisor restart")
                            _keepalive_lost.set()
                            # Force-exit so the parent supervisor detects us gone and
                            # restarts with a fresh subprocess.  The terminal process
                            # stays alive; the new subprocess will re-attach to it.
                            os._exit(1)
                except Exception as _e:
                    _keepalive_fail_count[0] += 1
                    if _keepalive_fail_count[0] >= 3:
                        _keepalive_lost.set()
                        os._exit(1)

        ka_thread = threading.Thread(target=_keepalive, daemon=True)
        ka_thread.start()

        # ── Command loop (one command → one response) ─────────────────────────
        lost_connection = False
        for raw_line in sys.stdin:
            if _keepalive_lost.is_set():
                lost_connection = True
                break
            raw_line = raw_line.strip()
            if not raw_line:
                continue

            try:
                cmd = json.loads(raw_line)
            except Exception:
                _send({"status": "error", "error": "invalid JSON command"})
                continue

            action = cmd.get("_action")

            if action == "shutdown":
                _stop_keepalive.set()
                mt5.shutdown()
                return

            if action == "ping":
                alive = _is_connected(mt5)
                _send({"status": "pong", "connected": alive})
                if not alive:
                    lost_connection = True
                    break
                continue

            if action == "ACCOUNT_INFO":
                info = mt5.account_info()
                positions = mt5.positions_get()
                if info:
                    _send({
                        "status": "ok",
                        "login": info.login,
                        "server": info.server,
                        "balance": info.balance,
                        "equity": info.equity,
                        "margin": info.margin,
                        "margin_level": info.margin_level if info.margin > 0 else 0.0,
                        "currency": info.currency,
                        "open_positions_count": len(positions) if positions else 0,
                    })
                else:
                    _send({"status": "error", "error": "account_info() returned None"})
                continue

            # Execute trade command — try even if connectivity check is uncertain;
            # mt5_order_utils will return a proper error if the connection is gone.
            _log(f"[{user_id}] Executing cmd: {cmd.get('action')} {cmd.get('symbol')}")
            try:
                from mt5_order_utils import execute_command
                # Quick connectivity sanity check before execution
                _info = mt5.account_info()
                _log(f"[{user_id}] Pre-exec account: {_info.login if _info else 'None'}, last_err={mt5.last_error()}")
                result = execute_command(mt5, cmd, comment_prefix="managed-vps")
                _log(f"[{user_id}] Exec result: {result}")
            except Exception as e:
                _log(f"[{user_id}] Trade execution exception: {e}")
                result = {"status": "failed", "error": str(e)}
                lost_connection = True

            _send(result)

            if lost_connection:
                break

        _stop_keepalive.set()

        if lost_connection:
            _log(f"[{user_id}] Connection lost — reconnecting in {RECONNECT_DELAY}s")
            try:
                mt5.shutdown()
            except Exception:
                pass
            time.sleep(RECONNECT_DELAY)
            continue

        # stdin closed — clean exit
        break

    try:
        mt5.shutdown()
    except Exception:
        pass


if __name__ == "__main__":
    main()
