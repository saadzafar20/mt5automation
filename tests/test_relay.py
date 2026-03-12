"""Tests for relay client and executor."""

import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from relay import RelayClient, MT5Executor, map_mt5_retcode


class TestRelayClient(unittest.TestCase):
    """Test RelayClient HTTP operations."""

    def setUp(self):
        self.client = RelayClient("http://localhost:5001", "testuser")

    def test_init_defaults(self):
        """Test client initialization."""
        self.assertEqual(self.client.bridge_url, "http://localhost:5001")
        self.assertEqual(self.client.user_id, "testuser")
        self.assertTrue(self.client.relay_id.startswith("relay-"))
        self.assertIsNone(self.client.token)

    def test_init_custom_relay_id(self):
        """Test client with custom relay ID."""
        client = RelayClient("http://test", "user", relay_id="my-relay")
        self.assertEqual(client.relay_id, "my-relay")

    @patch("relay.requests.Session.post")
    def test_login_success(self, mock_post):
        """Test successful login."""
        mock_post.return_value = MagicMock(
            status_code=200,
            json=lambda: {
                "token": "abc123xyz",
                "heartbeat_interval": 15,
                "poll_timeout": 30,
                "relay_id": "relay-assigned",
            }
        )
        
        result = self.client.login("password123")
        
        self.assertTrue(result)
        self.assertEqual(self.client.token, "abc123xyz")
        self.assertEqual(self.client.heartbeat_interval, 15)
        self.assertEqual(self.client.poll_timeout, 30)

    @patch("relay.requests.Session.post")
    def test_login_failure(self, mock_post):
        """Test failed login."""
        mock_post.return_value = MagicMock(
            status_code=401,
            text="invalid credentials"
        )
        
        result = self.client.login("wrongpass")
        
        self.assertFalse(result)
        self.assertIsNone(self.client.token)

    @patch("relay.requests.Session.post")
    def test_heartbeat(self, mock_post):
        """Test heartbeat sending."""
        self.client.token = "valid-token"
        mock_post.return_value = MagicMock(status_code=200)
        
        result = self.client.heartbeat({"version": "1.0"})
        
        self.assertTrue(result)
        mock_post.assert_called_once()

    @patch("relay.requests.Session.post")
    def test_poll(self, mock_post):
        """Test command polling."""
        self.client.token = "valid-token"
        mock_post.return_value = MagicMock(
            status_code=200,
            json=lambda: {
                "commands": [
                    {"id": "cmd-1", "action": "BUY", "symbol": "EURUSD", "size": 0.1}
                ]
            }
        )
        
        commands = self.client.poll()
        
        self.assertEqual(len(commands), 1)
        self.assertEqual(commands[0]["action"], "BUY")

    @patch("relay.requests.Session.post")
    def test_report_result(self, mock_post):
        """Test result reporting."""
        self.client.token = "valid-token"
        mock_post.return_value = MagicMock(status_code=200)
        
        result = self.client.report_result("cmd-1", "executed", {"order_id": 123})
        
        self.assertTrue(result)


class TestMT5Executor(unittest.TestCase):
    """Test MT5Executor trade execution."""

    def setUp(self):
        # Create temp config
        self.config_fd, self.config_path = tempfile.mkstemp(suffix=".json")
        with os.fdopen(self.config_fd, "w") as f:
            json.dump({
                "mt5": {
                    "login": 12345,
                    "password": "pass",
                    "server": "Demo",
                }
            }, f)

    def tearDown(self):
        os.unlink(self.config_path)

    def test_mock_mode_when_mt5_unavailable(self):
        """Test executor runs in mock mode without MT5."""
        executor = MT5Executor(self.config_path)
        
        # Should be in mock mode (MT5 not available in test env)
        self.assertFalse(executor.mt5_connected)
        
        # Execute should return mock result
        result = executor.execute_command({
            "action": "BUY",
            "symbol": "EURUSD",
            "size": 0.1,
        })
        
        self.assertEqual(result["status"], "executed")
        self.assertEqual(result["mode"], "mock")

    def test_missing_symbol_error(self):
        """Test execution fails without symbol."""
        executor = MT5Executor(self.config_path)
        
        result = executor.execute_command({
            "action": "BUY",
            "size": 0.1,
        })
        
        self.assertEqual(result["status"], "failed")
        self.assertIn("missing symbol", result["error"])


class TestMapMT5Retcode(unittest.TestCase):
    """Test MT5 return code mapping."""

    def test_known_codes(self):
        """Test known error codes are mapped."""
        self.assertIn("stop loss", map_mt5_retcode(10016).lower())
        self.assertIn("closed", map_mt5_retcode(10018).lower())
        self.assertIn("money", map_mt5_retcode(10019).lower())

    def test_unknown_code(self):
        """Test unknown codes return generic message."""
        msg = map_mt5_retcode(99999)
        self.assertIn("99999", msg)

    def test_none_code(self):
        """Test None returns generic failure."""
        msg = map_mt5_retcode(None)
        self.assertIn("failed", msg.lower())


if __name__ == "__main__":
    unittest.main()
