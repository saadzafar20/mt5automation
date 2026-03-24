"""
Telegram LLM Fallback Engine
=============================
Async GPT-4o-mini fallback for signals the regex parser can't handle.
Also supports vision (chart screenshot parsing).

Architecture:
  - Never in the hot execution path (regex handles instant execution)
  - Runs async in background threads
  - Two modes:
    1. Text fallback:  Regex parser returns low confidence → LLM re-parses
    2. Vision parsing: Image/screenshot → LLM extracts signal from chart

Usage:
    from telegram_llm_fallback import LLMFallback
    llm = LLMFallback(api_key="sk-...")
    result = llm.parse_signal_text("messy signal text")
    result = llm.parse_signal_image(image_bytes)
"""

from __future__ import annotations

import base64
import json
import logging
import threading
import time
from dataclasses import dataclass
from typing import Callable

import requests

logger = logging.getLogger("telegram_llm_fallback")

# System prompt for signal parsing
SIGNAL_PARSE_PROMPT = """You are a trading signal parser. Extract structured trade data from the message.

Return ONLY valid JSON with these fields:
{
  "action": "BUY" or "SELL" or null,
  "symbol": "MT5 symbol like EURUSD, XAUUSD, GBPJPY" or null,
  "entry": entry price as number or null (null = market order),
  "sl": stop loss price as number or null,
  "tp_list": [take profit prices as numbers] or [],
  "confidence": 0.0 to 1.0,
  "management_type": "close" or "move_sl" or "breakeven" or "partial_tp" or "cancel" or null,
  "reasoning": "brief explanation of your interpretation"
}

Symbol normalization rules:
- GOLD/XAUUSD → "XAUUSD"
- SILVER → "XAGUSD"
- BTC/BITCOIN → "BTCUSD"
- ETH/ETHEREUM → "ETHUSD"
- NASDAQ/NAS100 → "NAS100"
- US30/DOW → "US30"
- CABLE → "GBPUSD"
- FIBER → "EURUSD"
- Standard forex pairs: EURUSD, GBPUSD, USDJPY, etc.

Management messages (return action=null, set management_type):
- "close all", "stop stop stop", "exit trades" → management_type: "close"
- "move SL to breakeven" → management_type: "move_sl"
- "partial TP hit" → management_type: "partial_tp"
- "cancel order" → management_type: "cancel"

If the message is clearly not a trading signal (greetings, news, etc), return confidence: 0.0 and action: null."""

VISION_PARSE_PROMPT = """You are a trading chart analyst. Look at this chart screenshot and extract any visible trade setup.

Return ONLY valid JSON with these fields:
{
  "action": "BUY" or "SELL" or null,
  "symbol": "MT5 symbol" or null (read from chart title/label),
  "entry": entry price as number or null,
  "sl": stop loss price as number or null,
  "tp_list": [take profit prices] or [],
  "confidence": 0.0 to 1.0,
  "reasoning": "what you see in the chart"
}

Look for:
- Chart title/symbol label
- Horizontal lines (SL/TP levels)
- Arrow annotations (BUY/SELL direction)
- Price levels marked on the chart
- Any text overlay with trade details"""


@dataclass
class LLMParseResult:
    action: str | None = None
    symbol: str | None = None
    entry: float | None = None
    sl: float | None = None
    tp_list: list[float] | None = None
    confidence: float = 0.0
    management_type: str | None = None
    reasoning: str = ""
    llm_used: bool = True
    error: str | None = None


class LLMFallback:
    """Async LLM fallback for signal parsing using OpenAI API."""

    def __init__(self, api_key: str | None = None, model: str = "gpt-4o-mini",
                 timeout: int = 15, max_retries: int = 2):
        self._api_key = api_key
        self._model = model
        self._timeout = timeout
        self._max_retries = max_retries
        self._base_url = "https://api.openai.com/v1/chat/completions"
        # Rate limiting
        self._request_times: list[float] = []
        self._rate_lock = threading.Lock()
        self._max_requests_per_minute = 60

    @property
    def is_configured(self) -> bool:
        return bool(self._api_key)

    def _check_rate_limit(self) -> bool:
        """Returns True if we can make a request, False if rate limited."""
        now = time.time()
        with self._rate_lock:
            # Remove entries older than 60 seconds
            self._request_times = [t for t in self._request_times if now - t < 60]
            if len(self._request_times) >= self._max_requests_per_minute:
                return False
            self._request_times.append(now)
            return True

    def _call_openai(self, messages: list[dict], max_tokens: int = 500) -> dict | None:
        """Make a single OpenAI API call. Returns parsed JSON or None."""
        if not self._api_key:
            return None

        if not self._check_rate_limit():
            logger.warning("LLM rate limit reached, skipping request")
            return None

        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self._model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": 0.1,  # Low temp for consistent parsing
            "response_format": {"type": "json_object"},
        }

        for attempt in range(self._max_retries):
            try:
                resp = requests.post(
                    self._base_url,
                    headers=headers,
                    json=payload,
                    timeout=self._timeout,
                )
                if resp.status_code == 429:
                    # Rate limited by OpenAI — back off
                    wait = min(2 ** attempt, 10)
                    logger.warning(f"OpenAI rate limited, waiting {wait}s")
                    time.sleep(wait)
                    continue
                resp.raise_for_status()
                data = resp.json()
                content = data["choices"][0]["message"]["content"]
                return json.loads(content)
            except requests.exceptions.Timeout:
                logger.warning(f"OpenAI timeout (attempt {attempt + 1})")
                continue
            except json.JSONDecodeError as exc:
                logger.warning(f"Failed to parse LLM JSON response: {exc}")
                return None
            except Exception as exc:
                logger.warning(f"OpenAI API error (attempt {attempt + 1}): {exc}")
                if attempt < self._max_retries - 1:
                    time.sleep(1)
                continue

        return None

    def parse_signal_text(self, text: str) -> LLMParseResult:
        """Parse a text signal using GPT-4o-mini. Synchronous (call from background thread)."""
        messages = [
            {"role": "system", "content": SIGNAL_PARSE_PROMPT},
            {"role": "user", "content": text},
        ]

        result = self._call_openai(messages)
        if result is None:
            return LLMParseResult(error="LLM call failed or not configured")

        return self._json_to_result(result)

    def parse_signal_image(self, image_bytes: bytes, mime_type: str = "image/png",
                           caption: str = "") -> LLMParseResult:
        """Parse a chart screenshot using GPT-4o-mini vision. Synchronous."""
        b64_image = base64.b64encode(image_bytes).decode("utf-8")

        content_parts = [
            {
                "type": "image_url",
                "image_url": {
                    "url": f"data:{mime_type};base64,{b64_image}",
                    "detail": "high",
                },
            },
        ]
        if caption:
            content_parts.insert(0, {"type": "text", "text": caption})

        messages = [
            {"role": "system", "content": VISION_PARSE_PROMPT},
            {"role": "user", "content": content_parts},
        ]

        result = self._call_openai(messages, max_tokens=800)
        if result is None:
            return LLMParseResult(error="Vision LLM call failed or not configured")

        return self._json_to_result(result)

    def _json_to_result(self, data: dict) -> LLMParseResult:
        """Convert JSON response to LLMParseResult."""
        try:
            tp_list = data.get("tp_list", [])
            if not isinstance(tp_list, list):
                tp_list = [tp_list] if tp_list else []

            return LLMParseResult(
                action=data.get("action"),
                symbol=data.get("symbol"),
                entry=_safe_float(data.get("entry")),
                sl=_safe_float(data.get("sl")),
                tp_list=[_safe_float(t) for t in tp_list if t is not None],
                confidence=float(data.get("confidence", 0.0)),
                management_type=data.get("management_type"),
                reasoning=data.get("reasoning", ""),
            )
        except Exception as exc:
            logger.warning(f"Failed to parse LLM result: {exc}")
            return LLMParseResult(error=f"Parse error: {exc}")


class LLMFallbackProcessor:
    """
    Manages async LLM fallback processing.

    When regex parser returns low confidence, this queues the message
    for LLM re-parsing in a background thread. If the LLM finds a valid
    signal, it calls the execution callback.
    """

    def __init__(self, llm: LLMFallback, execute_callback: Callable,
                 confidence_threshold: float = 0.5,
                 learning_callback: Callable | None = None,
                 learning_confidence_threshold: float = 0.9,
                 learning_auto_approve: bool = False):
        """
        Args:
            llm: LLMFallback instance
            execute_callback: function(user_id, signal_data) -> result
            confidence_threshold: Minimum LLM confidence to execute
        """
        self._llm = llm
        self._execute = execute_callback
        self._threshold = confidence_threshold
        self._learning_callback = learning_callback
        self._learning_threshold = learning_confidence_threshold
        self._learning_auto_approve = bool(learning_auto_approve)
        self._queue: list[dict] = []
        self._queue_lock = threading.Lock()
        self._running = False
        self._thread: threading.Thread | None = None
        # Stats
        self.stats = {
            "queued": 0,
            "processed": 0,
            "executed": 0,
            "skipped": 0,
            "errors": 0,
            "dropped": 0,
        }

    @property
    def is_running(self) -> bool:
        return self._running and self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if not self._llm.is_configured:
            logger.info("LLM fallback not configured (no API key) — disabled")
            return
        if self.is_running:
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._worker_loop, daemon=True, name="llm-fallback"
        )
        self._thread.start()
        logger.info("LLM fallback processor started")

    def stop(self) -> None:
        self._running = False

    def enqueue(self, user_id: str, channel_id: str, raw_text: str,
                message_id: int, sub: dict, log_callback: Callable | None = None) -> None:
        """Queue a message for LLM re-parsing."""
        if not self.is_running:
            return
        item = {
            "user_id": user_id,
            "channel_id": channel_id,
            "raw_text": raw_text,
            "message_id": message_id,
            "sub": sub,
            "log_callback": log_callback,
            "queued_at": time.time(),
        }
        with self._queue_lock:
            # Cap queue size to prevent memory issues
            if len(self._queue) < 500:
                self._queue.append(item)
                self.stats["queued"] += 1
            else:
                logger.warning("LLM fallback queue full, dropping message")

    def enqueue_image(self, user_id: str, channel_id: str, image_bytes: bytes,
                      mime_type: str, caption: str, message_id: int, sub: dict,
                      log_callback: Callable | None = None) -> None:
        """Queue an image for LLM vision parsing."""
        if not self.is_running:
            return
        item = {
            "user_id": user_id,
            "channel_id": channel_id,
            "image_bytes": image_bytes,
            "mime_type": mime_type,
            "caption": caption,
            "message_id": message_id,
            "sub": sub,
            "log_callback": log_callback,
            "queued_at": time.time(),
            "is_image": True,
        }
        with self._queue_lock:
            if len(self._queue) < 500:
                self._queue.append(item)
                self.stats["queued"] += 1

    def _worker_loop(self) -> None:
        """Background worker that processes LLM requests."""
        while self._running:
            item = None
            with self._queue_lock:
                if self._queue:
                    item = self._queue.pop(0)

            if item is None:
                time.sleep(0.5)
                continue

            # Skip items older than 30 seconds (stale)
            age = time.time() - item["queued_at"]
            if age > 30:
                logger.warning(f"[LLM] Dropping stale item (age={age:.1f}s): {item!r}")
                self.stats["dropped"] = self.stats.get("dropped", 0) + 1
                continue

            try:
                self._process_item(item)
                self.stats["processed"] += 1
            except Exception:
                logger.exception("LLM fallback processing error")
                self.stats["errors"] += 1

        logger.info("LLM fallback worker exited")

    def _process_item(self, item: dict) -> None:
        """Process a single queued item through the LLM."""
        is_image = item.get("is_image", False)

        if is_image:
            result = self._llm.parse_signal_image(
                item["image_bytes"], item.get("mime_type", "image/png"),
                item.get("caption", "")
            )
        else:
            result = self._llm.parse_signal_text(item["raw_text"])

        if result.error:
            logger.warning(f"LLM parse error: {result.error}")
            return

        if not result.action or not result.symbol:
            self.stats["skipped"] += 1
            return

        if result.confidence < self._threshold:
            self.stats["skipped"] += 1
            return

        # Learn safe reusable pattern from high-confidence text parses.
        if (not is_image
                and item.get("raw_text")
                and result.confidence >= self._learning_threshold
                and self._learning_callback is not None):
            try:
                self._learning_callback(
                    item["user_id"],
                    item["raw_text"],
                    result,
                    self._learning_auto_approve,
                )
            except Exception:
                logger.exception("Failed to persist learned LLM pattern")

        # Build signal data for execution
        sub = item["sub"]
        signal_data = {
            "action": result.action,
            "symbol": result.symbol,
            "lot_size_pct": sub.get("risk_pct", 1.0),
            "script_name": sub.get("script_name", "Telegram"),
        }
        if result.sl is not None:
            signal_data["sl"] = result.sl
        if result.tp_list:
            signal_data["tp"] = result.tp_list[0]

        # Execute
        try:
            exec_result = self._execute(item["user_id"], signal_data)
            status_code = exec_result.get("status_code", 500)

            # Log if callback provided
            if item.get("log_callback"):
                import uuid as _uuid
                log_entry = {
                    "log_id": str(_uuid.uuid4()),
                    "channel_id": item["channel_id"],
                    "user_id": item["user_id"],
                    "telegram_message_id": item["message_id"],
                    "raw_text": item.get("raw_text", "[image]")[:2000],
                    "parsed_action": result.action,
                    "parsed_symbol": result.symbol,
                    "parsed_entry": result.entry,
                    "parsed_sl": result.sl,
                    "parsed_tp": json.dumps(result.tp_list) if result.tp_list else None,
                    "parse_confidence": result.confidence,
                    "execution_status": "executed" if status_code < 400 else "failed",
                    "execution_detail": f"LLM fallback: {result.reasoning}",
                    "command_id": exec_result.get("command_id"),
                    "created_at": time.time(),
                }
                item["log_callback"](log_entry)

            if status_code < 400:
                self.stats["executed"] += 1
            else:
                self.stats["skipped"] += 1
                logger.warning(f"LLM signal execution failed: {exec_result}")

        except Exception as exc:
            logger.exception(f"LLM fallback execution error: {exc}")
            self.stats["errors"] += 1


def _safe_float(val) -> float | None:
    """Safely convert a value to float."""
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None
