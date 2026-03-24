"""Tests for learned LLM parse pattern storage and matching."""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Required env vars before importing cloud_bridge / BridgeStore.
os.environ["CLOUD_BRIDGE_DEBUG"] = "true"
os.environ["BRIDGE_AUTH_SALT"] = "test-salt"
os.environ["BRIDGE_SESSION_SECRET"] = "test-session"
os.environ["BRIDGE_CREDS_KEY"] = "test-creds-key"

from cloud_bridge import BridgeStore


class TestLearnedPatterns(unittest.TestCase):
    def setUp(self):
        fd, path = tempfile.mkstemp(suffix="_learned_patterns.db")
        os.close(fd)
        self.db_path = path
        self.store = BridgeStore(self.db_path)
        self.user_id = "learned-user"
        self.store.register_dashboard_user(self.user_id, "password123")

    def tearDown(self):
        try:
            self.store.conn.close()
        except Exception:
            pass
        try:
            os.remove(self.db_path)
        except Exception:
            pass

    def test_manual_approval_gate(self):
        pattern_id = self.store.add_llm_learned_pattern(
            user_id=self.user_id,
            raw_text="BUY GOLD ENTRY 2350 SL 2320 TP 2380",
            action="BUY",
            symbol="XAUUSD",
            entry=2350.0,
            sl=2320.0,
            tp_list=[2380.0],
            source_confidence=0.95,
            min_confidence=0.9,
            auto_approve=False,
        )
        self.assertIsNotNone(pattern_id)

        # Not approved yet -> no match.
        self.assertIsNone(
            self.store.match_learned_pattern(
                self.user_id, "BUY GOLD ENTRY 2360 SL 2330 TP 2390"
            )
        )

        ok = self.store.set_learned_pattern_approved(int(pattern_id), True)
        self.assertTrue(ok)

        matched = self.store.match_learned_pattern(
            self.user_id, "BUY GOLD ENTRY 2360 SL 2330 TP 2390"
        )
        self.assertIsNotNone(matched)
        self.assertEqual(matched["action"], "BUY")
        self.assertEqual(matched["symbol"], "XAUUSD")

    def test_learned_pattern_extracts_updated_numeric_fields(self):
        pattern_id = self.store.add_llm_learned_pattern(
            user_id=self.user_id,
            raw_text="BUY GOLD ENTRY 2350 SL 2320 TP 2380",
            action="BUY",
            symbol="XAUUSD",
            entry=2350.0,
            sl=2320.0,
            tp_list=[2380.0],
            source_confidence=0.97,
            min_confidence=0.9,
            auto_approve=True,
        )
        self.assertIsNotNone(pattern_id)

        matched = self.store.match_learned_pattern(
            self.user_id, "BUY GOLD ENTRY 2361 SL 2331 TP 2391"
        )
        self.assertIsNotNone(matched)
        self.assertEqual(matched["entry"], 2361.0)
        self.assertEqual(matched["sl"], 2331.0)
        self.assertEqual(matched["tp_list"], [2391.0])


if __name__ == "__main__":
    unittest.main()
