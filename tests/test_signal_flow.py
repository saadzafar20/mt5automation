"""Integration tests for signal flow: TradingView -> Bridge -> Relay/Managed."""

import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Set required env vars BEFORE importing cloud_bridge
os.environ["BRIDGE_AUTH_SALT"] = "test-salt"
os.environ["BRIDGE_SESSION_SECRET"] = "test-session"
os.environ["BRIDGE_CREDS_KEY"] = "test-creds-key-32-chars-long-ok"
os.environ["BRIDGE_REQUIRE_API_KEY"] = "true"
os.environ["CLOUD_BRIDGE_DEBUG"] = "true"

# Use a different DB path to avoid conflicting with running server
_test_db_fd, _test_db_path = tempfile.mkstemp(suffix="_signal_test.db")
os.environ["BRIDGE_DB_PATH"] = _test_db_path

from cloud_bridge import app, store, BridgeStore, Command, CommandStatus


class TestSignalEndpoint(unittest.TestCase):
    """Test /signal endpoint."""

    @classmethod
    def setUpClass(cls):
        """Set up test client and temp database."""
        cls.db_path = _test_db_path
        
        # Replace global store with test store
        import cloud_bridge
        cloud_bridge.store = BridgeStore(cls.db_path)
        cls.store = cloud_bridge.store
        
        cls.client = app.test_client()
        app.config["TESTING"] = True

    @classmethod
    def tearDownClass(cls):
        """Clean up - don't close connection as other test classes share it."""
        pass

    def setUp(self):
        """Create test user for each test."""
        self.user_id = f"testuser-{id(self)}"
        self.api_key = self.store.register_dashboard_user(self.user_id, "password123")
        self.store.register_relay(self.user_id, "relay-1", "self-hosted")
        self.store.heartbeat(self.user_id, "relay-1")  # Mark online

    def test_signal_missing_user_id(self):
        """Test signal without user_id returns 400."""
        resp = self.client.post("/signal", json={
            "action": "BUY",
            "symbol": "EURUSD",
            "size": 0.1
        })
        self.assertEqual(resp.status_code, 400)
        self.assertIn("user_id", resp.get_json()["error"])

    def test_signal_missing_api_key(self):
        """Test signal without api_key returns 401."""
        resp = self.client.post("/signal", json={
            "user_id": self.user_id,
            "action": "BUY",
            "symbol": "EURUSD",
        })
        self.assertEqual(resp.status_code, 401)

    def test_signal_invalid_api_key(self):
        """Test signal with wrong api_key returns 401."""
        resp = self.client.post("/signal", json={
            "user_id": self.user_id,
            "api_key": "wrong-key",
            "action": "BUY",
            "symbol": "EURUSD",
        })
        self.assertEqual(resp.status_code, 401)

    def test_signal_valid_queued(self):
        """Test valid signal queues command."""
        resp = self.client.post("/signal", json={
            "user_id": self.user_id,
            "api_key": self.api_key,
            "action": "BUY",
            "symbol": "EURUSD",
            "size": 0.1,
            "script_name": "TestScript",
        })
        self.assertEqual(resp.status_code, 202)
        data = resp.get_json()
        self.assertEqual(data["status"], "queued")
        self.assertIn("command_id", data)
        self.assertEqual(data["relay_id"], "relay-1")

    def test_signal_via_webhook_token(self):
        """Test signal via unique webhook URL."""
        token = self.store.get_or_create_webhook_token(self.user_id)
        resp = self.client.post(f"/signal/{token}", json={
            "action": "SELL",
            "symbol": "GBPUSD",
            "size": 0.05,
        })
        self.assertEqual(resp.status_code, 202)

    def test_signal_invalid_webhook_token(self):
        """Test invalid webhook token returns 404."""
        resp = self.client.post("/signal/invalid-token", json={
            "action": "BUY",
            "symbol": "EURUSD",
        })
        self.assertEqual(resp.status_code, 404)

    def test_signal_missing_action_or_symbol(self):
        """Test signal without action or symbol returns 400."""
        resp = self.client.post("/signal", json={
            "user_id": self.user_id,
            "api_key": self.api_key,
            "action": "BUY",
            # missing symbol
        })
        self.assertEqual(resp.status_code, 400)

    def test_signal_max_lot_size_exceeded(self):
        """Test signal exceeding max lot size is rejected."""
        # Set max lot size to 0.1
        self.store.update_user_settings(self.user_id, {"max_lot_size": 0.1})
        
        resp = self.client.post("/signal", json={
            "user_id": self.user_id,
            "api_key": self.api_key,
            "action": "BUY",
            "symbol": "EURUSD",
            "size": 1.0,  # exceeds 0.1
        })
        self.assertEqual(resp.status_code, 400)
        self.assertIn("max lot size", resp.get_json()["error"])

    def test_signal_rate_limit(self):
        """Test rate limiting blocks excessive trades."""
        # Set very low rate limit
        self.store.update_user_settings(self.user_id, {
            "rate_limit_max_trades": 2,
            "rate_limit_window_secs": 60,
        })
        
        # First two should succeed
        for i in range(2):
            resp = self.client.post("/signal", json={
                "user_id": self.user_id,
                "api_key": self.api_key,
                "action": "BUY",
                "symbol": "EURUSD",
                "size": 0.01,
                "script_name": "RateLimitTest",
            })
            self.assertEqual(resp.status_code, 202)
        
        # Third should be rate limited
        resp = self.client.post("/signal", json={
            "user_id": self.user_id,
            "api_key": self.api_key,
            "action": "BUY",
            "symbol": "EURUSD",
            "size": 0.01,
            "script_name": "RateLimitTest",
        })
        self.assertEqual(resp.status_code, 429)


class TestRelayPollFlow(unittest.TestCase):
    """Test relay poll and result reporting."""

    @classmethod
    def setUpClass(cls):
        # Reuse the module-level test database
        import cloud_bridge
        cls.store = cloud_bridge.store
        cls.client = app.test_client()
        app.config["TESTING"] = True

    @classmethod
    def tearDownClass(cls):
        pass  # Cleanup handled at module level

    def setUp(self):
        self.user_id = f"relayuser-{id(self)}"
        self.api_key = self.store.register_dashboard_user(self.user_id, "pass")
        self.relay_token = self.store.register_relay(self.user_id, "relay-1", "self-hosted")
        self.store.heartbeat(self.user_id, "relay-1")

    def test_relay_poll_returns_queued_commands(self):
        """Test relay poll returns queued commands."""
        # Queue a command
        cmd = Command(self.user_id, "relay-1", "BUY", "EURUSD", 0.1)
        self.store.enqueue(cmd)
        
        # Poll
        resp = self.client.post("/relay/poll?wait=0", headers={
            "X-User-ID": self.user_id,
            "X-Relay-ID": "relay-1",
            "X-Relay-Token": self.relay_token,
        }, json={})
        
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(len(data["commands"]), 1)
        self.assertEqual(data["commands"][0]["id"], cmd.id)

    def test_relay_report_result(self):
        """Test relay reports execution result."""
        cmd = Command(self.user_id, "relay-1", "BUY", "EURUSD", 0.1)
        self.store.enqueue(cmd)
        self.store.dequeue(self.user_id, "relay-1")
        
        resp = self.client.post("/relay/result", headers={
            "X-User-ID": self.user_id,
            "X-Relay-ID": "relay-1",
            "X-Relay-Token": self.relay_token,
        }, json={
            "command_id": cmd.id,
            "status": "executed",
            "result": {"order_id": 99999},
        })
        
        self.assertEqual(resp.status_code, 200)
        
        # Verify command status updated
        fetched = self.store.get_command(cmd.id)
        self.assertEqual(fetched.status, CommandStatus.EXECUTED)


class TestManagedExecution(unittest.TestCase):
    """Test managed VPS execution mode."""

    @classmethod
    def setUpClass(cls):
        # Reuse the module-level test database
        import cloud_bridge
        cls.store = cloud_bridge.store
        cls.client = app.test_client()
        app.config["TESTING"] = True

    @classmethod
    def tearDownClass(cls):
        pass  # Cleanup handled at module level

    def setUp(self):
        self.user_id = f"managed-{id(self)}"
        self.api_key = self.store.register_dashboard_user(self.user_id, "pass")

    def test_managed_setup_via_api_key(self):
        """Test managed setup endpoint."""
        resp = self.client.post("/managed/setup", headers={
            "X-User-ID": self.user_id,
            "X-API-Key": self.api_key,
        }, json={
            "mt5_login": 12345678,
            "mt5_password": "mt5pass",
            "mt5_server": "MetaQuotes-Demo",
        })
        
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(self.store.is_managed_enabled(self.user_id))

    def test_managed_setup_via_login(self):
        """Test managed setup with dashboard credentials."""
        resp = self.client.post("/managed/setup/login", json={
            "user_id": self.user_id,
            "password": "pass",
            "mt5_login": 12345678,
            "mt5_password": "mt5pass",
            "mt5_server": "MetaQuotes-Demo",
        })
        
        self.assertEqual(resp.status_code, 200)

    @patch("cloud_bridge.managed_executor")
    def test_signal_uses_managed_executor_when_enabled(self, mock_executor):
        """Test that signals use managed executor when enabled."""
        # Setup managed account
        self.store.upsert_managed_account(
            self.user_id, 12345, "pass", "MetaQuotes-Demo"
        )
        
        # Mock executor response
        mock_executor.execute.return_value = {
            "status": "executed",
            "order_id": 999,
            "mode": "managed-vps",
        }
        
        resp = self.client.post("/signal", json={
            "user_id": self.user_id,
            "api_key": self.api_key,
            "action": "BUY",
            "symbol": "EURUSD",
            "size": 0.1,
        })
        
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data["mode"], "managed-vps")
        mock_executor.execute.assert_called_once()


if __name__ == "__main__":
    unittest.main()
