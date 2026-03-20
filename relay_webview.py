#!/usr/bin/env python3
"""
PlatAlgo Relay — React UI served via Flask
Serves the React build on localhost and opens it in the user's browser.
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

from flask import Flask, jsonify, request, send_from_directory
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
SCRIPT_DIR = Path(__file__).resolve().parent
DIST_DIR = SCRIPT_DIR / "relay-ui" / "dist"
LAST_USER_FILE = SCRIPT_DIR / "relay_last_user.json"

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
def fallback(_e):
    """SPA fallback — serve index.html for client-side routing."""
    return send_from_directory(str(DIST_DIR), "index.html")


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
        if platform.system() == "Windows":
            import subprocess
            exe = sys.executable
            subprocess.run([
                "schtasks", "/create", "/tn", "PlatAlgoRelay",
                "/tr", f'"{exe}" "{__file__}"',
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
  <array><string>{sys.executable}</string><string>{__file__}</string></array>
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
    import webbrowser

    if not DIST_DIR.exists() or not (DIST_DIR / "index.html").exists():
        logger.error(f"React build not found at {DIST_DIR}. Run 'npm run build' in relay-ui/")
        sys.exit(1)

    url = f"http://127.0.0.1:{LOCAL_PORT}"
    logger.info(f"PlatAlgo Relay starting on {url}")

    # Open browser after a short delay (let Flask start first)
    def open_browser():
        time.sleep(1)
        webbrowser.open(url)

    threading.Thread(target=open_browser, daemon=True).start()

    app.run(host="127.0.0.1", port=LOCAL_PORT, debug=False, use_reloader=False)


if __name__ == "__main__":
    main()
