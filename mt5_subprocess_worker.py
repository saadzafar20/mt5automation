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
import shutil
import sys
import threading
import time

RECONNECT_DELAY = 5   # seconds between reconnect attempts
KEEPALIVE_INTERVAL = 20  # seconds between keep-alive pings


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
    Create a hard link to terminal64.exe inside the user's data dir.
    Hard links share disk blocks so this costs ~0 extra space.
    Falls back to a full copy if hard links aren't supported (e.g. cross-volume).
    Returns the path to the user-local exe.
    """
    os.makedirs(data_dir, exist_ok=True)
    user_exe = os.path.join(data_dir, "terminal64.exe")
    if not os.path.exists(user_exe):
        shutil.copy2(base_exe, user_exe)
        _log(f"Copied terminal64.exe → {user_exe}")
    return user_exe


def _write_autotrading_config(data_dir: str):
    """
    Pre-write config/common.ini with ExpertAdvisorsEnabled=1.
    MT5 reads this on startup — doing it before mt5.initialize() means
    AutoTrading is enabled from the very first connection, no patching needed.
    MT5 expects UTF-16 LE with BOM.
    """
    config_dir = os.path.join(data_dir, "config")
    os.makedirs(config_dir, exist_ok=True)
    config_path = os.path.join(config_dir, "common.ini")
    content = "[Common]\nExpertAdvisorsEnabled=1\n"
    with open(config_path, "w", encoding="utf-16") as f:
        f.write(content)
    _log(f"Wrote autotrading config → {config_path}")


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

    # ── Connect + command loop ────────────────────────────────────────────────
    _autotrading_patched = False  # patch once on first successful connect
    while True:
        # Pre-write autotrading config before every connect attempt
        try:
            _write_autotrading_config(data_dir)
        except Exception as e:
            _log(f"[{user_id}] Warning: could not write autotrading config: {e}")

        # Shut down any stale session cleanly
        try:
            if mt5.terminal_info() is not None:
                mt5.shutdown()
        except Exception:
            pass

        init_kwargs: dict = {
            "login":    login,
            "password": password,
            "server":   server,
            "portable": True,   # store data inside terminal_exe's directory
        }
        if terminal_exe and os.path.exists(terminal_exe):
            init_kwargs["path"] = terminal_exe

        ok = mt5.initialize(**init_kwargs)
        if not ok:
            err = mt5.last_error()
            _log(f"[{user_id}] MT5 init failed: {err} — retrying in {RECONNECT_DELAY}s")
            _send({"status": "connecting", "error": str(err)})
            time.sleep(RECONNECT_DELAY)
            continue

        # ── Ensure AutoTrading is enabled ─────────────────────────────────────
        # Write config at actual data_path. On first connect the terminal may
        # already be running with AutoTrading disabled (started by a prior
        # session). We patch the config then kill+reinit once so the fresh
        # terminal starts with AutoTrading on.
        if not _autotrading_patched:
            term = mt5.terminal_info()
            actual_data_path = getattr(term, "data_path", None) if term else None
            if actual_data_path:
                try:
                    config_path = os.path.join(actual_data_path, "config", "common.ini")
                    os.makedirs(os.path.dirname(config_path), exist_ok=True)
                    with open(config_path, "w", encoding="utf-16") as f:
                        f.write("[Common]\nExpertAdvisorsEnabled=1\n")
                    _autotrading_patched = True
                    _log(f"[{user_id}] Patched {config_path} — restarting terminal")

                    # Get terminal PID before disconnecting so we can kill it reliably
                    term_pid = getattr(term, "community_connection", None)  # not a real pid
                    mt5.shutdown()

                    # Kill by path (most reliable for per-user terminal copies)
                    killed = False
                    if terminal_exe and os.path.exists(terminal_exe):
                        import subprocess as sp
                        r = sp.run(
                            ['powershell', '-Command',
                             f'Get-Process | Where-Object {{ $_.Path -eq "{terminal_exe}" }} | Stop-Process -Force; $true'],
                            capture_output=True, text=True, timeout=15,
                        )
                        killed = True
                        _log(f"[{user_id}] Kill result: {r.stdout.strip() or r.stderr.strip() or 'ok'}")

                    # Wait longer to ensure process fully exits before re-init
                    time.sleep(8)
                    ok = mt5.initialize(**init_kwargs)
                    if not ok:
                        _log(f"[{user_id}] Re-init after patch failed: {mt5.last_error()} — retrying")
                        time.sleep(RECONNECT_DELAY)
                        continue
                except Exception as e:
                    _log(f"[{user_id}] Could not patch common.ini: {e}")
                    _autotrading_patched = True  # don't retry on failure

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

        # ── Populate full market watch ─────────────────────────────────────────
        # Add every symbol available on the broker to the market watch so that
        # symbol_info_tick() works for any symbol without manual market watch setup.
        try:
            all_syms = mt5.symbols_get()
            if all_syms:
                for _sym in all_syms:
                    mt5.symbol_select(_sym.name, True)
                _log(f"[{user_id}] Market watch populated: {len(all_syms)} symbols")
        except Exception as _e:
            _log(f"[{user_id}] Could not populate market watch: {_e}")

        # ── Keep-alive thread ─────────────────────────────────────────────────
        # MT5 connections time out when idle. Ping account_info() periodically
        # to keep the broker connection alive between trades.
        _stop_keepalive = threading.Event()

        def _keepalive():
            while not _stop_keepalive.wait(KEEPALIVE_INTERVAL):
                try:
                    mt5.account_info()
                except Exception:
                    pass

        ka_thread = threading.Thread(target=_keepalive, daemon=True)
        ka_thread.start()

        # ── Command loop (one command → one response) ─────────────────────────
        lost_connection = False
        for raw_line in sys.stdin:
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

            # Execute trade command — try even if connectivity check is uncertain;
            # mt5_order_utils will return a proper error if the connection is gone.
            try:
                from mt5_order_utils import execute_command
                result = execute_command(mt5, cmd, comment_prefix="managed-vps")
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
