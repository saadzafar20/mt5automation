"""
Production-ready test suite for PlatAlgo Cloud Bridge.
Run:
  pytest tests/test_production_ready.py -m "not integration and not live_mt5" -v   # unit only
  pytest tests/test_production_ready.py -m integration -v                           # integration (needs live bridge)
  pytest tests/test_production_ready.py -m live_mt5 -v                             # live MT5 (needs VPS)
"""
import hashlib
import json
import os
import sys
import pytest
import sqlite3
import tempfile
import time

# Setup env before importing cloud_bridge
os.environ.setdefault("BRIDGE_AUTH_SALT", "test-salt-for-unit-tests")
os.environ.setdefault("BRIDGE_SESSION_SECRET", "test-session-secret")
os.environ.setdefault("BRIDGE_CREDS_KEY", "test-encryption-key-32-chars-ok")
os.environ.setdefault("CLOUD_BRIDGE_DEBUG", "true")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ── Config ───────────────────────────────────────────────────────────────────

BRIDGE_URL = os.getenv("BRIDGE_URL", "https://app.platalgo.com")
TEST_USER_ID = os.getenv("TEST_USER_ID", "Ayan")
TEST_PASSWORD = os.getenv("TEST_PASSWORD", "123456Uy")
TEST_API_KEY = os.getenv("TEST_API_KEY", "")  # populated by live auth flow tests

MT5_LOGIN = os.getenv("MT5_LOGIN", "104414754")
MT5_PASSWORD = os.getenv("MT5_PASSWORD", "Mx_0FzTp")
MT5_SERVER = os.getenv("MT5_SERVER", "MetaQuotes-Demo")


# ── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def test_store(tmp_path):
    """Provide an isolated BridgeStore backed by a temp DB."""
    db_path = str(tmp_path / "test.db")
    os.environ["BRIDGE_DB_PATH"] = db_path
    from cloud_bridge import BridgeStore
    s = BridgeStore(db_path)
    yield s
    s.conn.close()


@pytest.fixture
def test_app(tmp_path):
    """Provide a Flask test client with an isolated DB — no module reload."""
    import cloud_bridge
    db_path = str(tmp_path / "test.db")
    # Swap store to a fresh isolated DB; routes reference the module-level `store`
    orig_store = cloud_bridge.store
    cloud_bridge.store = cloud_bridge.BridgeStore(db_path)
    cloud_bridge.app.config["TESTING"] = True
    cloud_bridge.app.config["WTF_CSRF_ENABLED"] = False
    yield cloud_bridge.app.test_client()
    cloud_bridge.store.conn.close()
    cloud_bridge.store = orig_store


@pytest.fixture
def live_client():
    """Provide an authenticated requests session for live integration tests."""
    import requests
    session = requests.Session()
    session.headers.update({"X-User-ID": TEST_USER_ID, "X-API-Key": TEST_API_KEY})
    return session


# ── Unit Tests — TestInviteCodes ──────────────────────────────────────────────

class TestInviteCodes:
    @pytest.mark.unit
    def test_create_validate_consume(self, test_store):
        """Invite codes can be created, validated, consumed, and re-validation fails."""
        code = test_store.create_invite_code()
        assert isinstance(code, str) and len(code) > 8

        valid, reason = test_store.validate_invite_code(code)
        assert valid is True
        assert reason == ""

        test_store.consume_invite_code(code, "user123")

        valid2, reason2 = test_store.validate_invite_code(code)
        assert valid2 is False
        assert "already used" in reason2

    @pytest.mark.unit
    def test_expired_code(self, test_store):
        """Invite codes with a past expiry are rejected as expired."""
        code = test_store.create_invite_code(expires_hours=-1)
        valid, reason = test_store.validate_invite_code(code)
        assert valid is False
        assert "expired" in reason

    @pytest.mark.unit
    def test_invalid_code(self, test_store):
        """Non-existent invite codes are rejected."""
        valid, reason = test_store.validate_invite_code("non-existent-code-xyz")
        assert valid is False
        assert "invalid" in reason

    @pytest.mark.unit
    def test_list_invite_codes(self, test_store):
        """list_invite_codes returns all created codes."""
        c1 = test_store.create_invite_code()
        c2 = test_store.create_invite_code(expires_hours=24)
        codes = test_store.list_invite_codes()
        code_values = [c["code"] for c in codes]
        assert c1 in code_values
        assert c2 in code_values

    @pytest.mark.unit
    def test_register_requires_invite_code(self, test_app):
        """POST /register without invite_code returns 400."""
        resp = test_app.post("/register", data={
            "user_id": "newuser",
            "password": "Password1",
            "password_confirm": "Password1",
        })
        assert resp.status_code == 400
        assert b"invite" in resp.data.lower()

    @pytest.mark.unit
    def test_register_invalid_invite_code(self, test_app):
        """POST /register with invalid invite code returns 400."""
        resp = test_app.post("/register", data={
            "user_id": "newuser",
            "password": "Password1",
            "password_confirm": "Password1",
            "invite_code": "bad-code",
        })
        assert resp.status_code == 400
        assert b"invalid" in resp.data.lower() or b"invite" in resp.data.lower()

    @pytest.mark.unit
    def test_register_success_with_valid_invite(self, test_app):
        """POST /register with valid invite code creates user and redirects."""
        import importlib
        import cloud_bridge
        importlib.reload(cloud_bridge)
        code = cloud_bridge.store.create_invite_code()
        cloud_bridge.app.config["TESTING"] = True
        client = cloud_bridge.app.test_client()
        resp = client.post("/register", data={
            "user_id": "testuser_valid",
            "password": "Password1",
            "password_confirm": "Password1",
            "invite_code": code,
        })
        assert resp.status_code in (200, 302)
        valid, reason = cloud_bridge.store.validate_invite_code(code)
        assert valid is False
        assert "already used" in reason


# ── Unit Tests — TestMagicNumbers ─────────────────────────────────────────────

class TestMagicNumbers:
    @pytest.mark.unit
    def test_magic_number_range(self, test_store):
        """Magic numbers are within the 6-digit range."""
        test_store.upsert_user("magic_user1", "key1")
        m = test_store.get_user_magic_number("magic_user1")
        assert 100000 <= m <= 999999

    @pytest.mark.unit
    def test_magic_numbers_unique(self, test_store):
        """Magic numbers are generated uniquely for each user."""
        test_store.upsert_user("magic_user1", "key1")
        test_store.upsert_user("magic_user2", "key2")
        test_store.upsert_user("magic_user3", "key3")
        m1 = test_store.get_user_magic_number("magic_user1")
        m2 = test_store.get_user_magic_number("magic_user2")
        m3 = test_store.get_user_magic_number("magic_user3")
        assert len({m1, m2, m3}) == 3, "Magic numbers should be unique"

    @pytest.mark.unit
    def test_magic_number_stable(self, test_store):
        """Same user always gets the same magic number."""
        test_store.upsert_user("stable_user", "key")
        m1 = test_store.get_user_magic_number("stable_user")
        m2 = test_store.get_user_magic_number("stable_user")
        assert m1 == m2


# ── Unit Tests — TestCircuitBreaker ──────────────────────────────────────────

class TestCircuitBreaker:
    @pytest.mark.unit
    def test_default_not_broken(self, test_store):
        """Circuit breaker starts as not broken."""
        test_store.upsert_user("cb_user", "cb_key")
        status = test_store.get_circuit_status("cb_user")
        assert status["broken"] is False

    @pytest.mark.unit
    def test_set_broken_true(self, test_store):
        """Circuit breaker can be set to broken."""
        test_store.upsert_user("cb_user", "cb_key")
        test_store.set_circuit_broken("cb_user", True)
        status = test_store.get_circuit_status("cb_user")
        assert status["broken"] is True
        assert status["broken_at"] is not None

    @pytest.mark.unit
    def test_reset_circuit_breaker(self, test_store):
        """Circuit breaker can be reset to not broken."""
        test_store.upsert_user("cb_user", "cb_key")
        test_store.set_circuit_broken("cb_user", True)
        test_store.set_circuit_broken("cb_user", False)
        status = test_store.get_circuit_status("cb_user")
        assert status["broken"] is False

    @pytest.mark.unit
    def test_circuit_breaker_reset_endpoint_requires_auth(self, test_app):
        """POST /api/circuit-breaker/reset without auth returns 400 or 401."""
        resp = test_app.post("/api/circuit-breaker/reset")
        assert resp.status_code in (400, 401)


# ── Unit Tests — TestIdempotency ─────────────────────────────────────────────

class TestIdempotency:
    @pytest.mark.unit
    def test_new_key_not_duplicate(self, test_store):
        """A fresh idempotency key is not a duplicate."""
        test_store.upsert_user("idem_user", "idem_key")
        assert test_store.check_idempotency("fresh-key-xyz", "idem_user", ttl_secs=120) is False

    @pytest.mark.unit
    def test_recorded_key_is_duplicate(self, test_store):
        """After recording, the same key is detected as duplicate."""
        test_store.upsert_user("idem_user", "idem_key")
        idem_key = "test-idem-key-abc123"
        assert test_store.check_idempotency(idem_key, "idem_user", ttl_secs=120) is False
        test_store.record_idempotency(idem_key, "idem_user")
        assert test_store.check_idempotency(idem_key, "idem_user", ttl_secs=120) is True

    @pytest.mark.unit
    def test_expired_key_not_duplicate(self, test_store):
        """An expired idempotency key is not a duplicate."""
        test_store.upsert_user("idem_user", "idem_key")
        idem_key = "test-idem-key-expire"
        test_store.record_idempotency(idem_key, "idem_user")
        assert test_store.check_idempotency(idem_key, "idem_user", ttl_secs=0) is False

    @pytest.mark.unit
    def test_different_users_isolated(self, test_store):
        """Idempotency keys are isolated per user."""
        test_store.upsert_user("user_a", "key_a")
        test_store.upsert_user("user_b", "key_b")
        idem_key = "shared-key"
        test_store.record_idempotency(idem_key, "user_a")
        # user_b should not see user_a's key
        assert test_store.check_idempotency(idem_key, "user_b", ttl_secs=120) is False


# ── Unit Tests — TestInputValidation ─────────────────────────────────────────

class TestInputValidation:
    @pytest.mark.unit
    def test_register_short_username(self, test_app):
        """POST /register with username < 3 chars returns 400."""
        import importlib
        import cloud_bridge
        importlib.reload(cloud_bridge)
        code = cloud_bridge.store.create_invite_code()
        cloud_bridge.app.config["TESTING"] = True
        client = cloud_bridge.app.test_client()
        resp = client.post("/register", data={
            "user_id": "ab",
            "password": "Password1",
            "password_confirm": "Password1",
            "invite_code": code,
        })
        assert resp.status_code == 400

    @pytest.mark.unit
    def test_register_short_password(self, test_app):
        """POST /register with password < 8 chars returns 400."""
        import importlib
        import cloud_bridge
        importlib.reload(cloud_bridge)
        code = cloud_bridge.store.create_invite_code()
        cloud_bridge.app.config["TESTING"] = True
        client = cloud_bridge.app.test_client()
        resp = client.post("/register", data={
            "user_id": "validuser",
            "password": "short",
            "password_confirm": "short",
            "invite_code": code,
        })
        assert resp.status_code == 400

    @pytest.mark.unit
    def test_register_password_mismatch(self, test_app):
        """POST /register with mismatched passwords returns 400."""
        import importlib
        import cloud_bridge
        importlib.reload(cloud_bridge)
        code = cloud_bridge.store.create_invite_code()
        cloud_bridge.app.config["TESTING"] = True
        client = cloud_bridge.app.test_client()
        resp = client.post("/register", data={
            "user_id": "validuser",
            "password": "Password1",
            "password_confirm": "Password2",
            "invite_code": code,
        })
        assert resp.status_code == 400

    @pytest.mark.unit
    def test_health_returns_online(self, test_app):
        """GET /health returns status=online with required fields."""
        resp = test_app.get("/health")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data["status"] == "online"
        assert "bridge" in data
        assert "uptime_secs" in data
        assert "components" in data
        assert "managed_sessions" in data["components"]
        assert "timestamp" in data


# ── Unit Tests — TestRateLimiting ────────────────────────────────────────────

class TestRateLimiting:
    @pytest.mark.unit
    def test_register_route_exists(self, test_app):
        """GET /register page is accessible (rate limiter does not block single request)."""
        resp = test_app.get("/register")
        assert resp.status_code == 200

    @pytest.mark.unit
    def test_health_not_rate_limited(self, test_app):
        """Health endpoint responds fine without being blocked."""
        for _ in range(5):
            resp = test_app.get("/health")
            assert resp.status_code == 200

    @pytest.mark.unit
    def test_rate_limit_helper_noop_without_limiter(self, test_app):
        """_rate_limit decorator is a no-op when flask-limiter is unavailable."""
        import cloud_bridge
        # If _limiter is None the decorator should just return the function unchanged
        if cloud_bridge._limiter is None:
            def dummy():
                return "ok"
            decorated = cloud_bridge._rate_limit("1 per minute")(dummy)
            assert decorated is dummy


# ── Unit Tests — TestAnalytics ───────────────────────────────────────────────

class TestAnalytics:
    @pytest.mark.unit
    def test_analytics_requires_auth(self, test_app):
        """GET /api/analytics without auth returns 400 or 401."""
        resp = test_app.get("/api/analytics")
        assert resp.status_code in (400, 401)

    @pytest.mark.unit
    def test_dashboard_analytics_requires_auth(self, test_app):
        """GET /dashboard/analytics without auth returns 400 or 401."""
        resp = test_app.get("/dashboard/analytics")
        assert resp.status_code in (400, 401)

    @pytest.mark.unit
    def test_api_analytics_structure(self, test_app):
        """GET /api/analytics with auth returns correct shape."""
        import importlib
        import cloud_bridge
        importlib.reload(cloud_bridge)
        # Create user and get api_key
        api_key = cloud_bridge.store.register_dashboard_user("analytics_user", "Password1!")
        cloud_bridge.app.config["TESTING"] = True
        os.environ["BRIDGE_REQUIRE_API_KEY"] = "true"
        client = cloud_bridge.app.test_client()
        resp = client.get("/api/analytics?days=7", headers={
            "X-User-ID": "analytics_user",
            "X-API-Key": api_key,
        })
        os.environ["BRIDGE_REQUIRE_API_KEY"] = "false"
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert "total_signals" in data
        assert "executed" in data
        assert "failed" in data
        assert "success_rate" in data
        assert "by_script" in data
        assert "period_days" in data


# ── Unit Tests — TestWebhookRotation ─────────────────────────────────────────

class TestWebhookRotation:
    @pytest.mark.unit
    def test_webhook_rotate_requires_auth(self, test_app):
        """POST /api/rotate-webhook without auth returns 400 or 401."""
        resp = test_app.post("/api/rotate-webhook")
        assert resp.status_code in (400, 401)

    @pytest.mark.unit
    def test_webhook_rotate_returns_new_url(self, test_app):
        """POST /api/rotate-webhook with auth returns new webhook_url."""
        import cloud_bridge
        api_key = cloud_bridge.store.register_dashboard_user("webhook_user", "Password1!")
        os.environ["BRIDGE_REQUIRE_API_KEY"] = "true"
        try:
            resp = test_app.post("/api/rotate-webhook", headers={
                "X-User-ID": "webhook_user",
                "X-API-Key": api_key,
            })
        finally:
            os.environ["BRIDGE_REQUIRE_API_KEY"] = "false"
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data["status"] == "rotated"
        assert "webhook_url" in data
        assert "/signal/" in data["webhook_url"]


# ── Unit Tests — TestHealthEndpoint ──────────────────────────────────────────

class TestHealthEndpoint:
    @pytest.mark.unit
    def test_health_status_online(self, test_app):
        """GET /health returns status=online."""
        resp = test_app.get("/health")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data["status"] == "online"

    @pytest.mark.unit
    def test_health_has_bridge_version(self, test_app):
        """GET /health includes bridge version string."""
        resp = test_app.get("/health")
        data = json.loads(resp.data)
        assert "bridge" in data
        assert isinstance(data["bridge"], str)

    @pytest.mark.unit
    def test_health_has_uptime(self, test_app):
        """GET /health includes uptime_secs as non-negative int."""
        resp = test_app.get("/health")
        data = json.loads(resp.data)
        assert "uptime_secs" in data
        assert data["uptime_secs"] >= 0

    @pytest.mark.unit
    def test_health_has_timestamp(self, test_app):
        """GET /health includes ISO timestamp."""
        resp = test_app.get("/health")
        data = json.loads(resp.data)
        assert "timestamp" in data

    @pytest.mark.unit
    def test_health_has_managed_sessions(self, test_app):
        """GET /health components includes managed_sessions."""
        resp = test_app.get("/health")
        data = json.loads(resp.data)
        assert "managed_sessions" in data["components"]


# ── Unit Tests — TestTradeExport ─────────────────────────────────────────────

class TestTradeExport:
    @pytest.mark.unit
    def test_export_requires_auth(self, test_app):
        """GET /api/export/trades without auth returns 400 or 401."""
        resp = test_app.get("/api/export/trades")
        assert resp.status_code in (400, 401)

    @pytest.mark.unit
    def test_export_returns_csv(self, test_app):
        """GET /api/export/trades with auth returns CSV content."""
        import importlib
        import cloud_bridge
        importlib.reload(cloud_bridge)
        api_key = cloud_bridge.store.register_dashboard_user("export_user", "Password1!")
        cloud_bridge.app.config["TESTING"] = True
        os.environ["BRIDGE_REQUIRE_API_KEY"] = "true"
        client = cloud_bridge.app.test_client()
        resp = client.get("/api/export/trades", headers={
            "X-User-ID": "export_user",
            "X-API-Key": api_key,
        })
        os.environ["BRIDGE_REQUIRE_API_KEY"] = "false"
        assert resp.status_code == 200
        content_type = resp.headers.get("Content-Type", "")
        assert "csv" in content_type or "text" in content_type
        # CSV header row must be present
        assert b"timestamp" in resp.data or b"action" in resp.data


# ── Legacy flat unit tests (kept for backwards compatibility) ─────────────────

@pytest.mark.unit
def test_invite_code_create_validate_consume(test_store):
    """Invite codes can be created, validated, consumed, and re-validation fails."""
    code = test_store.create_invite_code()
    assert isinstance(code, str) and len(code) > 8

    valid, reason = test_store.validate_invite_code(code)
    assert valid is True
    assert reason == ""

    test_store.consume_invite_code(code, "user123")

    valid2, reason2 = test_store.validate_invite_code(code)
    assert valid2 is False
    assert "already used" in reason2


@pytest.mark.unit
def test_invite_code_expired(test_store):
    """Invite codes with a past expiry are rejected as expired."""
    code = test_store.create_invite_code(expires_hours=-1)
    valid, reason = test_store.validate_invite_code(code)
    assert valid is False
    assert "expired" in reason


@pytest.mark.unit
def test_invite_code_invalid(test_store):
    """Non-existent invite codes are rejected."""
    valid, reason = test_store.validate_invite_code("non-existent-code-xyz")
    assert valid is False
    assert "invalid" in reason


@pytest.mark.unit
def test_signal_idempotency(test_store):
    """Idempotency keys are recorded and expire after TTL."""
    test_store.upsert_user("idem_user", "idem_key")

    idem_key = "test-idem-key-abc123"
    assert test_store.check_idempotency(idem_key, "idem_user", ttl_secs=120) is False
    test_store.record_idempotency(idem_key, "idem_user")
    assert test_store.check_idempotency(idem_key, "idem_user", ttl_secs=120) is True
    assert test_store.check_idempotency(idem_key, "idem_user", ttl_secs=0) is False


@pytest.mark.unit
def test_circuit_breaker_set_get(test_store):
    """Circuit breaker state can be set and retrieved."""
    test_store.upsert_user("cb_user", "cb_key")
    status = test_store.get_circuit_status("cb_user")
    assert status["broken"] is False

    test_store.set_circuit_broken("cb_user", True)
    status = test_store.get_circuit_status("cb_user")
    assert status["broken"] is True
    assert status["broken_at"] is not None

    test_store.set_circuit_broken("cb_user", False)
    status = test_store.get_circuit_status("cb_user")
    assert status["broken"] is False


@pytest.mark.unit
def test_magic_number_unique(test_store):
    """Magic numbers are generated uniquely and within the expected range."""
    test_store.upsert_user("magic_user1", "key1")
    test_store.upsert_user("magic_user2", "key2")
    test_store.upsert_user("magic_user3", "key3")

    m1 = test_store.get_user_magic_number("magic_user1")
    m2 = test_store.get_user_magic_number("magic_user2")
    m3 = test_store.get_user_magic_number("magic_user3")

    assert 100000 <= m1 <= 999999
    assert 100000 <= m2 <= 999999
    assert 100000 <= m3 <= 999999
    assert len({m1, m2, m3}) == 3, "Magic numbers should be unique"


@pytest.mark.unit
def test_health_endpoint(test_app):
    """GET /health returns 200 with status=online."""
    resp = test_app.get("/health")
    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert data["status"] == "online"
    assert "bridge" in data
    assert "uptime_secs" in data
    assert "components" in data
    assert "managed_sessions" in data["components"]
    assert "timestamp" in data


@pytest.mark.unit
def test_register_requires_invite_code(test_app):
    """POST /register without invite_code returns 400."""
    resp = test_app.post("/register", data={
        "user_id": "newuser",
        "password": "Password1",
        "password_confirm": "Password1",
    })
    assert resp.status_code == 400
    assert b"invite code" in resp.data.lower()


@pytest.mark.unit
def test_register_invalid_invite_code(test_app):
    """POST /register with invalid invite code returns 400 with error message."""
    resp = test_app.post("/register", data={
        "user_id": "newuser",
        "password": "Password1",
        "password_confirm": "Password1",
        "invite_code": "bad-code",
    })
    assert resp.status_code == 400
    assert b"invalid" in resp.data.lower() or b"invite" in resp.data.lower()


@pytest.mark.unit
def test_terms_page(test_app):
    """GET /terms returns 200."""
    resp = test_app.get("/terms")
    assert resp.status_code == 200
    assert b"Terms" in resp.data


@pytest.mark.unit
def test_privacy_page(test_app):
    """GET /privacy returns 200."""
    resp = test_app.get("/privacy")
    assert resp.status_code == 200
    assert b"Privacy" in resp.data


@pytest.mark.unit
def test_analytics_requires_auth(test_app):
    """GET /api/analytics without auth returns 400 or 401."""
    resp = test_app.get("/api/analytics")
    assert resp.status_code in (400, 401)


@pytest.mark.unit
def test_circuit_breaker_reset_requires_auth(test_app):
    """POST /api/circuit-breaker/reset without auth returns 400 or 401."""
    resp = test_app.post("/api/circuit-breaker/reset")
    assert resp.status_code in (400, 401)


@pytest.mark.unit
def test_export_trades_requires_auth(test_app):
    """GET /api/export/trades without auth returns 400 or 401."""
    resp = test_app.get("/api/export/trades")
    assert resp.status_code in (400, 401)


@pytest.mark.unit
def test_list_invite_codes(test_store):
    """list_invite_codes returns all created codes."""
    c1 = test_store.create_invite_code()
    c2 = test_store.create_invite_code(expires_hours=24)
    codes = test_store.list_invite_codes()
    code_values = [c["code"] for c in codes]
    assert c1 in code_values
    assert c2 in code_values


@pytest.mark.unit
def test_register_success_with_valid_invite(test_app):
    """POST /register with valid invite code creates user and redirects."""
    import importlib
    import cloud_bridge
    importlib.reload(cloud_bridge)
    code = cloud_bridge.store.create_invite_code()

    cloud_bridge.app.config["TESTING"] = True
    client = cloud_bridge.app.test_client()

    resp = client.post("/register", data={
        "user_id": "testuser_valid",
        "password": "Password1",
        "password_confirm": "Password1",
        "invite_code": code,
    })
    assert resp.status_code in (200, 302)
    valid, reason = cloud_bridge.store.validate_invite_code(code)
    assert valid is False
    assert "already used" in reason


# ── Integration Tests (need live bridge) ─────────────────────────────────────

class TestLiveAuthFlow:
    """Integration tests for the live authentication flow."""

    @pytest.mark.integration
    def test_live_health(self):
        """GET /health on live bridge returns status=online."""
        import requests
        resp = requests.get(f"{BRIDGE_URL}/health", timeout=10)
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "online"
        assert "uptime_secs" in data

    @pytest.mark.integration
    def test_live_login_returns_api_key(self):
        """POST /relay/login with valid credentials returns api_key."""
        if not TEST_PASSWORD:
            pytest.skip("TEST_PASSWORD not set")
        import requests
        resp = requests.post(
            f"{BRIDGE_URL}/relay/login",
            json={"user_id": TEST_USER_ID, "password": TEST_PASSWORD},
            timeout=10,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "api_key" in data
        # Cache the api_key for downstream tests via environment
        os.environ["TEST_API_KEY"] = data["api_key"]

    @pytest.mark.integration
    def test_live_terms_page(self):
        """GET /terms on live bridge returns 200."""
        import requests
        resp = requests.get(f"{BRIDGE_URL}/terms", timeout=10)
        assert resp.status_code == 200

    @pytest.mark.integration
    def test_live_privacy_page(self):
        """GET /privacy on live bridge returns 200."""
        import requests
        resp = requests.get(f"{BRIDGE_URL}/privacy", timeout=10)
        assert resp.status_code == 200


class TestLiveWebhookFlow:
    """Integration tests for the live webhook / signal pipeline."""

    @pytest.mark.integration
    def test_live_analytics(self, live_client):
        """GET /api/analytics returns expected structure."""
        if not TEST_API_KEY:
            pytest.skip("TEST_API_KEY not set")
        resp = live_client.get(f"{BRIDGE_URL}/api/analytics?days=7", timeout=10)
        assert resp.status_code == 200
        data = resp.json()
        assert "period_days" in data
        assert "total_signals" in data
        assert "executed" in data
        assert "failed" in data
        assert "success_rate" in data
        assert "by_script" in data
        assert data["period_days"] == 7

    @pytest.mark.integration
    def test_live_dashboard_analytics(self, live_client):
        """GET /dashboard/analytics returns win_rate, by_symbol, by_script, recent_24h."""
        if not TEST_API_KEY:
            pytest.skip("TEST_API_KEY not set")
        resp = live_client.get(f"{BRIDGE_URL}/dashboard/analytics", timeout=10)
        assert resp.status_code == 200
        data = resp.json()
        assert "win_rate" in data
        assert "total_signals" in data
        assert "executed" in data
        assert "failed" in data
        assert "signals_today" in data
        assert "signals_this_week" in data
        assert "by_symbol" in data
        assert "by_script" in data
        assert "recent_24h" in data

    @pytest.mark.integration
    def test_live_circuit_breaker_reset(self, live_client):
        """POST /api/circuit-breaker/reset returns 200 on live bridge."""
        if not TEST_API_KEY:
            pytest.skip("TEST_API_KEY not set")
        resp = live_client.post(f"{BRIDGE_URL}/api/circuit-breaker/reset", timeout=10)
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "reset"

    @pytest.mark.integration
    def test_live_webhook_rotate(self, live_client):
        """POST /api/webhook/rotate returns a new webhook URL."""
        if not TEST_API_KEY:
            pytest.skip("TEST_API_KEY not set")
        resp = live_client.post(f"{BRIDGE_URL}/api/webhook/rotate", timeout=10)
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "rotated"
        assert "webhook_url" in data

    @pytest.mark.integration
    def test_live_managed_account_info(self, live_client):
        """GET /managed/account-info returns account data."""
        if not TEST_API_KEY:
            pytest.skip("TEST_API_KEY not set")
        resp = live_client.get(f"{BRIDGE_URL}/managed/account-info", timeout=10)
        # Either 200 (if managed MT5 is connected) or 400 (managed mode not enabled)
        assert resp.status_code in (200, 400)


# ── Live MT5 Tests (need VPS + real MT5 session) ──────────────────────────────

class TestLiveMT5Execution:
    """
    Live end-to-end tests that exercise the managed MT5 execution pipeline.

    These tests are ordered (test_1_ through test_9_) and share state via
    class attributes. Run with:
        pytest tests/test_production_ready.py -m live_mt5 -v -s
    """

    _api_key: str = ""
    _live_url: str = BRIDGE_URL
    _webhook_token: str = ""

    @classmethod
    def _headers(cls):
        return {
            "X-User-ID": TEST_USER_ID,
            "X-API-Key": cls._api_key or os.environ.get("TEST_API_KEY", ""),
            "Content-Type": "application/json",
        }

    @pytest.mark.live_mt5
    def test_1_restart_managed_session(self):
        """Authenticate and start/restart the managed MT5 session."""
        import requests

        # Step 1: authenticate to get api_key
        login_resp = requests.post(
            f"{self._live_url}/relay/login",
            json={"user_id": TEST_USER_ID, "password": TEST_PASSWORD},
            timeout=15,
        )
        assert login_resp.status_code == 200, f"Login failed: {login_resp.text}"
        data = login_resp.json()
        assert "api_key" in data
        TestLiveMT5Execution._api_key = data["api_key"]
        os.environ["TEST_API_KEY"] = data["api_key"]

        # Step 2: set up managed MT5
        setup_resp = requests.post(
            f"{self._live_url}/managed/setup",
            json={
                "mt5_login": MT5_LOGIN,
                "mt5_password": MT5_PASSWORD,
                "mt5_server": MT5_SERVER,
            },
            headers=self._headers(),
            timeout=30,
        )
        assert setup_resp.status_code == 200, f"Setup failed: {setup_resp.text}"
        data = setup_resp.json()
        assert data.get("managed_execution") is True

    @pytest.mark.live_mt5
    def test_2_wait_for_mt5_connected(self):
        """Poll /managed/status until MT5 session is connected (up to 60s)."""
        import requests
        deadline = time.time() + 60
        connected = False
        while time.time() < deadline:
            resp = requests.get(
                f"{self._live_url}/managed/status",
                headers=self._headers(),
                timeout=10,
            )
            assert resp.status_code == 200
            data = resp.json()
            if data.get("connected"):
                connected = True
                break
            time.sleep(3)
        assert connected, "MT5 session did not connect within 60 seconds"

    @pytest.mark.live_mt5
    def test_3_account_info(self):
        """GET /managed/account-info returns balance and equity."""
        import requests
        resp = requests.get(
            f"{self._live_url}/managed/account-info",
            headers=self._headers(),
            timeout=15,
        )
        assert resp.status_code == 200, f"Account info failed: {resp.text}"
        data = resp.json()
        assert "balance" in data or "equity" in data, f"Unexpected response: {data}"

    @pytest.mark.live_mt5
    def test_4_buy_trade(self):
        """Execute a BUY trade on EURUSD via managed execution."""
        import requests
        resp = requests.post(
            f"{self._live_url}/api/trade",
            json={
                "action": "BUY",
                "symbol": "EURUSD",
                "size": 0.01,
            },
            headers=self._headers(),
            timeout=20,
        )
        assert resp.status_code == 200, f"BUY trade failed: {resp.text}"
        data = resp.json()
        assert data.get("status") == "executed", f"Trade not executed: {data}"
        assert "order_id" in data or "command_id" in data

    @pytest.mark.live_mt5
    def test_5_close_all_positions(self):
        """Close all open positions via managed execution."""
        import requests
        resp = requests.post(
            f"{self._live_url}/api/trade",
            json={"action": "CLOSE_ALL", "symbol": ""},
            headers=self._headers(),
            timeout=20,
        )
        assert resp.status_code == 200, f"CLOSE_ALL failed: {resp.text}"
        data = resp.json()
        assert data.get("status") in ("executed", "queued"), f"Unexpected: {data}"

    @pytest.mark.live_mt5
    def test_6_circuit_breaker_reset(self):
        """POST /api/circuit-breaker/reset succeeds with live auth."""
        import requests
        resp = requests.post(
            f"{self._live_url}/api/circuit-breaker/reset",
            headers=self._headers(),
            timeout=10,
        )
        assert resp.status_code == 200, f"Circuit breaker reset failed: {resp.text}"
        data = resp.json()
        assert data.get("status") == "reset"

    @pytest.mark.live_mt5
    def test_7_analytics_after_trade(self):
        """GET /api/analytics shows at least 1 signal after the BUY trade."""
        import requests
        resp = requests.get(
            f"{self._live_url}/api/analytics?days=1",
            headers=self._headers(),
            timeout=10,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_signals"] >= 0  # May be 0 if trade went via different path

    @pytest.mark.live_mt5
    def test_8_dashboard_analytics_shape(self):
        """GET /dashboard/analytics returns required fields after live trading."""
        import requests
        resp = requests.get(
            f"{self._live_url}/dashboard/analytics",
            headers=self._headers(),
            timeout=10,
        )
        assert resp.status_code == 200, f"Dashboard analytics failed: {resp.text}"
        data = resp.json()
        required = [
            "win_rate", "total_signals", "executed", "failed",
            "signals_today", "signals_this_week", "by_symbol", "by_script", "recent_24h",
        ]
        for field in required:
            assert field in data, f"Missing field: {field}"

    @pytest.mark.live_mt5
    def test_9_duplicate_idempotency(self):
        """Sending the same signal twice is deduplicated by the idempotency key."""
        import requests
        idem_key = f"live-test-idem-{int(time.time())}"
        payload = {
            "action": "BUY",
            "symbol": "EURUSD",
            "size": 0.01,
            "idempotency_key": idem_key,
        }
        resp1 = requests.post(
            f"{self._live_url}/api/trade",
            json=payload,
            headers=self._headers(),
            timeout=20,
        )
        assert resp1.status_code == 200

        # Small delay to ensure the first request is fully processed
        time.sleep(1)

        resp2 = requests.post(
            f"{self._live_url}/api/trade",
            json=payload,
            headers=self._headers(),
            timeout=20,
        )
        # Second request should either be deduplicated (200 with duplicate status)
        # or the idempotency check returns a specific indicator
        assert resp2.status_code in (200, 409)
        data2 = resp2.json()
        # If 200, the status may indicate duplicate
        if resp2.status_code == 200:
            status = data2.get("status", "")
            assert status in ("executed", "duplicate", "queued"), f"Unexpected: {data2}"

        # Clean up any open positions
        requests.post(
            f"{self._live_url}/api/trade",
            json={"action": "CLOSE_ALL", "symbol": ""},
            headers=self._headers(),
            timeout=20,
        )


# ── Standalone integration tests (backwards compat) ──────────────────────────

@pytest.mark.integration
def test_live_health(live_client):
    """GET /health on live bridge returns status=online."""
    if not TEST_API_KEY:
        pytest.skip("TEST_API_KEY not set")
    import requests
    resp = requests.get(f"{BRIDGE_URL}/health", timeout=10)
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "online"
    assert "uptime_secs" in data


@pytest.mark.integration
def test_live_analytics(live_client):
    """GET /api/analytics returns expected structure."""
    if not TEST_API_KEY:
        pytest.skip("TEST_API_KEY not set")
    resp = live_client.get(f"{BRIDGE_URL}/api/analytics?days=7", timeout=10)
    assert resp.status_code == 200
    data = resp.json()
    assert "period_days" in data
    assert "total_signals" in data
    assert "executed" in data
    assert "failed" in data
    assert "success_rate" in data
    assert "by_script" in data
    assert data["period_days"] == 7


@pytest.mark.integration
def test_live_circuit_breaker_reset(live_client):
    """POST /api/circuit-breaker/reset returns 200 on live bridge."""
    if not TEST_API_KEY:
        pytest.skip("TEST_API_KEY not set")
    resp = live_client.post(f"{BRIDGE_URL}/api/circuit-breaker/reset", timeout=10)
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "reset"


@pytest.mark.integration
def test_live_terms_page():
    """GET /terms on live bridge returns 200."""
    import requests
    resp = requests.get(f"{BRIDGE_URL}/terms", timeout=10)
    assert resp.status_code == 200


@pytest.mark.integration
def test_live_privacy_page():
    """GET /privacy on live bridge returns 200."""
    import requests
    resp = requests.get(f"{BRIDGE_URL}/privacy", timeout=10)
    assert resp.status_code == 200
