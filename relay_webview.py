#!/usr/bin/env python3
"""
PlatAlgo Relay — pywebview + React frontend
Serves the React UI in a native window and provides a local Flask API
for relay lifecycle management + a JS bridge for native OS operations.
"""

import json
import logging
import os
import platform
import sys
import threading
import time
import uuid
from pathlib import Path

import webview
from flask import Flask, jsonify, request
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

# ── Flask API ────────────────────────────────────────────────────────────────

api = Flask(__name__)
CORS(api)

# Shared state
_relay = None          # Relay instance
_relay_thread = None   # Thread running relay.start()
_state = {
    "status": "Idle",
    "bridge_online": False,
    "mt5_online": False,
    "broker_online": False,
    "vps_active": False,
}
_log_buffer: list[str] = []
_log_lock = threading.Lock()
_log_cursor = 0  # tracks what the client has already received


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


@api.route("/api/relay/state")
def get_relay_state():
    global _log_cursor
    with _log_lock:
        new_logs = _log_buffer[_log_cursor:]
        _log_cursor = len(_log_buffer)
    return jsonify({**_state, "logs": new_logs})


@api.route("/api/relay/start", methods=["POST"])
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


@api.route("/api/relay/stop", methods=["POST"])
def stop_relay():
    global _relay
    if _relay and _relay.running:
        _relay.running = False
        _add_log("Relay stop requested")
        return jsonify({"status": "stopping"})
    return jsonify({"status": "not_running"})


@api.route("/api/relay/logs/clear", methods=["POST"])
def clear_logs():
    global _log_buffer, _log_cursor
    with _log_lock:
        _log_buffer = []
        _log_cursor = 0
    return jsonify({"status": "cleared"})


@api.route("/api/managed/enable", methods=["POST"])
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


@api.route("/api/managed/disable", methods=["POST"])
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


# ── JS Bridge ────────────────────────────────────────────────────────────────

class JsBridge:
    """Exposed to React via window.pywebview.api"""

    def get_keyring_password(self, service: str, user_id: str) -> str:
        try:
            import keyring
            return keyring.get_password(service, user_id) or ""
        except Exception:
            return ""

    def set_keyring_password(self, service: str, user_id: str, password: str):
        try:
            import keyring
            keyring.set_password(service, user_id, password)
        except Exception:
            pass

    def browse_file(self, title: str, start_dir: str, filter_str: str) -> str:
        try:
            result = _window.create_file_dialog(
                webview.OPEN_DIALOG,
                directory=start_dir,
                allow_multiple=False,
                file_types=(filter_str,),
            )
            return result[0] if result else ""
        except Exception:
            return ""

    def detect_mt5_path(self) -> str:
        if platform.system() != "Windows":
            return ""
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
                            return str(terminal)
                except (FileNotFoundError, OSError):
                    continue
        except Exception:
            pass
        return ""

    def is_startup_enabled(self) -> bool:
        if platform.system() == "Windows":
            import subprocess
            result = subprocess.run(
                ["schtasks", "/query", "/tn", "PlatAlgoRelay"],
                capture_output=True, text=True,
            )
            return result.returncode == 0
        elif platform.system() == "Darwin":
            plist = Path.home() / "Library" / "LaunchAgents" / "com.platalgo.relay.plist"
            return plist.exists()
        return False

    def enable_startup(self):
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

    def disable_startup(self):
        if platform.system() == "Windows":
            import subprocess
            subprocess.run(["schtasks", "/delete", "/tn", "PlatAlgoRelay", "/f"], capture_output=True)
        elif platform.system() == "Darwin":
            plist = Path.home() / "Library" / "LaunchAgents" / "com.platalgo.relay.plist"
            plist.unlink(missing_ok=True)

    def set_clipboard(self, text: str):
        try:
            _window.evaluate_js(f"navigator.clipboard.writeText({json.dumps(text)})")
        except Exception:
            pass

    def get_last_user(self) -> str:
        try:
            return LAST_USER_FILE.read_text(encoding="utf-8")
        except Exception:
            return ""

    def save_last_user(self, data_json: str):
        try:
            LAST_USER_FILE.write_text(data_json, encoding="utf-8")
        except Exception:
            pass

    def open_external(self, url: str):
        import webbrowser
        webbrowser.open(url)


# ── Main ─────────────────────────────────────────────────────────────────────

_window = None


def main():
    global _window

    # Start Flask API in background
    flask_thread = threading.Thread(
        target=lambda: api.run(host="127.0.0.1", port=LOCAL_PORT, debug=False, use_reloader=False),
        daemon=True,
    )
    flask_thread.start()
    logger.info(f"Local API on http://127.0.0.1:{LOCAL_PORT}")

    js_bridge = JsBridge()
    is_dev = "--dev" in sys.argv

    if is_dev:
        url = "http://localhost:5173"
        logger.info(f"Dev mode: loading {url}")
    else:
        index = DIST_DIR / "index.html"
        if not index.exists():
            logger.error(f"Build not found: {index}. Run 'npm run build' in relay-ui/")
            sys.exit(1)
        url = str(index)
        logger.info(f"Production mode: loading {url}")

    _window = webview.create_window(
        "PlatAlgo Relay",
        url=url,
        js_api=js_bridge,
        width=1300,
        height=860,
        min_size=(1100, 720),
        background_color="#0A1210",
    )

    webview.start(debug=is_dev)


if __name__ == "__main__":
    main()
