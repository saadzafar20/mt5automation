"""
Telegram Signal Parser Engine
=============================
Converts messy, human-written Telegram signal text into structured trade data.

Three-layer pipeline:
  Layer 1 — clean_text():      Remove emojis, normalize whitespace, uppercase
  Layer 2 — extract_signal():  Multi-pattern regex extraction
  Layer 3 — validate_signal(): SL/TP direction checks, symbol normalization

Usage:
    from telegram_signal_parser import parse_telegram_message
    result = parse_telegram_message("BUY EURUSD SL 1.0690 TP 1.0780")
    if result.action and not result.skip_reason:
        # valid signal — execute
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# Symbol aliases — common names → MT5 symbol
# ---------------------------------------------------------------------------
SYMBOL_ALIASES: dict[str, str] = {
    "GOLD": "XAUUSD",
    "XAUUSD": "XAUUSD",
    "SILVER": "XAGUSD",
    "XAGUSD": "XAGUSD",
    "OIL": "USOUSD",
    "CRUDE": "USOUSD",
    "WTI": "USOUSD",
    "BRENT": "UKOUSD",
    "CABLE": "GBPUSD",
    "FIBER": "EURUSD",
    "LOONIE": "USDCAD",
    "AUSSIE": "AUDUSD",
    "KIWI": "NZDUSD",
    "SWISSY": "USDCHF",
    "GOPHER": "USDJPY",
    "BITCOIN": "BTCUSD",
    "BTC": "BTCUSD",
    "ETHEREUM": "ETHUSD",
    "ETH": "ETHUSD",
    "NAS100": "NAS100",
    "NASDAQ": "NAS100",
    "US30": "US30",
    "DOW": "US30",
    "SPX500": "SPX500",
    "SP500": "SPX500",
    "US500": "SPX500",
    "DAX": "GER40",
    "GER40": "GER40",
    "GER30": "GER40",
}

# Known MT5 forex pairs (majors, minors, exotics) — used for direct matching
KNOWN_FOREX_PAIRS: set[str] = {
    "EURUSD", "GBPUSD", "USDJPY", "USDCHF", "AUDUSD", "NZDUSD", "USDCAD",
    "EURGBP", "EURJPY", "EURCHF", "EURAUD", "EURNZD", "EURCAD",
    "GBPJPY", "GBPCHF", "GBPAUD", "GBPNZD", "GBPCAD",
    "AUDJPY", "AUDCHF", "AUDNZD", "AUDCAD",
    "NZDJPY", "NZDCHF", "NZDCAD",
    "CADJPY", "CADCHF", "CHFJPY",
}

# All known symbols (forex + aliases targets)
ALL_KNOWN_SYMBOLS: set[str] = KNOWN_FOREX_PAIRS | set(SYMBOL_ALIASES.values())

# Action word mappings
ACTION_MAP: dict[str, str] = {
    "BUY": "BUY",
    "SELL": "SELL",
    "LONG": "BUY",
    "SHORT": "SELL",
}

# Management / non-trade keywords
MANAGEMENT_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\bCLOSE\s+(ALL|TRADE|POSITION|BUY|SELL)", re.IGNORECASE), "close"),
    (re.compile(r"\bSTOP\s+STOP\b", re.IGNORECASE), "close"),
    (re.compile(r"\bCLOSE\s+NOW\b", re.IGNORECASE), "close"),
    (re.compile(r"\bEXIT\s+(ALL|TRADE|NOW)", re.IGNORECASE), "close"),
    (re.compile(r"\bMOVE\s+SL\b", re.IGNORECASE), "move_sl"),
    (re.compile(r"\bBREAKEVEN\b|\bBE\b", re.IGNORECASE), "breakeven"),
    (re.compile(r"\bPARTIAL\s+(TP|CLOSE|PROFIT)", re.IGNORECASE), "partial_tp"),
    (re.compile(r"\bUPDATE\b.*\b(SL|TP|STOP|TAKE)\b", re.IGNORECASE), "update"),
    (re.compile(r"\bCANCEL\b.*\b(ORDER|TRADE|SIGNAL)\b", re.IGNORECASE), "cancel"),
    (re.compile(r"\bSECURE\s+(PROFIT|TRADE)", re.IGNORECASE), "secure_profit"),
]

# Pips format detection — "50 pips", "100 pip"
PIPS_PATTERN = re.compile(r"\bPIPS?\b", re.IGNORECASE)
NUMBER_PATTERN = re.compile(r"\d+(?:\.\d+)?")

# Emoji removal pattern
EMOJI_PATTERN = re.compile(
    "["
    "\U0001F600-\U0001F64F"  # emoticons
    "\U0001F300-\U0001F5FF"  # symbols & pictographs
    "\U0001F680-\U0001F6FF"  # transport & map
    "\U0001F7E0-\U0001F7FF"  # geometric shapes extended
    "\U0001F1E0-\U0001F1FF"  # flags
    "\U00002702-\U000027B0"  # dingbats
    "\U000024C2-\U0001F251"  # enclosed characters
    "\U0001F900-\U0001F9FF"  # supplemental symbols
    "\U0001FA00-\U0001FA6F"  # chess symbols
    "\U0001FA70-\U0001FAFF"  # symbols extended-A
    "\U00002600-\U000026FF"  # misc symbols
    "\U0000FE00-\U0000FE0F"  # variation selectors
    "\U0000200D"             # zero width joiner
    "\U00000023\U000020E3"   # keycap #
    "]+",
    flags=re.UNICODE,
)


# ---------------------------------------------------------------------------
# Data class
# ---------------------------------------------------------------------------
@dataclass
class ParsedSignal:
    action: str | None = None       # "BUY" | "SELL"
    symbol: str | None = None       # Normalized MT5 symbol
    entry: float | None = None      # None = market order
    sl: float | None = None
    tp_list: list[float] = field(default_factory=list)
    confidence: float = 0.0         # 0.0-1.0
    raw_text: str = ""
    skip_reason: str | None = None  # If set, this is not a valid trade signal
    management_type: str | None = None  # "close", "move_sl", etc.


# ---------------------------------------------------------------------------
# Layer 1 — Text cleaning
# ---------------------------------------------------------------------------
def clean_text(raw: str) -> str:
    """Remove emojis, normalize whitespace, uppercase."""
    text = EMOJI_PATTERN.sub(" ", raw)
    # Normalize various dash/hyphen characters
    text = re.sub(r"[–—−]", "-", text)
    # Normalize colons with optional spaces around them for consistent parsing
    # "SL:1920" → "SL: 1920", "TP : 1900" → "TP: 1900"
    text = re.sub(r"\s*:\s*", ": ", text)
    # Collapse multiple spaces/tabs into single space
    text = re.sub(r"[ \t]+", " ", text)
    # Uppercase for consistent matching
    text = text.upper().strip()
    return text


def normalize_for_learning(raw: str) -> str:
    """Normalize text and replace numeric literals with <NUM> placeholders."""
    cleaned = clean_text(raw)
    return NUMBER_PATTERN.sub("<NUM>", cleaned)


def build_learned_regex(raw: str) -> tuple[str, str, str]:
    """
    Build a safe regex from a concrete message.

    Returns:
        (cleaned_text, normalized_template, regex_pattern)
    """
    cleaned = clean_text(raw)
    normalized = NUMBER_PATTERN.sub("<NUM>", cleaned)

    parts: list[str] = []
    last = 0
    for m in NUMBER_PATTERN.finditer(cleaned):
        parts.append(re.escape(cleaned[last:m.start()]))
        parts.append(r"([0-9]+(?:\.[0-9]+)?)")
        last = m.end()
    parts.append(re.escape(cleaned[last:]))

    regex_pattern = "^" + "".join(parts) + "$"
    return cleaned, normalized, regex_pattern


# ---------------------------------------------------------------------------
# Layer 2 — Signal extraction
# ---------------------------------------------------------------------------
def _extract_action(text: str) -> str | None:
    """Extract BUY/SELL from text."""
    # Try explicit action words first
    match = re.search(r"\b(BUY|SELL|LONG|SHORT)\b", text)
    if match:
        return ACTION_MAP[match.group(1)]
    return None


def _extract_symbol(text: str) -> str | None:
    """Extract and normalize the trading symbol."""
    # Strategy 1: Check for known aliases (GOLD, CABLE, BTC, etc.)
    for alias, symbol in SYMBOL_ALIASES.items():
        if re.search(r"\b" + re.escape(alias) + r"\b", text):
            return symbol

    # Strategy 2: Check for known forex pairs
    for pair in KNOWN_FOREX_PAIRS:
        if re.search(r"\b" + re.escape(pair) + r"\b", text):
            return pair

    # Strategy 3: Generic forex pattern — 6 uppercase letters (e.g. AUDNZD)
    match = re.search(r"\b([A-Z]{6})\b", text)
    if match:
        candidate = match.group(1)
        # Exclude common false positives
        if candidate not in {"SIGNAL", "MARKET", "PROFIT", "TARGET", "CANCEL",
                             "UPDATE", "CLOSED", "GOLDEN", "TRADER", "STRONG",
                             "RESULT", "REASON", "SECURE", "MANAGE", "FRIEND",
                             "ALWAYS", "PLEASE", "THANKS", "MOVING", "UPSIDE",
                             "BUYING", "REVIEW", "BEFORE", "FINGER"}:
            return candidate

    # Strategy 4: Pairs with slash — EUR/USD, GBP/JPY
    match = re.search(r"\b([A-Z]{3})/([A-Z]{3})\b", text)
    if match:
        return match.group(1) + match.group(2)

    return None


def _extract_price(text: str, keywords: list[str]) -> float | None:
    """Extract a price value near one of the given keywords."""
    for kw in keywords:
        # Match: keyword followed by optional colon/space, then a number
        pattern = r"\b" + kw + r"\b[\s:=]*(\d+\.?\d*)"
        match = re.search(pattern, text)
        if match:
            try:
                return float(match.group(1))
            except ValueError:
                continue
    return None


def _extract_entry(text: str) -> float | None:
    """Extract entry price."""
    # Try explicit entry keywords
    price = _extract_price(text, ["ENTRY", "ENTER", "EP", "PRICE", "OPEN"])
    if price:
        return price

    # Try @ symbol: "BUY EURUSD @ 1.0720" or "BUY EURUSD @1.0720"
    match = re.search(r"@\s*(\d+\.?\d*)", text)
    if match:
        try:
            return float(match.group(1))
        except ValueError:
            pass

    return None


def _extract_sl(text: str) -> float | None:
    """Extract stop loss price."""
    return _extract_price(text, ["SL", "STOP LOSS", "STOPLOSS", "STOP"])


def _extract_tp_list(text: str) -> list[float]:
    """Extract one or more take profit levels."""
    tp_values: list[float] = []

    # Try numbered TPs: TP1, TP2, TP3 — require at least one separator between
    # the TP label and the price to avoid backtracking into "TP 100 pips" → "10"
    numbered = re.findall(r"\bTP\d{0,2}[\s:=]+(\d+\.?\d*)", text)
    if numbered:
        for val in numbered:
            try:
                tp_values.append(float(val))
            except ValueError:
                continue
        return tp_values

    # Try "TAKE PROFIT" keyword
    price = _extract_price(text, ["TAKE PROFIT", "TAKEPROFIT", "TARGET"])
    if price:
        return [price]

    return tp_values


def extract_signal(cleaned_text: str) -> ParsedSignal:
    """Layer 2: Extract signal fields from cleaned text using multi-pattern regex."""
    signal = ParsedSignal(raw_text=cleaned_text)

    signal.action = _extract_action(cleaned_text)
    signal.symbol = _extract_symbol(cleaned_text)
    signal.entry = _extract_entry(cleaned_text)
    signal.sl = _extract_sl(cleaned_text)
    signal.tp_list = _extract_tp_list(cleaned_text)

    # Calculate confidence based on extracted fields
    score = 0.0
    if signal.action:
        score += 0.35
    if signal.symbol:
        score += 0.35
    if signal.sl:
        score += 0.15
    if signal.tp_list:
        score += 0.10
    if signal.entry:
        score += 0.05
    signal.confidence = min(score, 1.0)

    return signal


# ---------------------------------------------------------------------------
# Layer 3 — Validation
# ---------------------------------------------------------------------------
def validate_signal(signal: ParsedSignal) -> ParsedSignal:
    """Layer 3: Validate extracted signal — SL/TP direction, minimum fields."""
    # Must have at least action + symbol
    if not signal.action or not signal.symbol:
        signal.skip_reason = "missing action or symbol"
        return signal

    # Validate SL direction if both entry and SL are known
    if signal.entry and signal.sl:
        if signal.action == "BUY" and signal.sl >= signal.entry:
            signal.skip_reason = f"SL ({signal.sl}) >= entry ({signal.entry}) for BUY"
            return signal
        if signal.action == "SELL" and signal.sl <= signal.entry:
            signal.skip_reason = f"SL ({signal.sl}) <= entry ({signal.entry}) for SELL"
            return signal

    # Validate TP direction if both entry and TP are known
    if signal.entry and signal.tp_list:
        first_tp = signal.tp_list[0]
        if signal.action == "BUY" and first_tp <= signal.entry:
            signal.skip_reason = f"TP ({first_tp}) <= entry ({signal.entry}) for BUY"
            return signal
        if signal.action == "SELL" and first_tp >= signal.entry:
            signal.skip_reason = f"TP ({first_tp}) >= entry ({signal.entry}) for SELL"
            return signal

    return signal


# ---------------------------------------------------------------------------
# Management message detection
# ---------------------------------------------------------------------------
def is_management_message(text: str) -> tuple[bool, str | None]:
    """
    Detect non-trade management messages: close, move SL, breakeven, etc.
    Returns (is_management, management_type).
    """
    for pattern, mgmt_type in MANAGEMENT_PATTERNS:
        if pattern.search(text):
            return True, mgmt_type
    return False, None


# ---------------------------------------------------------------------------
# Top-level entry point
# ---------------------------------------------------------------------------
def parse_telegram_message(raw_text: str) -> ParsedSignal:
    """
    Full pipeline: clean → check management → extract → validate.
    Returns a ParsedSignal with all fields populated.
    """
    if not raw_text or not raw_text.strip():
        return ParsedSignal(raw_text=raw_text or "", skip_reason="empty message")

    # Layer 1: Clean
    cleaned = clean_text(raw_text)

    # Check for management messages first
    is_mgmt, mgmt_type = is_management_message(cleaned)
    if is_mgmt:
        return ParsedSignal(
            raw_text=raw_text,
            skip_reason=f"management message: {mgmt_type}",
            management_type=mgmt_type,
        )

    # Detect pips-format signals before extraction — "TP 100 pips SL 50 pips".
    # The regex pipeline treats pips values as absolute prices (wrong); route to LLM instead.
    if PIPS_PATTERN.search(cleaned):
        action = _extract_action(cleaned)
        symbol = _extract_symbol(cleaned)
        confidence = (0.35 if action else 0.0) + (0.35 if symbol else 0.0)
        return ParsedSignal(
            raw_text=raw_text,
            action=action,
            symbol=symbol,
            skip_reason="pips format signal — needs LLM interpretation",
            confidence=confidence,
        )

    # Layer 2: Extract
    signal = extract_signal(cleaned)
    signal.raw_text = raw_text  # preserve original (not cleaned)

    # Layer 3: Validate
    signal = validate_signal(signal)

    # If confidence too low and no action/symbol, mark as non-signal
    if signal.confidence < 0.5 and not signal.skip_reason:
        signal.skip_reason = "low confidence — likely not a trade signal"

    return signal
