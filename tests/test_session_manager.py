"""Tests for MT5UserSession and SessionManager (managed VPS execution)."""

import os
import sys
import threading
import time
import unittest
from unittest.mock import MagicMock, patch, call

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from managed_mt5_worker import MT5UserSession, SessionManager


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_mt5(connected=True, init_ok=True, order_ok=True):
    """Return a minimal MT5 mock."""
    mt5 = MagicMock()
    # Set constants FIRST so they are integers before anything references them
    mt5.TRADE_RETCODE_DONE = 10009
    mt5.ORDER_TYPE_BUY = 0
    mt5.ORDER_TYPE_SELL = 1
    mt5.TRADE_ACTION_DEAL = 1
    mt5.ORDER_FILLING_IOC = 2

    mt5.initialize.return_value = init_ok
    mt5.terminal_info.return_value = MagicMock(connected=connected) if connected else None

    acct = MagicMock()
    acct.login = 12345
    acct.server = "MockBroker"
    mt5.account_info.return_value = acct

    result = MagicMock()
    result.retcode = 10009 if order_ok else 10019  # use literal, not attribute ref
    result.order = 77777
    result.comment = "done"
    mt5.order_send.return_value = result

    return mt5


# ---------------------------------------------------------------------------
# MT5UserSession
# ---------------------------------------------------------------------------

class TestMT5UserSessionConnect(unittest.TestCase):
    """Test MT5UserSession connection logic."""

    def test_connect_success_sets_connected(self):
        """_connect() should set _connected True on successful mt5.initialize()."""
        mt5 = _make_mt5(init_ok=True, connected=True)
        session = MT5UserSession.__new__(MT5UserSession)
        session.user_id = "user1"
        session._login = 12345
        session._password = "pass"
        session._server = "MockBroker"
        session._path = None

        result = session._connect(mt5)

        self.assertTrue(result)
        mt5.initialize.assert_called_once_with(
            path=None, login=12345, password="pass", server="MockBroker"
        )

    def test_connect_failure_returns_false(self):
        """_connect() should return False when mt5.initialize() fails."""
        mt5 = _make_mt5(init_ok=False)
        mt5.last_error.return_value = (-1, "connection error")
        session = MT5UserSession.__new__(MT5UserSession)
        session.user_id = "user1"
        session._login = 12345
        session._password = "pass"
        session._server = "MockBroker"
        session._path = None

        result = session._connect(mt5)
        self.assertFalse(result)

    def test_is_alive_true_when_connected(self):
        """_is_alive() returns True when terminal reports connected."""
        mt5 = _make_mt5(connected=True)
        session = MT5UserSession.__new__(MT5UserSession)
        session.user_id = "user1"

        self.assertTrue(session._is_alive(mt5))

    def test_is_alive_false_when_disconnected(self):
        """_is_alive() returns False when terminal is not connected."""
        mt5 = _make_mt5(connected=False)
        session = MT5UserSession.__new__(MT5UserSession)
        session.user_id = "user1"

        self.assertFalse(session._is_alive(mt5))


class TestMT5UserSessionExecute(unittest.TestCase):
    """Test command execution via MT5UserSession queue."""

    @patch("managed_mt5_worker.MT5UserSession._connect", return_value=True)
    @patch("managed_mt5_worker.MT5UserSession._is_alive", return_value=True)
    def test_execute_command_routed_to_thread(self, mock_alive, mock_connect):
        """execute() should dispatch to the session thread and return result."""
        session = MT5UserSession.__new__(MT5UserSession)
        session.user_id = "user1"
        session._login = 12345
        session._password = "pass"
        session._server = "MockBroker"
        session._path = None
        session._stopped = False
        session._connected = True

        import queue as _queue
        session._queue = _queue.Queue()

        # Simulate a command being processed synchronously
        cmd = {"action": "BUY", "symbol": "EURUSD", "size": 0.1}
        expected = {"status": "executed", "order_id": 77777}

        def fake_put(item):
            # Immediately resolve the result_box and set the event
            command, result_box, done = item
            result_box.append(expected)
            done.set()

        session._queue.put = fake_put

        result = session.execute(cmd)
        self.assertEqual(result, expected)

    def test_execute_returns_error_when_stopped(self):
        """execute() should immediately return error when session is stopped."""
        session = MT5UserSession.__new__(MT5UserSession)
        session.user_id = "user1"
        session._stopped = True

        result = session.execute({"action": "BUY", "symbol": "EURUSD"})
        self.assertEqual(result["status"], "failed")
        self.assertIn("shut down", result["error"])

    def test_execute_timeout(self):
        """execute() should return timeout error if result never arrives."""
        import queue as _queue
        import managed_mt5_worker as mw

        original_timeout = mw.TRADE_TIMEOUT_SECS
        mw.TRADE_TIMEOUT_SECS = 0.1  # very short timeout for test speed

        session = MT5UserSession.__new__(MT5UserSession)
        session.user_id = "user1"
        session._stopped = False
        session._queue = _queue.Queue()

        result = session.execute({"action": "BUY", "symbol": "EURUSD"})
        mw.TRADE_TIMEOUT_SECS = original_timeout

        self.assertEqual(result["status"], "failed")
        self.assertIn("timed out", result["error"])


# ---------------------------------------------------------------------------
# SessionManager
# ---------------------------------------------------------------------------

class TestSessionManager(unittest.TestCase):
    """Test SessionManager lifecycle and dispatch."""

    def test_start_session_creates_entry(self):
        """start_session() should create a session for the user."""
        mgr = SessionManager()

        with patch("managed_mt5_worker.MT5UserSession") as MockSession:
            mock_s = MagicMock()
            MockSession.return_value = mock_s

            mgr.start_session("alice", 111, "pw", "FakeBroker")

        self.assertIn("alice", mgr._sessions)

    def test_start_session_replaces_existing(self):
        """start_session() should shut down old session before creating new one."""
        mgr = SessionManager()

        with patch("managed_mt5_worker.MT5UserSession") as MockSession:
            old_session = MagicMock()
            new_session = MagicMock()
            MockSession.side_effect = [old_session, new_session]

            mgr.start_session("alice", 111, "pw", "Broker")
            mgr.start_session("alice", 222, "pw2", "Broker")

        old_session.shutdown.assert_called_once()
        self.assertIs(mgr._sessions["alice"], new_session)

    def test_stop_session_removes_entry(self):
        """stop_session() should shut down session and remove it from the map."""
        mgr = SessionManager()

        with patch("managed_mt5_worker.MT5UserSession") as MockSession:
            mock_s = MagicMock()
            MockSession.return_value = mock_s

            mgr.start_session("bob", 123, "pw", "Broker")
            mgr.stop_session("bob")

        mock_s.shutdown.assert_called_once()
        self.assertNotIn("bob", mgr._sessions)

    def test_execute_dispatches_to_session(self):
        """execute() should forward command to the user's session."""
        mgr = SessionManager()

        mock_session = MagicMock()
        mock_session.execute.return_value = {"status": "executed", "order_id": 1}

        with mgr._lock:
            mgr._sessions["charlie"] = mock_session

        result = mgr.execute("charlie", {"action": "BUY", "symbol": "EURUSD"})

        mock_session.execute.assert_called_once_with({"action": "BUY", "symbol": "EURUSD"})
        self.assertEqual(result["status"], "executed")

    def test_execute_unknown_user_returns_error(self):
        """execute() for unknown user should return error without crashing."""
        mgr = SessionManager()
        result = mgr.execute("nobody", {"action": "BUY", "symbol": "EURUSD"})
        self.assertEqual(result["status"], "failed")
        self.assertIn("no active MT5 session", result["error"])

    def test_session_status_active(self):
        """session_status() returns active+connected when session exists."""
        mgr = SessionManager()
        mock_session = MagicMock()
        mock_session.connected = True

        with mgr._lock:
            mgr._sessions["dave"] = mock_session

        status = mgr.session_status("dave")
        self.assertTrue(status["active"])
        self.assertTrue(status["connected"])

    def test_session_status_not_found(self):
        """session_status() returns inactive for unknown user."""
        mgr = SessionManager()
        status = mgr.session_status("ghost")
        self.assertFalse(status["active"])
        self.assertFalse(status["connected"])

    def test_load_from_store(self):
        """load_from_store() should start one session per enabled account."""
        mgr = SessionManager()

        mock_store = MagicMock()
        mock_store.get_all_managed_accounts.return_value = [
            {
                "user_id": "u1",
                "mt5_login": "111",
                "mt5_password_enc": "enc_pw1",
                "mt5_server": "BrokerA",
                "mt5_path": "",
            },
            {
                "user_id": "u2",
                "mt5_login": "222",
                "mt5_password_enc": "enc_pw2",
                "mt5_server": "BrokerB",
                "mt5_path": None,
            },
        ]
        decrypt_fn = lambda enc: enc.replace("enc_", "")

        with patch.object(mgr, "start_session") as mock_start:
            mgr.load_from_store(mock_store, decrypt_fn)

        self.assertEqual(mock_start.call_count, 2)
        calls = mock_start.call_args_list
        self.assertEqual(calls[0], call("u1", 111, "pw1", "BrokerA", None))
        self.assertEqual(calls[1], call("u2", 222, "pw2", "BrokerB", None))

    def test_load_from_store_skips_bad_accounts(self):
        """load_from_store() should skip bad accounts and not raise."""
        mgr = SessionManager()

        mock_store = MagicMock()
        mock_store.get_all_managed_accounts.return_value = [
            {
                "user_id": "good",
                "mt5_login": "111",
                "mt5_password_enc": "enc_pw",
                "mt5_server": "Broker",
                "mt5_path": None,
            },
            {
                "user_id": "bad",
                # Non-integer login — int() raises ValueError before start_session
                "mt5_login": "NOT_A_NUMBER",
                "mt5_password_enc": "enc_pw",
                "mt5_server": "Broker",
                "mt5_path": None,
            },
        ]
        decrypt_fn = lambda enc: enc.replace("enc_", "")

        with patch.object(mgr, "start_session") as mock_start:
            # Should not raise even though "bad" causes int() to fail
            mgr.load_from_store(mock_store, decrypt_fn)

        # Only "good" reached start_session; "bad" failed at int(login) before the call
        self.assertEqual(mock_start.call_count, 1)
        self.assertEqual(mock_start.call_args_list[0][0][0], "good")


# ---------------------------------------------------------------------------
# mt5_order_utils
# ---------------------------------------------------------------------------

class TestMT5OrderUtils(unittest.TestCase):
    """Test shared MT5 order utilities."""

    def setUp(self):
        from mt5_order_utils import map_mt5_retcode, execute_command
        self.map_retcode = map_mt5_retcode
        self.execute_command = execute_command

    def test_retcode_known(self):
        self.assertIn("stop loss", self.map_retcode(10016).lower())
        self.assertIn("closed", self.map_retcode(10018).lower())
        self.assertIn("money", self.map_retcode(10019).lower())

    def test_retcode_unknown(self):
        msg = self.map_retcode(99999)
        self.assertIn("99999", msg)

    def test_retcode_none(self):
        msg = self.map_retcode(None)
        self.assertIn("failed", msg.lower())

    def test_execute_command_buy(self):
        mt5 = _make_mt5(order_ok=True)

        tick = MagicMock()
        tick.ask = 1.1050
        tick.bid = 1.1048
        mt5.symbol_info_tick.return_value = tick

        result = self.execute_command(mt5, {
            "action": "BUY",
            "symbol": "EURUSD",
            "size": 0.1,
        })
        self.assertEqual(result["status"], "executed")
        self.assertEqual(result["order_id"], 77777)

    def test_execute_command_unknown_action(self):
        mt5 = _make_mt5()
        result = self.execute_command(mt5, {
            "action": "HODL",
            "symbol": "EURUSD",
        })
        self.assertEqual(result["status"], "failed")
        self.assertIn("unknown action", result["error"])

    def test_execute_command_close_no_positions(self):
        mt5 = _make_mt5()
        mt5.positions_get.return_value = []

        result = self.execute_command(mt5, {
            "action": "CLOSE_ALL",
            "symbol": "EURUSD",
        })
        self.assertEqual(result["status"], "failed")
        self.assertIn("no open positions", result["error"])


if __name__ == "__main__":
    unittest.main()
