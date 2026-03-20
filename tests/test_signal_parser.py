"""Tests for the Telegram signal parser engine."""

import pytest
from telegram_signal_parser import (
    ParsedSignal,
    clean_text,
    extract_signal,
    is_management_message,
    parse_telegram_message,
    validate_signal,
)


# ── Layer 1: clean_text ─────────────────────────────────────────────────────

class TestCleanText:
    def test_removes_emojis(self):
        result = clean_text("🟢 BUY EURUSD NOW")
        assert "BUY" in result
        assert "EURUSD" in result
        assert "🟢" not in result

    def test_uppercases(self):
        assert "BUY EURUSD" in clean_text("buy eurusd")

    def test_normalizes_whitespace(self):
        result = clean_text("BUY   EURUSD    SL  1.07")
        assert "  " not in result

    def test_normalizes_colons(self):
        result = clean_text("SL:1920")
        assert "SL: 1920" in result

    def test_empty_string(self):
        assert clean_text("") == ""

    def test_emoji_only(self):
        result = clean_text("🟢🔴📊")
        assert result.strip() == ""


# ── Layer 2: extract_signal ──────────────────────────────────────────────────

class TestExtractAction:
    def test_buy(self):
        sig = extract_signal("BUY EURUSD SL 1.0690 TP 1.0780")
        assert sig.action == "BUY"

    def test_sell(self):
        sig = extract_signal("SELL XAUUSD SL 2365 TP 2340")
        assert sig.action == "SELL"

    def test_long_maps_to_buy(self):
        sig = extract_signal("LONG EURUSD")
        assert sig.action == "BUY"

    def test_short_maps_to_sell(self):
        sig = extract_signal("SHORT GBPUSD")
        assert sig.action == "SELL"

    def test_no_action(self):
        sig = extract_signal("EURUSD 1.0720")
        assert sig.action is None


class TestExtractSymbol:
    def test_forex_pair(self):
        sig = extract_signal("BUY EURUSD SL 1.07")
        assert sig.symbol == "EURUSD"

    def test_gold_alias(self):
        sig = extract_signal("BUY GOLD SL 1920")
        assert sig.symbol == "XAUUSD"

    def test_xauusd_direct(self):
        sig = extract_signal("SELL XAUUSD SL 2365")
        assert sig.symbol == "XAUUSD"

    def test_btc_alias(self):
        sig = extract_signal("BUY BTC SL 40000")
        assert sig.symbol == "BTCUSD"

    def test_nasdaq_alias(self):
        sig = extract_signal("SELL NASDAQ SL 18000")
        assert sig.symbol == "NAS100"

    def test_cable_alias(self):
        sig = extract_signal("BUY CABLE SL 1.25")
        assert sig.symbol == "GBPUSD"

    def test_slash_format(self):
        sig = extract_signal("BUY EUR/USD SL 1.07")
        assert sig.symbol == "EURUSD"

    def test_no_symbol(self):
        sig = extract_signal("BUY NOW SL 1920")
        assert sig.symbol is None


class TestExtractPrices:
    def test_sl_basic(self):
        sig = extract_signal("BUY EURUSD SL 1.0690 TP 1.0780")
        assert sig.sl == 1.0690

    def test_sl_with_colon(self):
        sig = extract_signal("BUY EURUSD SL: 1.0690")
        assert sig.sl == 1.0690

    def test_stop_loss_keyword(self):
        sig = extract_signal("BUY EURUSD STOP LOSS 1.0690")
        assert sig.sl == 1.0690

    def test_single_tp(self):
        sig = extract_signal("BUY EURUSD SL 1.0690 TP 1.0780")
        assert sig.tp_list == [1.0780]

    def test_multiple_tps(self):
        sig = extract_signal("SELL XAUUSD SL: 2365 TP1: 2340 TP2: 2330 TP3: 2310")
        assert sig.tp_list == [2340.0, 2330.0, 2310.0]

    def test_take_profit_keyword(self):
        sig = extract_signal("BUY EURUSD SL 1.0690 TAKE PROFIT 1.0780")
        assert sig.tp_list == [1.0780]

    def test_entry_at_symbol(self):
        sig = extract_signal("BUY EURUSD @1.0720 SL 1.0690")
        assert sig.entry == 1.0720

    def test_entry_keyword(self):
        sig = extract_signal("BUY EURUSD ENTRY 1.0720 SL 1.0690")
        assert sig.entry == 1.0720

    def test_no_entry_is_market(self):
        sig = extract_signal("BUY EURUSD SL 1.0690 TP 1.0780")
        assert sig.entry is None

    def test_no_sl(self):
        sig = extract_signal("BUY EURUSD TP 1.0780")
        assert sig.sl is None

    def test_no_tp(self):
        sig = extract_signal("BUY EURUSD SL 1.0690")
        assert sig.tp_list == []


class TestConfidence:
    def test_full_signal_high_confidence(self):
        sig = extract_signal("BUY EURUSD SL 1.0690 TP 1.0780")
        assert sig.confidence >= 0.9

    def test_action_symbol_only(self):
        sig = extract_signal("BUY EURUSD")
        assert sig.confidence == pytest.approx(0.7)

    def test_no_action_low_confidence(self):
        sig = extract_signal("EURUSD 1.0720 1.0690")
        assert sig.confidence < 0.5


# ── Layer 3: validate_signal ─────────────────────────────────────────────────

class TestValidation:
    def test_buy_sl_below_entry_valid(self):
        sig = ParsedSignal(action="BUY", symbol="EURUSD", entry=1.0720, sl=1.0690)
        result = validate_signal(sig)
        assert result.skip_reason is None

    def test_buy_sl_above_entry_invalid(self):
        sig = ParsedSignal(action="BUY", symbol="EURUSD", entry=1.0720, sl=1.0780)
        result = validate_signal(sig)
        assert result.skip_reason is not None
        assert "SL" in result.skip_reason

    def test_sell_sl_above_entry_valid(self):
        sig = ParsedSignal(action="SELL", symbol="EURUSD", entry=1.0720, sl=1.0780)
        result = validate_signal(sig)
        assert result.skip_reason is None

    def test_sell_sl_below_entry_invalid(self):
        sig = ParsedSignal(action="SELL", symbol="EURUSD", entry=1.0720, sl=1.0690)
        result = validate_signal(sig)
        assert result.skip_reason is not None

    def test_buy_tp_above_entry_valid(self):
        sig = ParsedSignal(action="BUY", symbol="EURUSD", entry=1.0720, tp_list=[1.0780])
        result = validate_signal(sig)
        assert result.skip_reason is None

    def test_buy_tp_below_entry_invalid(self):
        sig = ParsedSignal(action="BUY", symbol="EURUSD", entry=1.0720, tp_list=[1.0690])
        result = validate_signal(sig)
        assert result.skip_reason is not None

    def test_no_entry_skips_direction_check(self):
        sig = ParsedSignal(action="BUY", symbol="EURUSD", sl=1.0690, tp_list=[1.0780])
        result = validate_signal(sig)
        assert result.skip_reason is None

    def test_missing_action_invalid(self):
        sig = ParsedSignal(symbol="EURUSD")
        result = validate_signal(sig)
        assert result.skip_reason == "missing action or symbol"

    def test_missing_symbol_invalid(self):
        sig = ParsedSignal(action="BUY")
        result = validate_signal(sig)
        assert result.skip_reason == "missing action or symbol"


# ── Management message detection ─────────────────────────────────────────────

class TestManagementMessages:
    def test_close_all(self):
        is_mgmt, typ = is_management_message("CLOSE ALL TRADES")
        assert is_mgmt
        assert typ == "close"

    def test_close_buy(self):
        is_mgmt, typ = is_management_message("CLOSE BUY")
        assert is_mgmt

    def test_move_sl(self):
        is_mgmt, typ = is_management_message("MOVE SL TO BREAKEVEN")
        assert is_mgmt
        assert typ == "move_sl"

    def test_breakeven(self):
        is_mgmt, typ = is_management_message("SET BREAKEVEN")
        assert is_mgmt

    def test_partial_tp(self):
        is_mgmt, typ = is_management_message("PARTIAL TP HIT")
        assert is_mgmt

    def test_cancel_order(self):
        is_mgmt, typ = is_management_message("CANCEL ORDER")
        assert is_mgmt

    def test_stop_stop_stop(self):
        is_mgmt, typ = is_management_message("STOP STOP STOP")
        assert is_mgmt
        assert typ == "close"

    def test_close_now(self):
        is_mgmt, typ = is_management_message("CLOSE NOW")
        assert is_mgmt
        assert typ == "close"

    def test_exit_all(self):
        is_mgmt, typ = is_management_message("EXIT ALL TRADES")
        assert is_mgmt
        assert typ == "close"

    def test_normal_signal_not_management(self):
        is_mgmt, _ = is_management_message("BUY EURUSD SL 1.07 TP 1.08")
        assert not is_mgmt

    def test_random_text_not_management(self):
        is_mgmt, _ = is_management_message("Good morning everyone!")
        assert not is_mgmt


# ── Full pipeline: parse_telegram_message ────────────────────────────────────

class TestFullPipeline:
    def test_standard_signal(self):
        result = parse_telegram_message("BUY EURUSD SL 1.0690 TP 1.0780")
        assert result.action == "BUY"
        assert result.symbol == "EURUSD"
        assert result.sl == 1.0690
        assert result.tp_list == [1.0780]
        assert result.skip_reason is None

    def test_multiline_signal(self):
        text = "SELL XAUUSD\nSL: 2365\nTP1: 2340\nTP2: 2330"
        result = parse_telegram_message(text)
        assert result.action == "SELL"
        assert result.symbol == "XAUUSD"
        assert result.sl == 2365.0
        assert len(result.tp_list) == 2
        assert result.skip_reason is None

    def test_emoji_signal(self):
        text = "🟢 BUY Gold @ 2350\nStop Loss 2320\nTake Profit 2380"
        result = parse_telegram_message(text)
        assert result.action == "BUY"
        assert result.symbol == "XAUUSD"
        assert result.entry == 2350.0
        assert result.sl == 2320.0
        assert result.tp_list == [2380.0]
        assert result.skip_reason is None

    def test_now_keyword_market_order(self):
        text = "BUY EURUSD NOW\nSL 1.0690\nTP 1.0780"
        result = parse_telegram_message(text)
        assert result.action == "BUY"
        assert result.entry is None  # "NOW" = market order
        assert result.skip_reason is None

    def test_garbage_text_skipped(self):
        result = parse_telegram_message("Good morning everyone! Have a great trading day!")
        assert result.skip_reason is not None

    def test_management_message_skipped(self):
        result = parse_telegram_message("Close all gold trades")
        assert result.skip_reason is not None
        assert result.management_type == "close"

    def test_empty_message(self):
        result = parse_telegram_message("")
        assert result.skip_reason == "empty message"

    def test_none_like_whitespace(self):
        result = parse_telegram_message("   ")
        assert result.skip_reason == "empty message"

    def test_preserves_raw_text(self):
        raw = "🟢 BUY EURUSD NOW"
        result = parse_telegram_message(raw)
        assert result.raw_text == raw

    def test_mixed_case(self):
        result = parse_telegram_message("buy eurusd sl 1.0690 tp 1.0780")
        assert result.action == "BUY"
        assert result.symbol == "EURUSD"

    def test_signal_with_at_entry(self):
        text = "SELL GBPUSD @1.2650\nSL: 1.2700\nTP: 1.2580"
        result = parse_telegram_message(text)
        assert result.action == "SELL"
        assert result.symbol == "GBPUSD"
        assert result.entry == 1.2650
        assert result.sl == 1.2700
        assert result.tp_list == [1.2580]
        assert result.skip_reason is None

    def test_three_tps(self):
        text = "BUY EURUSD\nSL: 1.0650\nTP1: 1.0750\nTP2: 1.0800\nTP3: 1.0850"
        result = parse_telegram_message(text)
        assert result.action == "BUY"
        assert len(result.tp_list) == 3
        assert result.tp_list == [1.0750, 1.0800, 1.0850]

    def test_us30_alias(self):
        result = parse_telegram_message("SELL US30 SL 39500 TP 39000")
        assert result.symbol == "US30"

    def test_bitcoin_alias(self):
        result = parse_telegram_message("BUY BITCOIN SL 60000 TP 65000")
        assert result.symbol == "BTCUSD"

    def test_silver_alias(self):
        result = parse_telegram_message("BUY SILVER SL 22.50 TP 23.50")
        assert result.symbol == "XAGUSD"
