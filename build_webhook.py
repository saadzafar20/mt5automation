"""
build_webhook.py — GitHub push webhook listener for Windows VPS.

Listens on port 9000 (internal, not exposed to internet directly).
Caddy forwards /webhook/build -> localhost:9000/webhook/build.

Set environment variables (in a .env file or system env):
    WEBHOOK_SECRET   = the secret you set in GitHub webhook settings
    BRIDGE_PUBLIC_URL = https://app.platalgo.com

Run as a Windows service via NSSM:
    nssm install PlatAlgoBuildHook python C:\\trading\\build_webhook.py
    nssm set PlatAlgoBuildHook AppDirectory C:\\trading
    nssm start PlatAlgoBuildHook
"""

import hashlib
import hmac
import json
import logging
import os
import subprocess
import threading
from pathlib import Path

from flask import Flask, request, jsonify

# ── Config ────────────────────────────────────────────────────────────────────

BASE_DIR        = Path(__file__).parent          # C:\trading
DOWNLOADS_DIR   = BASE_DIR / "downloads"
BUILD_SCRIPT    = BASE_DIR / "build_and_deploy.bat"
LOG_FILE        = BASE_DIR / "build_webhook.log"
WEBHOOK_SECRET  = os.getenv("WEBHOOK_SECRET", "")
PORT            = int(os.getenv("WEBHOOK_PORT", "9000"))
PUBLIC_BASE_URL = os.getenv("BRIDGE_PUBLIC_URL", "").rstrip("/")
BRANCH          = os.getenv("WEBHOOK_BRANCH", "refs/heads/main")

# ── Logging ───────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger(__name__)

# ── Flask app ─────────────────────────────────────────────────────────────────

app = Flask(__name__)

# Lock so only one build runs at a time
_build_lock = threading.Lock()
_build_status = {"running": False, "last_exit": None, "last_trigger": None}


def _verify_signature(payload: bytes, sig_header: str) -> bool:
    """Verify GitHub HMAC-SHA256 webhook signature."""
    if not WEBHOOK_SECRET:
        log.warning("WEBHOOK_SECRET not set — skipping signature check")
        return True
    if not sig_header or not sig_header.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(
        WEBHOOK_SECRET.encode(), payload, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, sig_header)


def _run_build():
    """Run build_and_deploy.bat in a background thread."""
    if not _build_lock.acquire(blocking=False):
        log.info("Build already running — skipping")
        return
    try:
        _build_status["running"] = True
        log.info("Starting build: %s", BUILD_SCRIPT)
        result = subprocess.run(
            ["cmd", "/c", str(BUILD_SCRIPT)],
            cwd=str(BASE_DIR),
            capture_output=True,
            text=True,
        )
        _build_status["last_exit"] = result.returncode
        if result.returncode == 0:
            log.info("Build succeeded")
        else:
            log.error("Build failed (exit %d):\n%s", result.returncode, result.stderr)
    finally:
        _build_status["running"] = False
        _build_lock.release()


@app.route("/webhook/build", methods=["POST"])
def webhook_build():
    payload = request.get_data()

    # Verify signature
    sig = request.headers.get("X-Hub-Signature-256", "")
    if not _verify_signature(payload, sig):
        log.warning("Invalid webhook signature")
        return jsonify({"error": "invalid signature"}), 403

    # Only act on push to the target branch
    try:
        data = json.loads(payload)
    except Exception:
        return jsonify({"error": "bad json"}), 400

    ref = data.get("ref", "")
    if ref != BRANCH:
        log.info("Ignoring push to %s (want %s)", ref, BRANCH)
        return jsonify({"ok": True, "skipped": True}), 200

    import datetime
    _build_status["last_trigger"] = datetime.datetime.utcnow().isoformat()
    log.info("Push to %s received — queuing build", BRANCH)

    thread = threading.Thread(target=_run_build, daemon=True)
    thread.start()

    return jsonify({"ok": True, "message": "build started"}), 202


@app.route("/webhook/build/status", methods=["GET"])
def build_status():
    return jsonify(_build_status)


if __name__ == "__main__":
    DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)
    log.info("Build webhook server starting on port %d", PORT)
    app.run(host="127.0.0.1", port=PORT)
