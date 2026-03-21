#!/usr/bin/env python3
"""
PlatAlgo Relay — React UI served via Flask
Serves the React build on localhost and opens it as a native app window.
Also provides a local API for relay lifecycle management.
"""

import json
import logging
import os
import platform
import sys
import threading
import time
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory, Response
from flask_cors import CORS

# ── Logging ──────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("relay_webview")

# ── Constants ────────────────────────────────────────────────────────────────

BRIDGE_URL = os.getenv("BRIDGE_URL", "https://app.platalgo.com")
LOCAL_PORT = 5199

# Support PyInstaller bundled mode — search multiple paths for dist
if getattr(sys, "frozen", False):
    _meipass = Path(sys._MEIPASS)
    _exe_dir = Path(sys.executable).resolve().parent
    # Mac .app: exe is in Contents/MacOS, _MEIPASS may be Contents/Frameworks
    _mac_resources = _exe_dir.parent / "Resources"
    APP_DIR = _exe_dir
else:
    _meipass = Path(__file__).resolve().parent
    _exe_dir = _meipass
    _mac_resources = _meipass
    APP_DIR = _meipass

# Try every possible location PyInstaller might put the data
DIST_DIR = None
_candidates = [
    _meipass / "relay-ui" / "dist",
    _exe_dir / "relay-ui" / "dist",
    _exe_dir / "_internal" / "relay-ui" / "dist",
    _mac_resources / "relay-ui" / "dist",
    _meipass.parent / "Resources" / "relay-ui" / "dist",
    Path(__file__).resolve().parent / "relay-ui" / "dist" if not getattr(sys, "frozen", False) else _meipass,
]
for _c in _candidates:
    logger.info(f"  checking {_c} -> exists={_c.exists()}")
    if (_c / "index.html").is_file():
        DIST_DIR = _c
        break

if DIST_DIR is None:
    # Last resort: walk up from executable looking for it
    for _p in [_meipass, _exe_dir]:
        for _root, _dirs, _files in os.walk(_p):
            if "index.html" in _files and "assets" in _dirs:
                DIST_DIR = Path(_root)
                logger.info(f"  found dist via walk: {DIST_DIR}")
                break
        if DIST_DIR:
            break

if DIST_DIR is None:
    DIST_DIR = _meipass / "relay-ui" / "dist"  # fallback for error messages

LAST_USER_FILE = APP_DIR / "relay_last_user.json"

logger.info(f"DIST_DIR={DIST_DIR} exists={DIST_DIR.exists()}")
logger.info(f"frozen={getattr(sys, 'frozen', False)} _MEIPASS={getattr(sys, '_MEIPASS', 'N/A')}")
logger.info(f"exe={sys.executable}")

# ── Flask App ────────────────────────────────────────────────────────────────

app = Flask(__name__, static_folder=str(DIST_DIR), static_url_path="")
CORS(app)

# Shared state
_relay = None
_relay_thread = None
_state = {
    "status": "Idle",
    "bridge_online": False,
    "mt5_online": False,
    "broker_online": False,
    "vps_active": False,
}
_log_buffer: list[str] = []
_log_lock = threading.Lock()
_log_cursor = 0


def _add_log(line: str):
    global _log_buffer
    with _log_lock:
        _log_buffer.append(f"[{time.strftime('%H:%M:%S')}] {line}")
        if len(_log_buffer) > 500:
            _log_buffer = _log_buffer[-300:]


def _on_status(text: str):
    _state["status"] = text
    _add_log(text)


def _on_state(state_dict: dict):
    if "cloud_connected" in state_dict:
        _state["bridge_online"] = state_dict["cloud_connected"]
    if "mt5_connected" in state_dict:
        _state["mt5_online"] = state_dict["mt5_connected"]
    if "broker_connected" in state_dict:
        _state["broker_online"] = state_dict["broker_connected"]


# ── Serve React App ──────────────────────────────────────────────────────────

@app.route("/")
def serve_index():
    return send_from_directory(str(DIST_DIR), "index.html")


@app.errorhandler(404)
def spa_fallback(_e):
    """SPA fallback — serve index.html for client-side routing (not API routes)."""
    if request.path.startswith("/api/"):
        return jsonify({"error": "not found"}), 404
    return send_from_directory(str(DIST_DIR), "index.html")


@app.route("/api/debug")
def debug_info():
    """Debug endpoint to check dist directory."""
    files = []
    if DIST_DIR.exists():
        for f in DIST_DIR.rglob("*"):
            if f.is_file():
                files.append(str(f.relative_to(DIST_DIR)))
    return jsonify({
        "bundle_dir": str(BUNDLE_DIR),
        "dist_dir": str(DIST_DIR),
        "dist_exists": DIST_DIR.exists(),
        "index_exists": (DIST_DIR / "index.html").exists(),
        "files": files,
        "frozen": getattr(sys, "frozen", False),
    })


# ── Relay API ────────────────────────────────────────────────────────────────

@app.route("/api/relay/state")
def get_relay_state():
    global _log_cursor
    with _log_lock:
        new_logs = _log_buffer[_log_cursor:]
        _log_cursor = len(_log_buffer)
    return jsonify({**_state, "logs": new_logs})


@app.route("/api/relay/start", methods=["POST"])
def start_relay():
    global _relay, _relay_thread
    if _relay and _relay.running:
        return jsonify({"error": "Relay already running"}), 400

    data = request.get_json(force=True)
    user_id = data.get("user_id")
    password = data.get("password", "")
    api_key = data.get("api_key", "")
    relay_type = data.get("relay_type", "self-hosted")

    if not user_id:
        return jsonify({"error": "user_id required"}), 400

    try:
        from relay import Relay
        _relay = Relay(
            bridge_url=BRIDGE_URL,
            user_id=user_id,
            password=password,
            api_key=api_key,
            mt5_login=data.get("mt5_login"),
            mt5_password=data.get("mt5_password"),
            mt5_server=data.get("mt5_server"),
        )
        _relay.client.relay_type = relay_type

        def run():
            _relay.start(on_status=_on_status, on_state=_on_state)
            _state["status"] = "Idle"
            _state["bridge_online"] = False
            _state["mt5_online"] = False
            _state["broker_online"] = False
            _add_log("Relay stopped")

        _relay_thread = threading.Thread(target=run, daemon=True)
        _relay_thread.start()
        _add_log(f"Relay starting for {user_id}")
        return jsonify({"status": "started"})
    except Exception as e:
        logger.exception("Failed to start relay")
        return jsonify({"error": str(e)}), 500


@app.route("/api/relay/stop", methods=["POST"])
def stop_relay():
    global _relay
    if _relay and _relay.running:
        _relay.running = False
        _add_log("Relay stop requested")
        return jsonify({"status": "stopping"})
    return jsonify({"status": "not_running"})


@app.route("/api/relay/logs/clear", methods=["POST"])
def clear_logs():
    global _log_buffer, _log_cursor
    with _log_lock:
        _log_buffer = []
        _log_cursor = 0
    return jsonify({"status": "cleared"})


@app.route("/api/managed/enable", methods=["POST"])
def managed_enable():
    import requests as req
    data = request.get_json(force=True)
    user_id = data.get("user_id")
    headers = {"X-User-ID": user_id}
    if data.get("api_key"):
        headers["X-API-Key"] = data["api_key"]

    body = {
        "mt5_login": data.get("mt5_login", ""),
        "mt5_password": data.get("mt5_password", ""),
        "mt5_server": data.get("mt5_server", ""),
    }

    if data.get("password"):
        body["user_id"] = user_id
        body["password"] = data["password"]
        resp = req.post(f"{BRIDGE_URL}/managed/setup/login", json=body, timeout=15)
    else:
        resp = req.post(f"{BRIDGE_URL}/managed/setup", json=body, headers=headers, timeout=15)

    _state["vps_active"] = resp.status_code == 200
    _add_log(f"VPS enable: {resp.status_code}")
    return jsonify(resp.json()), resp.status_code


@app.route("/api/managed/disable", methods=["POST"])
def managed_disable():
    import requests as req
    data = request.get_json(force=True)
    user_id = data.get("user_id")
    resp = req.post(
        f"{BRIDGE_URL}/relay/managed/disable",
        json={"user_id": user_id},
        headers={"X-User-ID": user_id},
        timeout=15,
    )
    _state["vps_active"] = False
    _add_log("VPS disabled")
    return jsonify(resp.json()), resp.status_code


# ── Native Bridge API (called from React via fetch) ─────────────────────────

@app.route("/api/bridge/keyring/get", methods=["POST"])
def bridge_keyring_get():
    try:
        import keyring
        data = request.get_json(force=True)
        pw = keyring.get_password(data["service"], data["user_id"]) or ""
        return jsonify({"password": pw})
    except Exception:
        return jsonify({"password": ""})


@app.route("/api/bridge/keyring/set", methods=["POST"])
def bridge_keyring_set():
    try:
        import keyring
        data = request.get_json(force=True)
        keyring.set_password(data["service"], data["user_id"], data["password"])
        return jsonify({"ok": True})
    except Exception:
        return jsonify({"ok": False})


@app.route("/api/bridge/detect-mt5")
def bridge_detect_mt5():
    if platform.system() != "Windows":
        return jsonify({"path": ""})
    try:
        import winreg
        key = winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall",
        )
        for i in range(winreg.QueryInfoKey(key)[0]):
            try:
                subkey_name = winreg.EnumKey(key, i)
                subkey = winreg.OpenKey(key, subkey_name)
                name, _ = winreg.QueryValueEx(subkey, "DisplayName")
                if "MetaTrader 5" in str(name):
                    loc, _ = winreg.QueryValueEx(subkey, "InstallLocation")
                    terminal = Path(loc) / "terminal64.exe"
                    if terminal.exists():
                        return jsonify({"path": str(terminal)})
            except (FileNotFoundError, OSError):
                continue
    except Exception:
        pass
    return jsonify({"path": ""})


@app.route("/api/bridge/startup", methods=["GET", "POST", "DELETE"])
def bridge_startup():
    if request.method == "GET":
        if platform.system() == "Windows":
            import subprocess
            result = subprocess.run(
                ["schtasks", "/query", "/tn", "PlatAlgoRelay"],
                capture_output=True, text=True,
            )
            return jsonify({"enabled": result.returncode == 0})
        elif platform.system() == "Darwin":
            plist = Path.home() / "Library" / "LaunchAgents" / "com.platalgo.relay.plist"
            return jsonify({"enabled": plist.exists()})
        return jsonify({"enabled": False})

    elif request.method == "POST":
        exe = sys.executable
        if platform.system() == "Windows":
            import subprocess
            subprocess.run([
                "schtasks", "/create", "/tn", "PlatAlgoRelay",
                "/tr", f'"{exe}"',
                "/sc", "onlogon", "/rl", "highest", "/f",
            ], capture_output=True)
        elif platform.system() == "Darwin":
            plist_path = Path.home() / "Library" / "LaunchAgents" / "com.platalgo.relay.plist"
            plist_path.write_text(f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.platalgo.relay</string>
  <key>ProgramArguments</key>
  <array><string>{exe}</string></array>
  <key>RunAtLoad</key><true/>
</dict>
</plist>""")
        return jsonify({"ok": True})

    elif request.method == "DELETE":
        if platform.system() == "Windows":
            import subprocess
            subprocess.run(["schtasks", "/delete", "/tn", "PlatAlgoRelay", "/f"], capture_output=True)
        elif platform.system() == "Darwin":
            plist = Path.home() / "Library" / "LaunchAgents" / "com.platalgo.relay.plist"
            plist.unlink(missing_ok=True)
        return jsonify({"ok": True})

    return jsonify({"error": "invalid method"}), 405


@app.route("/api/bridge/open-external", methods=["POST"])
def bridge_open_external():
    """Open a URL in the system's default browser (needed for pywebview OAuth)."""
    import webbrowser
    data = request.get_json(force=True)
    url = data.get("url", "")
    if url and url.startswith("http"):
        webbrowser.open(url)
        return jsonify({"ok": True})
    return jsonify({"ok": False, "error": "invalid url"}), 400


@app.route("/api/bridge/last-user", methods=["GET", "POST"])
def bridge_last_user():
    if request.method == "GET":
        try:
            return jsonify(json.loads(LAST_USER_FILE.read_text(encoding="utf-8")))
        except Exception:
            return jsonify({})
    else:
        data = request.get_json(force=True)
        LAST_USER_FILE.write_text(json.dumps(data), encoding="utf-8")
        return jsonify({"ok": True})


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    if not DIST_DIR.exists() or not (DIST_DIR / "index.html").exists():
        # Show an error page instead of silently failing
        logger.error(f"React build not found at {DIST_DIR}")
        _serve_error_page()
        return

    url = f"http://127.0.0.1:{LOCAL_PORT}"
    logger.info(f"PlatAlgo Relay starting on {url}")
    logger.info(f"Serving from {DIST_DIR}")

    # Start Flask in background thread
    flask_thread = threading.Thread(
        target=lambda: app.run(host="127.0.0.1", port=LOCAL_PORT, debug=False, use_reloader=False),
        daemon=True,
    )
    flask_thread.start()

    # Wait for Flask to be ready
    import urllib.request
    for _ in range(50):
        try:
            urllib.request.urlopen(url, timeout=1)
            break
        except Exception:
            time.sleep(0.1)

    _open_window(url, flask_thread)


def _serve_error_page():
    """If dist isn't found, serve a simple error page."""
    @app.route("/", defaults={"path": ""})
    @app.route("/<path:path>")
    def error_page(path):
        return Response(
            f"<html><body style='font-family:sans-serif;padding:40px;background:#111;color:#eee'>"
            f"<h1>PlatAlgo Relay</h1>"
            f"<p>React build not found.</p>"
            f"<p>Expected at: <code>{DIST_DIR}</code></p>"
            f"<p>Bundle dir: <code>{BUNDLE_DIR}</code></p>"
            f"<p>Frozen: <code>{getattr(sys, 'frozen', False)}</code></p>"
            f"</body></html>",
            content_type="text/html",
        )

    url = f"http://127.0.0.1:{LOCAL_PORT}"
    flask_thread = threading.Thread(
        target=lambda: app.run(host="127.0.0.1", port=LOCAL_PORT, debug=False, use_reloader=False),
        daemon=True,
    )
    flask_thread.start()
    time.sleep(0.5)
    _open_window(url, flask_thread)


def _open_window(url: str, flask_thread: threading.Thread):
    """Open native window or browser fallback."""
    # Try pywebview first (native app window)
    try:
        import webview
        webview.create_window(
            "PlatAlgo Relay",
            url,
            width=1200,
            height=800,
            min_size=(900, 600),
        )
        webview.start()
        return
    except Exception as e:
        logger.warning(f"pywebview not available: {e}")

    # Fallback: Edge/Chrome in app mode
    if _open_app_mode(url):
        flask_thread.join()
        return

    # Last resort: default browser
    import webbrowser
    logger.warning("Opening in default browser")
    webbrowser.open(url)
    flask_thread.join()


def _open_app_mode(url: str) -> bool:
    """Launch Edge or Chrome in --app mode (frameless window, no browser chrome)."""
    import shutil
    import subprocess

    candidates = []
    if platform.system() == "Darwin":
        candidates = [
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
            "/Applications/Chromium.app/Contents/MacOS/Chromium",
        ]
    elif platform.system() == "Windows":
        candidates = [
            shutil.which("msedge"),
            r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
            shutil.which("chrome"),
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        ]

    for browser in candidates:
        if browser and Path(browser).exists():
            subprocess.Popen([browser, f"--app={url}"])
            return True
    return False


if __name__ == "__main__":
    main()
