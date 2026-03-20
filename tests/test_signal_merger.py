"""Tests for signal merging and channel-scoped management."""

import time
import pytest
from telegram_signal_parser import ParsedSignal
from telegram_bot_manager import SignalMerger


class TestSignalMerger:
    def setup_method(self):
        self.merger = SignalMerger(merge_window=5.0)

    def test_complete_signal_passes_through(self):
        """A signal with action, symbol, and SL should not be held."""
        parsed = ParsedSignal(action="BUY", symbol="EURUSD", sl=1.0690,
                              tp_list=[1.0780], confidence=0.95)
        merged, should_wait = self.merger.try_merge("chat1", parsed, "BUY EURUSD SL 1.0690 TP 1.0780", 1)
        assert merged is None
        assert should_wait is False

    def test_incomplete_signal_held(self):
        """A signal with action+symbol but no SL/TP should be held for merging."""
        parsed = ParsedSignal(action="BUY", symbol="XAUUSD", confidence=0.7)
        merged, should_wait = self.merger.try_merge("chat1", parsed, "BUY GOLD", 1)
        assert merged is None
        assert should_wait is True

    def test_merge_two_messages(self):
        """Two incomplete messages should merge into one complete signal."""
        # Message 1: BUY GOLD (has action+symbol, no levels)
        parsed1 = ParsedSignal(action="BUY", symbol="XAUUSD", confidence=0.7)
        merged, should_wait = self.merger.try_merge("chat1", parsed1, "BUY GOLD", 1)
        assert should_wait is True

        # Message 2: SL 2320 TP 2380 (has levels, no action/symbol)
        parsed2 = ParsedSignal(sl=2320.0, tp_list=[2380.0], confidence=0.25)
        merged, should_wait = self.merger.try_merge("chat1", parsed2, "SL 2320 TP 2380", 2)
        assert merged is not None
        assert merged.action == "BUY"
        assert merged.symbol == "XAUUSD"
        assert merged.sl == 2320.0
        assert merged.tp_list == [2380.0]

    def test_different_chats_independent(self):
        """Merging is per-chat — different chats don't interfere."""
        parsed1 = ParsedSignal(action="BUY", symbol="EURUSD", confidence=0.7)
        self.merger.try_merge("chat1", parsed1, "BUY EURUSD", 1)

        parsed2 = ParsedSignal(action="SELL", symbol="GBPUSD", confidence=0.7)
        self.merger.try_merge("chat2", parsed2, "SELL GBPUSD", 2)

        # Follow-up for chat1 should merge with chat1's pending signal
        parsed3 = ParsedSignal(sl=1.0690, tp_list=[1.0780], confidence=0.25)
        merged, _ = self.merger.try_merge("chat1", parsed3, "SL 1.0690 TP 1.0780", 3)
        assert merged is not None
        assert merged.symbol == "EURUSD"
        assert merged.action == "BUY"

    def test_expired_signal_flushed(self):
        """Expired pending signals should be returned by flush_expired."""
        self.merger = SignalMerger(merge_window=0.1)  # 100ms window for testing
        parsed = ParsedSignal(action="BUY", symbol="EURUSD", confidence=0.7)
        self.merger.try_merge("chat1", parsed, "BUY EURUSD", 1)

        time.sleep(0.15)  # wait for expiry
        expired = self.merger.flush_expired()
        assert len(expired) == 1
        assert expired[0][1].action == "BUY"
        assert expired[0][1].symbol == "EURUSD"

    def test_incomplete_without_action_discarded_on_flush(self):
        """Expired signals without action+symbol should be discarded."""
        self.merger = SignalMerger(merge_window=0.1)
        parsed = ParsedSignal(sl=1.0690, tp_list=[1.0780], confidence=0.25)
        self.merger.try_merge("chat1", parsed, "SL 1.0690 TP 1.0780", 1)

        time.sleep(0.15)
        expired = self.merger.flush_expired()
        assert len(expired) == 0  # no action+symbol → discarded

    def test_high_confidence_signal_not_held(self):
        """A signal with high confidence (action+symbol) should not be held even without SL/TP."""
        parsed = ParsedSignal(action="BUY", symbol="EURUSD", confidence=0.95,
                              sl=1.0690)  # has SL → complete enough
        merged, should_wait = self.merger.try_merge("chat1", parsed, "BUY EURUSD SL 1.0690", 1)
        assert should_wait is False

    def test_merge_preserves_newer_values(self):
        """When both messages have overlapping fields, newer wins."""
        # First message: action+symbol only (no SL/TP, low confidence → held)
        parsed1 = ParsedSignal(action="BUY", symbol="XAUUSD", confidence=0.7)
        self.merger.try_merge("chat1", parsed1, "BUY GOLD", 1)

        # Second message: SL and TP (merged with first)
        parsed2 = ParsedSignal(sl=2320.0, tp_list=[2380.0], confidence=0.25)
        merged, _ = self.merger.try_merge("chat1", parsed2, "SL 2320 TP 2380", 2)
        assert merged is not None
        assert merged.action == "BUY"
        assert merged.sl == 2320.0
        assert merged.tp_list == [2380.0]


class TestLLMFallbackIntegration:
    """Test LLM fallback module structure (no real API calls)."""

    def test_llm_not_configured(self):
        from telegram_llm_fallback import LLMFallback
        llm = LLMFallback(api_key=None)
        assert not llm.is_configured

    def test_llm_configured(self):
        from telegram_llm_fallback import LLMFallback
        llm = LLMFallback(api_key="sk-test-key")
        assert llm.is_configured

    def test_parse_result_defaults(self):
        from telegram_llm_fallback import LLMParseResult
        result = LLMParseResult()
        assert result.action is None
        assert result.symbol is None
        assert result.confidence == 0.0
        assert result.llm_used is True
        assert result.error is None

    def test_json_to_result(self):
        from telegram_llm_fallback import LLMFallback
        llm = LLMFallback(api_key="test")
        result = llm._json_to_result({
            "action": "BUY",
            "symbol": "XAUUSD",
            "entry": 2350.0,
            "sl": 2320.0,
            "tp_list": [2380.0, 2400.0],
            "confidence": 0.9,
            "reasoning": "Clear buy signal",
        })
        assert result.action == "BUY"
        assert result.symbol == "XAUUSD"
        assert result.entry == 2350.0
        assert result.sl == 2320.0
        assert result.tp_list == [2380.0, 2400.0]
        assert result.confidence == 0.9

    def test_json_to_result_handles_bad_data(self):
        from telegram_llm_fallback import LLMFallback
        llm = LLMFallback(api_key="test")
        result = llm._json_to_result({
            "action": "BUY",
            "symbol": "EURUSD",
            "entry": "not_a_number",
            "tp_list": "also_not_a_list",
        })
        assert result.action == "BUY"
        assert result.entry is None  # _safe_float returns None

    def test_rate_limiter(self):
        from telegram_llm_fallback import LLMFallback
        llm = LLMFallback(api_key="test")
        llm._max_requests_per_minute = 3

        assert llm._check_rate_limit() is True
        assert llm._check_rate_limit() is True
        assert llm._check_rate_limit() is True
        assert llm._check_rate_limit() is False  # 4th should fail

    def test_processor_not_started_without_key(self):
        from telegram_llm_fallback import LLMFallback, LLMFallbackProcessor
        llm = LLMFallback(api_key=None)
        processor = LLMFallbackProcessor(llm=llm, execute_callback=lambda u, s: {})
        processor.start()
        assert not processor.is_running  # should not start without API key
