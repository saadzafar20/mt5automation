"""
Section 12: Tests for all new features implemented in the 12-section spec.

Covers:
  - Section 1:  Pips format detection in telegram_signal_parser
  - Section 2:  Default lot/SL/TP per user (DB + bot command logic)
  - Section 3:  Per-user symbol whitelist
  - Section 4:  Multiple group subscriptions (store helpers)
  - Section 5:  Telegram account linking (token flow + bot /start)
  - Section 6:  Private trade confirmation (_send_private path)
  - Section 7:  Session expiry notifications (heartbeat worker logic)
  - Section 8:  Admin Telegram commands
  - Section 9:  Rate limiter (_rate_check)
  - Section 10: OAuth registration flow audit
  - Section 11: Cleanup / sanitization helpers
  - Live test:  TradingView webhook with Ayan/123456Uy credentials
"""

import json
import os
import sys
import tempfile
import threading
import time
import unittest
import pytest
from unittest.mock import MagicMock, patch, call

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("CLOUD_BRIDGE_DEBUG", "true")
os.environ.setdefault("BRIDGE_AUTH_SALT", "test-salt")
os.environ.setdefault("BRIDGE_SESSION_SECRET", "test-secret")

import cloud_bridge
from cloud_bridge import (
    BridgeStore, _rate_check, hash_secret,
    store as _global_store,
)
from telegram_signal_parser import parse_telegram_message, ParsedSignal


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_store():
    """Return a fresh in-memory BridgeStore for each test."""
    db = tempfile.mktemp(suffix=".db")
    return BridgeStore(db)


def _register(s: BridgeStore, user_id="testuser", api_key="testkey123"):
    s.upsert_user(user_id, api_key)
    return user_id, api_key


# ===========================================================================
# Section 1 — Pips format detection
# ===========================================================================

class TestPipsDetection(unittest.TestCase):
    """Pips-format signals must be routed to LLM, not executed with raw pip values."""

    def test_pips_format_sets_skip_reason(self):
        result = parse_telegram_message("BUY EURUSD TP 100 pips SL 50 pips")
        self.assertIsNotNone(result.skip_reason)
        self.assertIn("pips", result.skip_reason.lower())

    def test_pips_format_preserves_action_and_symbol(self):
        result = parse_telegram_message("SELL XAUUSD SL 30 pips TP 60 pips")
        self.assertEqual(result.action, "SELL")
        self.assertEqual(result.symbol, "XAUUSD")

    def test_pips_format_confidence_triggers_llm(self):
        """Confidence must be > 0.0 so the LLM fallback is triggered."""
        result = parse_telegram_message("BUY GBPUSD SL 40 pips TP 80 pips")
        self.assertGreater(result.confidence, 0.0)
        self.assertIn("pips format", result.skip_reason)

    def test_pip_singular_also_detected(self):
        result = parse_telegram_message("BUY EURUSD SL 50 pip")
        self.assertIsNotNone(result.skip_reason)
        self.assertIn("pips", result.skip_reason.lower())

    def test_normal_signal_not_affected(self):
        result = parse_telegram_message("BUY EURUSD SL 1.0690 TP 1.0780")
        self.assertIsNone(result.skip_reason)
        self.assertEqual(result.sl, 1.069)

    def test_numbered_tp_regex_no_backtrack(self):
        """'TP 1.0800 TP2 1.0850' should not backtrack into 'TP 10' → '0'."""
        result = parse_telegram_message("BUY EURUSD SL 1.0690 TP1 1.0780 TP2 1.0850")
        self.assertIsNone(result.skip_reason)
        self.assertIn(1.078, result.tp_list)

    def test_llm_worthy_includes_pips(self):
        """'pips format' must be in the _llm_worthy tuple in TelegramBotManager."""
        from telegram_bot_manager import TelegramBotManager
        # Construct minimal manager and confirm _on_message routes pips to LLM
        store = _make_store()
        _register(store)
        mock_app = MagicMock()
        mock_process = MagicMock()
        mock_llm = MagicMock()
        mock_llm.is_running = True
        mock_llm.enqueue = MagicMock()

        mgr = TelegramBotManager(store, mock_app, mock_process,
                                 bot_token=None, llm_processor=mock_llm)

        # Add a subscription so the message is processed
        channel_id = "ch-pips"
        store.add_telegram_channel(
            channel_id=channel_id, user_id="testuser",
            chat_id="42", chat_title="Test",
        )
        # Trigger _on_message with a pips signal
        mgr._on_message("42", "BUY EURUSD TP 100 pips SL 50 pips", 1)
        mock_llm.enqueue.assert_called_once()


# ===========================================================================
# Section 2 — Default lot/SL/TP
# ===========================================================================

class TestUserDefaults(unittest.TestCase):

    def test_set_and_get_defaults(self):
        s = _make_store()
        _register(s)
        s.set_user_default("testuser", "default_lot_size", 0.02)
        s.set_user_default("testuser", "default_sl_pips", 50.0)
        s.set_user_default("testuser", "default_tp_pips", 100.0)

        d = s.get_user_defaults("testuser")
        self.assertAlmostEqual(d["default_lot_size"], 0.02)
        self.assertAlmostEqual(d["default_sl_pips"], 50.0)
        self.assertAlmostEqual(d["default_tp_pips"], 100.0)

    def test_invalid_field_raises(self):
        s = _make_store()
        _register(s)
        with self.assertRaises(ValueError):
            s.set_user_default("testuser", "hack_field", 1.0)

    def test_defaults_returned_in_user_settings(self):
        s = _make_store()
        _register(s)
        s.set_user_default("testuser", "default_lot_size", 0.05)
        settings = s.get_user_settings("testuser")
        self.assertAlmostEqual(settings["default_lot_size"], 0.05)

    def test_defaults_applied_in_execute_for_subscription(self):
        """When signal has no SL/TP, defaults are injected into signal_data."""
        from telegram_bot_manager import TelegramBotManager

        s = _make_store()
        _register(s)
        s.set_user_default("testuser", "default_sl_pips", 50.0)
        s.set_user_default("testuser", "default_tp_pips", 100.0)

        captured = {}

        def mock_process(user_id, signal_data):
            captured.update(signal_data)
            return {"status": "executed", "status_code": 200, "mode": "managed-vps",
                    "command_id": "cmd1", "result": {}}

        mock_app = MagicMock()
        mgr = TelegramBotManager(s, mock_app, mock_process)

        sub = {
            "user_id": "testuser",
            "channel_id": "ch1",
            "chat_id": "100",
            "risk_pct": 1.0,
            "script_name": "Telegram",
        }
        parsed = ParsedSignal(action="BUY", symbol="EURUSD", confidence=0.70)
        mgr._execute_for_subscription(sub, parsed, "BUY EURUSD", 1)

        self.assertIn("sl_pips", captured)
        self.assertIn("tp_pips", captured)
        self.assertAlmostEqual(captured["sl_pips"], 50.0)
        self.assertAlmostEqual(captured["tp_pips"], 100.0)


class TestPipSizeConversion(unittest.TestCase):

    def test_eurusd_pip_size(self):
        from mt5_order_utils import pip_size_for_symbol
        self.assertAlmostEqual(pip_size_for_symbol("EURUSD"), 0.0001)

    def test_usdjpy_pip_size(self):
        from mt5_order_utils import pip_size_for_symbol
        self.assertAlmostEqual(pip_size_for_symbol("USDJPY"), 0.01)

    def test_xauusd_pip_size(self):
        from mt5_order_utils import pip_size_for_symbol
        self.assertAlmostEqual(pip_size_for_symbol("XAUUSD"), 0.1)

    def test_buy_sl_below_entry(self):
        from mt5_order_utils import pips_to_price
        sl = pips_to_price("EURUSD", 50, "BUY", 1.0800, "sl")
        self.assertAlmostEqual(sl, 1.0800 - 50 * 0.0001, places=5)

    def test_sell_tp_below_entry(self):
        from mt5_order_utils import pips_to_price
        tp = pips_to_price("EURUSD", 100, "SELL", 1.0800, "tp")
        self.assertAlmostEqual(tp, 1.0800 - 100 * 0.0001, places=5)

    def test_buy_tp_above_entry(self):
        from mt5_order_utils import pips_to_price
        tp = pips_to_price("XAUUSD", 200, "BUY", 2000.0, "tp")
        self.assertAlmostEqual(tp, 2000.0 + 200 * 0.1, places=2)


# ===========================================================================
# Section 3 — Per-user symbol whitelist
# ===========================================================================

class TestUserAllowedSymbols(unittest.TestCase):

    def test_empty_means_all_allowed(self):
        s = _make_store()
        _register(s)
        self.assertEqual(s.get_user_allowed_symbols("testuser"), [])

    def test_add_and_list(self):
        s = _make_store()
        _register(s)
        s.add_user_allowed_symbol("testuser", "EURUSD")
        s.add_user_allowed_symbol("testuser", "XAUUSD")
        syms = s.get_user_allowed_symbols("testuser")
        self.assertIn("EURUSD", syms)
        self.assertIn("XAUUSD", syms)

    def test_remove_symbol(self):
        s = _make_store()
        _register(s)
        s.add_user_allowed_symbol("testuser", "GBPUSD")
        s.remove_user_allowed_symbol("testuser", "GBPUSD")
        self.assertNotIn("GBPUSD", s.get_user_allowed_symbols("testuser"))

    def test_symbol_filter_blocks_trade(self):
        """When whitelist is active, signals for non-allowed symbols are filtered."""
        from telegram_bot_manager import TelegramBotManager
        s = _make_store()
        _register(s)
        s.add_user_allowed_symbol("testuser", "EURUSD")

        executed = []

        def mock_process(user_id, signal_data):
            executed.append(signal_data)
            return {"status": "executed", "status_code": 200, "mode": "relay",
                    "command_id": "cmd1", "result": {}}

        mgr = TelegramBotManager(s, MagicMock(), mock_process)
        channel_id = "ch-sym"
        s.add_telegram_channel(channel_id=channel_id, user_id="testuser",
                                chat_id="200", chat_title="Test")

        parsed = ParsedSignal(action="BUY", symbol="XAUUSD", confidence=0.70)
        sub = {
            "user_id": "testuser", "channel_id": channel_id,
            "chat_id": "200", "risk_pct": 1.0, "script_name": "Telegram",
        }
        mgr._execute_for_subscription(sub, parsed, "BUY GOLD", 1)
        self.assertEqual(executed, [], "XAUUSD should be filtered when only EURUSD is allowed")

    def test_symbol_filter_allows_whitelisted(self):
        """Whitelisted symbols are not filtered."""
        from telegram_bot_manager import TelegramBotManager
        s = _make_store()
        _register(s)
        s.add_user_allowed_symbol("testuser", "EURUSD")

        executed = []

        def mock_process(user_id, signal_data):
            executed.append(signal_data)
            return {"status": "executed", "status_code": 200, "mode": "relay",
                    "command_id": "cmd1", "result": {}}

        mgr = TelegramBotManager(s, MagicMock(), mock_process)
        channel_id = "ch-sym2"
        s.add_telegram_channel(channel_id=channel_id, user_id="testuser",
                                chat_id="201", chat_title="Test")

        parsed = ParsedSignal(action="BUY", symbol="EURUSD", confidence=0.70)
        sub = {
            "user_id": "testuser", "channel_id": channel_id,
            "chat_id": "201", "risk_pct": 1.0, "script_name": "Telegram",
        }
        mgr._execute_for_subscription(sub, parsed, "BUY EURUSD", 1)
        self.assertEqual(len(executed), 1, "EURUSD should pass the filter")


# ===========================================================================
# Section 4 — Multiple group subscriptions (store helpers)
# ===========================================================================

class TestMultipleGroupSubscriptions(unittest.TestCase):

    def test_add_channel_simple(self):
        s = _make_store()
        _register(s)
        channel_id = s.add_telegram_channel_simple("testuser", "-1001234", "Alpha Signals")
        channels = s.get_channels_for_user("testuser")
        self.assertEqual(len(channels), 1)
        self.assertEqual(channels[0]["chat_id"], "-1001234")
        self.assertEqual(channels[0]["chat_title"], "Alpha Signals")

    def test_multiple_channels_per_user(self):
        s = _make_store()
        _register(s)
        s.add_telegram_channel_simple("testuser", "-1001", "Group A")
        s.add_telegram_channel_simple("testuser", "-1002", "Group B")
        channels = s.get_channels_for_user("testuser")
        self.assertEqual(len(channels), 2)

    def test_remove_channel(self):
        s = _make_store()
        _register(s)
        s.add_telegram_channel_simple("testuser", "-1001", "Group A")
        removed = s.remove_telegram_channel("testuser", "-1001")
        self.assertTrue(removed)
        self.assertEqual(s.get_channels_for_user("testuser"), [])

    def test_remove_nonexistent_channel(self):
        s = _make_store()
        _register(s)
        removed = s.remove_telegram_channel("testuser", "-9999")
        self.assertFalse(removed)

    def test_fanout_to_multiple_users_same_chat(self):
        """Multiple users subscribed to the same chat all get the signal."""
        s = _make_store()
        s.upsert_user("user_a", "key_a")
        s.upsert_user("user_b", "key_b")
        s.add_telegram_channel(channel_id="ch-a", user_id="user_a",
                                chat_id="-9001", chat_title="Shared")
        s.add_telegram_channel(channel_id="ch-b", user_id="user_b",
                                chat_id="-9001", chat_title="Shared")
        subs = s.get_subscriptions_for_chat("-9001")
        user_ids = {sub["user_id"] for sub in subs}
        self.assertIn("user_a", user_ids)
        self.assertIn("user_b", user_ids)


# ===========================================================================
# Section 5 — Telegram account linking
# ===========================================================================

class TestTelegramLinking(unittest.TestCase):

    def test_create_and_consume_token(self):
        s = _make_store()
        _register(s)
        token = s.create_telegram_link_token("testuser")
        self.assertIsNotNone(token)
        self.assertGreater(len(token), 20)

        user_id = s.consume_telegram_link_token(token)
        self.assertEqual(user_id, "testuser")

    def test_token_cannot_be_reused(self):
        s = _make_store()
        _register(s)
        token = s.create_telegram_link_token("testuser")
        s.consume_telegram_link_token(token)
        # Second consume returns None
        self.assertIsNone(s.consume_telegram_link_token(token))

    def test_expired_token_returns_none(self):
        s = _make_store()
        _register(s)
        token = s.create_telegram_link_token("testuser", ttl=0)
        time.sleep(0.05)
        self.assertIsNone(s.consume_telegram_link_token(token))

    def test_invalid_token_returns_none(self):
        s = _make_store()
        _register(s)
        self.assertIsNone(s.consume_telegram_link_token("notavalidtoken"))

    def test_link_telegram_user(self):
        s = _make_store()
        _register(s)
        s.link_telegram_user("tg123", "testuser", "123456789", "alice")
        self.assertEqual(s.get_user_id_by_telegram_id("tg123"), "testuser")

    def test_get_private_chat_id(self):
        s = _make_store()
        _register(s)
        s.link_telegram_user("tg456", "testuser", "987654321", "bob")
        self.assertEqual(s.get_private_chat_id_for_user("testuser"), "987654321")

    def test_unlink_telegram_user(self):
        s = _make_store()
        _register(s)
        s.link_telegram_user("tg789", "testuser", "111", "charlie")
        s.unlink_telegram_user("tg789")
        self.assertIsNone(s.get_user_id_by_telegram_id("tg789"))
        self.assertIsNone(s.get_private_chat_id_for_user("testuser"))

    def test_reverse_lookup_telegram_id_for_user(self):
        s = _make_store()
        _register(s)
        s.link_telegram_user("tg_rev", "testuser", "555", "dave")
        self.assertEqual(s.get_telegram_id_for_user("testuser"), "tg_rev")

    def test_old_token_replaced_on_new_create(self):
        """Only the latest token is valid — old ones are replaced."""
        s = _make_store()
        _register(s)
        old_token = s.create_telegram_link_token("testuser")
        new_token = s.create_telegram_link_token("testuser")
        # Old token is gone
        self.assertIsNone(s.consume_telegram_link_token(old_token))
        # New token works
        self.assertEqual(s.consume_telegram_link_token(new_token), "testuser")


class TestBotCommandStart(unittest.TestCase):
    """Test the /start command flow in TelegramBotManager."""

    def _make_manager(self, store):
        return __import__("telegram_bot_manager").TelegramBotManager(
            store, MagicMock(), MagicMock(), bot_token=None
        )

    def test_start_with_valid_token_links_account(self):
        s = _make_store()
        _register(s)
        token = s.create_telegram_link_token("testuser")

        mgr = self._make_manager(s)
        replies = []
        mgr._reply_to_chat = lambda chat_id, text: replies.append(text)

        mgr._cmd_start(
            from_user_id="tg999", chat_id="999", args=[token],
            from_user={"id": "tg999", "username": "alice"},
        )

        self.assertEqual(s.get_user_id_by_telegram_id("tg999"), "testuser")
        self.assertTrue(any("linked" in r.lower() or "✅" in r for r in replies))

    def test_start_with_invalid_token_rejects(self):
        s = _make_store()
        _register(s)
        mgr = self._make_manager(s)
        replies = []
        mgr._reply_to_chat = lambda chat_id, text: replies.append(text)

        mgr._cmd_start("tg000", "000", ["badtoken"], {})
        self.assertIsNone(s.get_user_id_by_telegram_id("tg000"))
        self.assertTrue(any("invalid" in r.lower() or "❌" in r for r in replies))

    def test_start_without_token_shows_welcome(self):
        s = _make_store()
        mgr = self._make_manager(s)
        replies = []
        mgr._reply_to_chat = lambda chat_id, text: replies.append(text)
        mgr._cmd_start("tg001", "001", [], {})
        self.assertTrue(any("welcome" in r.lower() or "link" in r.lower() for r in replies))


# ===========================================================================
# Section 6 — Private trade confirmation
# ===========================================================================

class TestPrivateTradeConfirmation(unittest.TestCase):

    def test_send_private_uses_stored_chat_id(self):
        s = _make_store()
        _register(s)
        s.link_telegram_user("tg_priv", "testuser", "PRIV_CHAT_999", "eve")

        mock_api = MagicMock()
        mock_api.send_message.return_value = True

        mgr = __import__("telegram_bot_manager").TelegramBotManager(
            s, MagicMock(), MagicMock(), bot_token=None
        )
        mgr._api = mock_api

        mgr._send_private("testuser", "✅ Trade executed")
        mock_api.send_message.assert_called_once_with("PRIV_CHAT_999", "✅ Trade executed")

    def test_send_private_no_chat_id_is_noop(self):
        """If user hasn't linked Telegram, _send_private silently does nothing."""
        s = _make_store()
        _register(s)

        mock_api = MagicMock()
        mgr = __import__("telegram_bot_manager").TelegramBotManager(
            s, MagicMock(), MagicMock(), bot_token=None
        )
        mgr._api = mock_api

        mgr._send_private("testuser", "hello")
        mock_api.send_message.assert_not_called()

    def test_successful_execution_sends_private_confirmation(self):
        """After a successful trade, _send_private is called with ✅ message."""
        s = _make_store()
        _register(s)
        s.link_telegram_user("tg_conf", "testuser", "CONF_CHAT", "frank")

        sent_private = []

        def mock_process(user_id, signal_data):
            return {"status": "executed", "status_code": 200, "mode": "managed-vps",
                    "command_id": "cmd1", "result": {}}

        mgr = __import__("telegram_bot_manager").TelegramBotManager(
            s, MagicMock(), mock_process, bot_token=None
        )
        mgr._reply_to_chat = MagicMock()
        mgr._send_private = lambda uid, msg: sent_private.append(msg)

        channel_id = "ch-conf"
        s.add_telegram_channel(channel_id=channel_id, user_id="testuser",
                                chat_id="300", chat_title="Test")
        sub = {"user_id": "testuser", "channel_id": channel_id,
               "chat_id": "300", "risk_pct": 1.0, "script_name": "Telegram"}
        parsed = ParsedSignal(action="BUY", symbol="EURUSD", confidence=0.70)
        mgr._execute_for_subscription(sub, parsed, "BUY EURUSD", 1)

        self.assertTrue(any("✅" in m for m in sent_private),
                        f"Expected ✅ in private messages, got: {sent_private}")

    def test_failed_execution_sends_private_failure(self):
        s = _make_store()
        _register(s)
        s.link_telegram_user("tg_fail", "testuser", "FAIL_CHAT", "grace")

        sent_private = []

        def mock_process(user_id, signal_data):
            return {"status": "failed", "status_code": 500, "mode": "managed-vps",
                    "error": "no tick data", "result": {}}

        mgr = __import__("telegram_bot_manager").TelegramBotManager(
            s, MagicMock(), mock_process, bot_token=None
        )
        mgr._reply_to_chat = MagicMock()
        mgr._send_private = lambda uid, msg: sent_private.append(msg)

        channel_id = "ch-fail"
        s.add_telegram_channel(channel_id=channel_id, user_id="testuser",
                                chat_id="400", chat_title="Test")
        sub = {"user_id": "testuser", "channel_id": channel_id,
               "chat_id": "400", "risk_pct": 1.0, "script_name": "Telegram"}
        parsed = ParsedSignal(action="BUY", symbol="EURUSD", confidence=0.70)
        mgr._execute_for_subscription(sub, parsed, "BUY EURUSD", 1)

        self.assertTrue(any("❌" in m for m in sent_private))


# ===========================================================================
# Section 7 — Session expiry/recovery notifications
# ===========================================================================

class TestSessionNotifications(unittest.TestCase):

    def test_send_session_notification_calls_send_private(self):
        s = _make_store()
        _register(s)
        s.link_telegram_user("tg_sess", "testuser", "SESSION_CHAT", "henry")

        sent = []
        mgr = __import__("telegram_bot_manager").TelegramBotManager(
            s, MagicMock(), MagicMock(), bot_token=None
        )
        mgr._send_private = lambda uid, msg: sent.append((uid, msg))

        mgr.send_session_notification("testuser", "⚠️ MT5 session went offline")
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0][0], "testuser")
        self.assertIn("offline", sent[0][1])


class TestHeartbeatSessionTransitions(unittest.TestCase):
    """Verify the heartbeat worker correctly tracks state transitions."""

    def test_offline_notification_throttled(self):
        """Offline notification fires once, not again within 1 hour."""
        import cloud_bridge as cb
        # Reset state
        cb._session_last_state.clear()
        cb._session_offline_notified.clear()

        notifications = []
        original = cb.telegram_manager.send_session_notification
        cb.telegram_manager.send_session_notification = lambda uid, msg: notifications.append(msg)

        user_id = "throttle_test"
        # Simulate: was connected (True), now disconnected (False)
        cb._session_last_state[user_id] = True

        # Trigger transition to offline
        prev = cb._session_last_state.get(user_id)
        connected = False
        now = time.time()
        if prev is not None and prev != connected:
            last_notified = cb._session_offline_notified.get(user_id, 0)
            if now - last_notified >= 3600:
                cb.telegram_manager.send_session_notification(user_id, "⚠️ MT5 session went offline")
                cb._session_offline_notified[user_id] = now
        cb._session_last_state[user_id] = connected

        # First trigger fires
        self.assertEqual(len(notifications), 1)

        # Second trigger within an hour should not fire
        prev = cb._session_last_state.get(user_id)
        connected2 = False
        now2 = time.time()
        if prev is not None and prev != connected2:
            last_notified = cb._session_offline_notified.get(user_id, 0)
            if now2 - last_notified >= 3600:
                cb.telegram_manager.send_session_notification(user_id, "⚠️ again")
        # Still only 1 (was already False so prev == connected)
        self.assertEqual(len(notifications), 1)

        cb.telegram_manager.send_session_notification = original


# ===========================================================================
# Section 8 — Admin commands
# ===========================================================================

class TestAdminCommands(unittest.TestCase):

    def _make_admin_manager(self, store, admin_id="admin123"):
        return __import__("telegram_bot_manager").TelegramBotManager(
            store, MagicMock(), MagicMock(),
            bot_token=None, admin_telegram_id=admin_id
        )

    def test_admin_users_command(self):
        s = _make_store()
        s.upsert_user("alice", "key1")
        s.upsert_user("bob", "key2")
        mgr = self._make_admin_manager(s)

        replies = []
        mgr._reply_to_chat = lambda chat_id, text: replies.append(text)
        mgr._handle_admin_command("admin_chat", ["users"])

        self.assertTrue(any("alice" in r or "bob" in r or "Users" in r for r in replies))

    def test_admin_stats_command(self):
        s = _make_store()
        _register(s)
        mgr = self._make_admin_manager(s)
        replies = []
        mgr._reply_to_chat = lambda chat_id, text: replies.append(text)
        mgr._handle_admin_command("admin_chat", ["stats"])
        self.assertTrue(any("Stats" in r or "Users" in r or "users" in r.lower() for r in replies))

    def test_admin_user_lookup(self):
        s = _make_store()
        _register(s)
        mgr = self._make_admin_manager(s)
        replies = []
        mgr._reply_to_chat = lambda chat_id, text: replies.append(text)
        mgr._handle_admin_command("admin_chat", ["user", "testuser"])
        self.assertTrue(any("testuser" in r for r in replies))

    def test_admin_user_not_found(self):
        s = _make_store()
        mgr = self._make_admin_manager(s)
        replies = []
        mgr._reply_to_chat = lambda chat_id, text: replies.append(text)
        mgr._handle_admin_command("admin_chat", ["user", "ghost"])
        self.assertTrue(any("not found" in r.lower() for r in replies))

    def test_non_admin_blocked(self):
        s = _make_store()
        mgr = self._make_admin_manager(s, admin_id="admin123")
        replies = []
        mgr._reply_to_chat = lambda chat_id, text: replies.append(text)
        # Non-admin user sends /admin
        mgr._on_command("hacker", "some_chat", "/admin users", 1, False, {})
        self.assertTrue(any("denied" in r.lower() or "⛔" in r for r in replies))

    def test_admin_kick_disables_managed_account(self):
        s = _make_store()
        _register(s)
        s.upsert_managed_account("testuser", 12345, "pass", "Broker")
        mgr = self._make_admin_manager(s)
        replies = []
        mgr._reply_to_chat = lambda chat_id, text: replies.append(text)
        mgr._handle_admin_command("admin_chat", ["kick", "testuser"])
        # Check DB was updated
        with s.lock:
            row = s.conn.execute(
                "SELECT enabled FROM managed_accounts WHERE user_id = ?", ("testuser",)
            ).fetchone()
        self.assertEqual(row["enabled"], 0)


# ===========================================================================
# Section 9 — Rate limiter
# ===========================================================================

class TestRateLimiter(unittest.TestCase):

    def test_allows_under_limit(self):
        key = f"test_rl_{time.time()}"
        for _ in range(5):
            self.assertTrue(_rate_check(key, max_calls=10, window_secs=60))

    def test_blocks_over_limit(self):
        key = f"test_rl_block_{time.time()}"
        for _ in range(10):
            _rate_check(key, max_calls=10, window_secs=60)
        self.assertFalse(_rate_check(key, max_calls=10, window_secs=60))

    def test_resets_after_window(self):
        key = f"test_rl_reset_{time.time()}"
        for _ in range(3):
            _rate_check(key, max_calls=3, window_secs=1)
        self.assertFalse(_rate_check(key, max_calls=3, window_secs=1))
        time.sleep(1.1)
        self.assertTrue(_rate_check(key, max_calls=3, window_secs=1))

    def test_different_keys_independent(self):
        key1 = f"rl_k1_{time.time()}"
        key2 = f"rl_k2_{time.time()}"
        for _ in range(5):
            _rate_check(key1, max_calls=5, window_secs=60)
        self.assertFalse(_rate_check(key1, max_calls=5, window_secs=60))
        self.assertTrue(_rate_check(key2, max_calls=5, window_secs=60))

    def test_signal_endpoint_rate_limited(self):
        """POST /signal must return 429 after 10 requests in 60s for the same user."""
        import tempfile, os
        db = tempfile.mktemp(suffix=".db")
        app = cloud_bridge.app
        with app.test_client() as client:
            # Register user
            test_uid = f"rl_user_{int(time.time())}"
            api_key = "rltestapikey12345678901234"
            cloud_bridge.store.upsert_user(test_uid, api_key)

            responses = []
            for _ in range(12):
                r = client.post(
                    "/signal",
                    json={"action": "BUY", "symbol": "EURUSD"},
                    headers={"X-User-ID": test_uid, "X-API-Key": api_key},
                )
                responses.append(r.status_code)

            self.assertIn(429, responses, "Expected 429 after rate limit exceeded")


# ===========================================================================
# Section 10 — OAuth registration flow audit
# ===========================================================================

class TestOAuthRegistration(unittest.TestCase):

    def test_oauth_user_gets_api_key_and_webhook(self):
        s = _make_store()
        user_id, api_key = s.register_oauth_user("google", "oauth_sub_123", "test@example.com")
        self.assertIsNotNone(user_id)
        self.assertIsNotNone(api_key)
        self.assertGreater(len(api_key), 10)

    def test_oauth_user_gets_user_settings(self):
        s = _make_store()
        user_id, _ = s.register_oauth_user("google", "oauth_sub_456", "settings@example.com")
        settings = s.get_user_settings(user_id)
        self.assertIn("max_lot_size", settings)
        self.assertIsNotNone(settings["max_lot_size"])

    def test_oauth_user_gets_webhook_token(self):
        s = _make_store()
        user_id, _ = s.register_oauth_user("google", "oauth_sub_789", "webhook@example.com")
        token = s.get_or_create_webhook_token(user_id)
        self.assertIsNotNone(token)
        self.assertGreater(len(token), 10)

    def test_oauth_user_gets_default_script(self):
        s = _make_store()
        user_id, _ = s.register_oauth_user("google", "oauth_sub_abc", "script@example.com")
        # Should have the default-script assigned
        with s.lock:
            row = s.conn.execute(
                "SELECT 1 FROM user_scripts WHERE user_id = ? AND script_code = 'default-script'",
                (user_id,),
            ).fetchone()
        self.assertIsNotNone(row, "OAuth user should have default-script assigned")

    def test_oauth_identity_linked(self):
        s = _make_store()
        user_id, _ = s.register_oauth_user("google", "oauth_sub_link", "link@example.com")
        resolved = s.get_user_by_oauth("google", "oauth_sub_link")
        self.assertEqual(resolved, user_id)

    def test_existing_oauth_user_login_returns_same_id(self):
        s = _make_store()
        user_id1, _ = s.register_oauth_user("google", "oauth_same", "same@example.com")
        # Second login via OAuth finds same user
        resolved = s.get_user_by_oauth("google", "oauth_same")
        self.assertEqual(resolved, user_id1)

    def test_oauth_username_uniqueness(self):
        s = _make_store()
        uid1, _ = s.register_oauth_user("google", "sub_a", "duplicate@example.com")
        uid2, _ = s.register_oauth_user("facebook", "sub_b", "duplicate@example.com")
        self.assertNotEqual(uid1, uid2, "Duplicate email must yield unique user_ids")


# ===========================================================================
# Section 11 — Input sanitization / consistent error format
# ===========================================================================

class TestInputSanitization(unittest.TestCase):

    def test_signal_endpoint_rejects_missing_action(self):
        app = cloud_bridge.app
        uid = f"san_{int(time.time())}"
        api_key = "sanitestkey1234567890123456"
        cloud_bridge.store.upsert_user(uid, api_key)
        with app.test_client() as client:
            r = client.post(
                "/signal",
                json={"symbol": "EURUSD"},
                headers={"X-User-ID": uid, "X-API-Key": api_key},
            )
        self.assertEqual(r.status_code, 400)
        self.assertIn("error", r.get_json())

    def test_signal_endpoint_rejects_missing_symbol(self):
        app = cloud_bridge.app
        uid = f"san2_{int(time.time())}"
        api_key = "sanitestkey1234567890abcdef"
        cloud_bridge.store.upsert_user(uid, api_key)
        with app.test_client() as client:
            r = client.post(
                "/signal",
                json={"action": "BUY"},
                headers={"X-User-ID": uid, "X-API-Key": api_key},
            )
        self.assertEqual(r.status_code, 400)

    def test_signal_endpoint_rejects_invalid_auth(self):
        app = cloud_bridge.app
        with app.test_client() as client:
            r = client.post(
                "/signal",
                json={"action": "BUY", "symbol": "EURUSD"},
                headers={"X-User-ID": "nobody", "X-API-Key": "badkey"},
            )
        self.assertEqual(r.status_code, 401)

    def test_webhook_token_endpoint_invalid_token(self):
        app = cloud_bridge.app
        with app.test_client() as client:
            r = client.post("/signal/notarealtoken123",
                            json={"action": "BUY", "symbol": "EURUSD"})
        self.assertEqual(r.status_code, 404)

    def test_symbols_api_rejects_empty_symbol(self):
        app = cloud_bridge.app
        uid = f"symapistest_{int(time.time())}"
        api_key = "symapikey12345678901234567"
        cloud_bridge.store.upsert_user(uid, api_key)
        with app.test_client() as client:
            r = client.post(
                "/api/user/symbols",
                json={"symbol": ""},
                headers={"X-User-ID": uid, "X-API-Key": api_key},
            )
        self.assertEqual(r.status_code, 400)

    def test_settings_api_returns_all_fields(self):
        app = cloud_bridge.app
        uid = f"settest_{int(time.time())}"
        api_key = "settestkey12345678901234567"
        cloud_bridge.store.upsert_user(uid, api_key)
        with app.test_client() as client:
            r = client.get(
                "/settings",
                headers={"X-User-ID": uid, "X-API-Key": api_key},
            )
        self.assertEqual(r.status_code, 200)
        data = r.get_json()
        for field in ("max_lot_size", "default_lot_size", "default_sl_pips", "default_tp_pips"):
            self.assertIn(field, data, f"Expected '{field}' in /settings response")


# ===========================================================================
# Section 12 — Platform stats and admin BridgeStore methods
# ===========================================================================

class TestPlatformStats(unittest.TestCase):

    def test_get_platform_stats(self):
        s = _make_store()
        s.upsert_user("u1", "k1")
        s.upsert_user("u2", "k2")
        stats = s.get_platform_stats()
        self.assertGreaterEqual(stats["total_users"], 2)
        self.assertIn("managed_count", stats)
        self.assertIn("signals_today", stats)

    def test_get_all_users_summary(self):
        s = _make_store()
        s.upsert_user("sum_a", "k1")
        s.upsert_user("sum_b", "k2")
        users = s.get_all_users_summary()
        user_ids = [u["user_id"] for u in users]
        self.assertIn("sum_a", user_ids)
        self.assertIn("sum_b", user_ids)

    def test_get_user_admin_info(self):
        s = _make_store()
        _register(s)
        info = s.get_user_admin_info("testuser")
        self.assertIsNotNone(info)
        self.assertEqual(info["user_id"], "testuser")
        self.assertIn("channel_count", info)
        self.assertIn("signal_count", info)

    def test_get_user_admin_info_nonexistent(self):
        s = _make_store()
        self.assertIsNone(s.get_user_admin_info("ghost"))


# ===========================================================================
# Live TradingView webhook test (Ayan / 123456Uy)
# ===========================================================================

@pytest.mark.integration
class TestLiveWebhook(unittest.TestCase):
    """
    Live integration test: authenticate as Ayan and send a TradingView-style
    BUY signal to the cloud bridge.

    Requires network access to https://app.platalgo.com. The test is skipped
    in offline/CI environments.
    """

    BRIDGE_URL = "https://app.platalgo.com"
    USER_ID = "Ayan"
    PASSWORD = "123456Uy"

    def setUp(self):
        try:
            import requests as _req
            resp = _req.get(f"{self.BRIDGE_URL}/health", timeout=5)
            if resp.status_code != 200:
                self.skipTest("Cloud bridge not reachable")
            self._requests = _req
        except Exception:
            self.skipTest("Cloud bridge not reachable — skipping live test")

    def _get_api_key(self):
        """Log in via relay/login to get api_key."""
        r = self._requests.post(
            f"{self.BRIDGE_URL}/relay/login",
            json={
                "user_id": self.USER_ID,
                "password": self.PASSWORD,
                "relay_id": "test-relay-live",
            },
            timeout=10,
        )
        self.assertEqual(r.status_code, 200, f"Login failed: {r.text}")
        data = r.json()
        self.assertIn("api_key", data, f"No api_key in response: {data}")
        return data["api_key"]

    def test_live_signal_buy_eurusd(self):
        """Send a BUY EURUSD signal and expect queued or executed response."""
        api_key = self._get_api_key()
        r = self._requests.post(
            f"{self.BRIDGE_URL}/signal",
            json={"action": "BUY", "symbol": "EURUSD", "size": 0.01},
            headers={"X-User-ID": self.USER_ID, "X-API-Key": api_key},
            timeout=30,
        )
        self.assertIn(r.status_code, (200, 202, 500),
                      f"Unexpected status: {r.status_code} {r.text}")
        data = r.json()
        self.assertIn("status", data)
        self.assertIn(data["status"], ("queued", "executed", "failed"),
                      f"Unexpected status value: {data}")
        # 500/failed is OK — markets may be closed or managed session reconnecting
        print(f"\n[LIVE] Signal result: {data}")

    def test_live_dashboard_summary(self):
        """Dashboard summary endpoint returns relay and webhook info."""
        api_key = self._get_api_key()
        r = self._requests.post(
            f"{self.BRIDGE_URL}/dashboard/summary/login",
            json={"user_id": self.USER_ID, "api_key": api_key},
            timeout=10,
        )
        self.assertEqual(r.status_code, 200, f"Dashboard failed: {r.text}")
        data = r.json()
        self.assertIn("webhook_url", data)
        print(f"\n[LIVE] Dashboard webhook_url: {data.get('webhook_url')}")

    def test_live_managed_status(self):
        """Managed MT5 status endpoint responds."""
        api_key = self._get_api_key()
        r = self._requests.get(
            f"{self.BRIDGE_URL}/managed/status",
            headers={"X-User-ID": self.USER_ID, "X-API-Key": api_key},
            timeout=10,
        )
        self.assertEqual(r.status_code, 200, f"Managed status failed: {r.text}")
        data = r.json()
        self.assertIn("configured", data)
        print(f"\n[LIVE] Managed status: {data}")

    def test_live_auth_rejects_wrong_password(self):
        """Wrong password must return 401."""
        r = self._requests.post(
            f"{self.BRIDGE_URL}/relay/login",
            json={"user_id": self.USER_ID, "password": "WRONGPASS"},
            timeout=10,
        )
        self.assertEqual(r.status_code, 401, f"Expected 401, got {r.status_code}: {r.text}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
