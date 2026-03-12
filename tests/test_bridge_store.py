"""Tests for BridgeStore (SQLite persistence layer)."""

import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Set required env vars before importing cloud_bridge
os.environ.setdefault("BRIDGE_AUTH_SALT", "test-salt-for-unit-tests")
os.environ.setdefault("BRIDGE_SESSION_SECRET", "test-session-secret")
os.environ.setdefault("BRIDGE_CREDS_KEY", "test-encryption-key-32-chars-ok")
os.environ.setdefault("CLOUD_BRIDGE_DEBUG", "true")

# Use a different DB path to avoid conflicting with running server
_test_db_fd, _test_db_path = tempfile.mkstemp(suffix="_test.db")
os.environ["BRIDGE_DB_PATH"] = _test_db_path

from cloud_bridge import BridgeStore, Command, CommandStatus, RelayState, hash_secret


class TestBridgeStore(unittest.TestCase):
    """Test BridgeStore database operations."""

    def setUp(self):
        """Create a temp database for each test."""
        self.db_fd, self.db_path = tempfile.mkstemp(suffix=".db")
        self.store = BridgeStore(self.db_path)

    def tearDown(self):
        """Clean up temp database."""
        self.store.conn.close()
        os.close(self.db_fd)
        os.unlink(self.db_path)

    def test_upsert_user(self):
        """Test user creation and API key hashing."""
        self.store.upsert_user("alice", "secret-key-123")
        self.assertTrue(self.store.user_exists("alice"))
        self.assertFalse(self.store.user_exists("bob"))

    def test_verify_api_key(self):
        """Test API key verification."""
        self.store.upsert_user("alice", "my-api-key")
        self.assertTrue(self.store.verify_api_key("alice", "my-api-key"))
        self.assertFalse(self.store.verify_api_key("alice", "wrong-key"))
        self.assertFalse(self.store.verify_api_key("nonexistent", "any-key"))

    def test_register_dashboard_user(self):
        """Test dashboard user registration with password."""
        api_key = self.store.register_dashboard_user("bob", "password123")
        self.assertIsInstance(api_key, str)
        self.assertTrue(len(api_key) > 10)
        self.assertTrue(self.store.user_exists("bob"))
        self.assertTrue(self.store.verify_dashboard_login("bob", "password123"))
        self.assertFalse(self.store.verify_dashboard_login("bob", "wrongpass"))

    def test_duplicate_user_registration(self):
        """Test that duplicate registration raises error."""
        self.store.register_dashboard_user("charlie", "pass1")
        with self.assertRaises(ValueError):
            self.store.register_dashboard_user("charlie", "pass2")

    def test_webhook_token(self):
        """Test webhook token generation and lookup."""
        self.store.upsert_user("alice", "key")
        token = self.store.get_or_create_webhook_token("alice")
        self.assertIsInstance(token, str)
        self.assertTrue(len(token) > 10)
        
        # Same token on subsequent calls
        token2 = self.store.get_or_create_webhook_token("alice")
        self.assertEqual(token, token2)
        
        # Lookup by token
        user_id = self.store.get_user_id_by_webhook_token(token)
        self.assertEqual(user_id, "alice")
        
        # Invalid token
        self.assertIsNone(self.store.get_user_id_by_webhook_token("invalid"))

    def test_relay_registration(self):
        """Test relay registration and token verification."""
        self.store.upsert_user("alice", "key")
        token = self.store.register_relay("alice", "relay-1", "self-hosted")
        
        self.assertIsInstance(token, str)
        self.assertTrue(self.store.verify_relay_token("alice", "relay-1", token))
        self.assertFalse(self.store.verify_relay_token("alice", "relay-1", "wrong"))
        self.assertFalse(self.store.verify_relay_token("alice", "relay-2", token))

    def test_heartbeat(self):
        """Test relay heartbeat updates state."""
        self.store.upsert_user("alice", "key")
        self.store.register_relay("alice", "relay-1", "self-hosted")
        
        # Initial state should be unknown/offline
        relays = self.store.list_relays("alice")
        self.assertIn("relay-1", relays)
        
        # Send heartbeat
        result = self.store.heartbeat("alice", "relay-1", {"version": "1.0"})
        self.assertTrue(result)
        
        # Check state is online
        relays = self.store.list_relays("alice")
        self.assertEqual(relays["relay-1"]["state"], RelayState.ONLINE.value)

    def test_command_enqueue_dequeue(self):
        """Test command queue operations."""
        self.store.upsert_user("alice", "key")
        self.store.register_relay("alice", "relay-1", "self-hosted")
        
        # Enqueue command
        cmd = Command("alice", "relay-1", "BUY", "EURUSD", 0.1, sl=1.0900, tp=1.1100)
        self.store.enqueue(cmd)
        
        # Verify command exists
        fetched = self.store.get_command(cmd.id)
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched.action, "BUY")
        self.assertEqual(fetched.symbol, "EURUSD")
        self.assertEqual(fetched.status, CommandStatus.QUEUED)
        
        # Dequeue
        commands = self.store.dequeue("alice", "relay-1", limit=10)
        self.assertEqual(len(commands), 1)
        self.assertEqual(commands[0].id, cmd.id)
        self.assertEqual(commands[0].status, CommandStatus.DELIVERED)
        
        # Second dequeue should be empty
        commands2 = self.store.dequeue("alice", "relay-1", limit=10)
        self.assertEqual(len(commands2), 0)

    def test_command_result_update(self):
        """Test updating command execution result."""
        self.store.upsert_user("alice", "key")
        self.store.register_relay("alice", "relay-1", "self-hosted")
        
        cmd = Command("alice", "relay-1", "BUY", "EURUSD", 0.1)
        self.store.enqueue(cmd)
        self.store.dequeue("alice", "relay-1")
        
        # Update result
        result = self.store.update_result(
            "alice", "relay-1", cmd.id,
            CommandStatus.EXECUTED,
            {"order_id": 12345}
        )
        self.assertTrue(result)
        
        # Verify
        fetched = self.store.get_command(cmd.id)
        self.assertEqual(fetched.status, CommandStatus.EXECUTED)
        self.assertEqual(fetched.result["order_id"], 12345)

    def test_user_settings(self):
        """Test user settings CRUD."""
        self.store.upsert_user("alice", "key")
        
        # Default settings
        settings = self.store.get_user_settings("alice")
        self.assertEqual(settings["max_lot_size"], 0.5)
        self.assertEqual(settings["rate_limit_max_trades"], 5)
        
        # Update
        self.store.update_user_settings("alice", {
            "max_lot_size": 1.0,
            "notifications_enabled": 1,
            "telegram_chat_id": "12345",
        })
        
        settings = self.store.get_user_settings("alice")
        self.assertEqual(settings["max_lot_size"], 1.0)
        self.assertEqual(settings["notifications_enabled"], 1)
        self.assertEqual(settings["telegram_chat_id"], "12345")

    def test_script_management(self):
        """Test script catalog and user assignments."""
        self.store.upsert_user("alice", "key")
        
        # Create script
        self.store.upsert_script("gold-scalper", "Gold Scalper Pro")
        self.assertTrue(self.store.script_exists("gold-scalper"))
        
        # Assign to user
        self.store.assign_script_to_user("alice", "gold-scalper")
        scripts = self.store.get_user_scripts("alice")
        self.assertEqual(len(scripts), 1)
        self.assertEqual(scripts[0]["script_code"], "gold-scalper")

    def test_rate_limit_counting(self):
        """Test counting recent commands for rate limiting."""
        self.store.upsert_user("alice", "key")
        self.store.register_relay("alice", "relay-1", "self-hosted")
        
        # Enqueue multiple commands
        for i in range(3):
            cmd = Command("alice", "relay-1", "BUY", "EURUSD", 0.1, script_name="TestScript")
            self.store.enqueue(cmd)
        
        count = self.store.count_recent_script_commands("alice", "TestScript", window_secs=60)
        self.assertEqual(count, 3)


class TestHashSecret(unittest.TestCase):
    """Test cryptographic helpers."""

    def test_hash_deterministic(self):
        """Test hash is deterministic."""
        h1 = hash_secret("user1", "secret")
        h2 = hash_secret("user1", "secret")
        self.assertEqual(h1, h2)

    def test_hash_different_inputs(self):
        """Test different inputs produce different hashes."""
        h1 = hash_secret("user1", "secret")
        h2 = hash_secret("user2", "secret")
        h3 = hash_secret("user1", "different")
        self.assertNotEqual(h1, h2)
        self.assertNotEqual(h1, h3)


if __name__ == "__main__":
    unittest.main()
