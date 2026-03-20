"""
Telegram Bot Manager
====================
Manages a single shared Telegram bot that listens for signals across
all connected channels. Uses getUpdates long-polling (no webhooks needed).

Architecture:
  - TelegramAPI:       Thin HTTP wrapper around Telegram Bot API (uses requests)
  - TelegramBotRunner: Runs getUpdates polling in a daemon thread
  - SignalMerger:      Merges multi-message signals (e.g. "BUY GOLD" + "SL 2320 TP 2380")
  - TelegramBotManager: Singleton that wires the bot to the signal parser + execution pipeline

Features:
  - Channel-scoped close: "STOP STOP STOP" closes only positions from that channel
  - Signal merging: Detects incomplete signals and waits for follow-up messages
  - LLM async fallback: Low-confidence signals queued for GPT-4o-mini re-parsing
  - Photo/screenshot parsing: Images sent to LLM vision for chart analysis
"""

from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from typing import Callable

import requests

from telegram_signal_parser import ParsedSignal, parse_telegram_message

logger = logging.getLogger("telegram_bot_manager")


# ---------------------------------------------------------------------------
# Telegram Bot HTTP API wrapper
# ---------------------------------------------------------------------------
class TelegramAPI:
    """Thin wrapper around the Telegram Bot HTTP API."""

    def __init__(self, token: str, timeout: int = 10):
        self.base_url = f"https://api.telegram.org/bot{token}"
        self.timeout = timeout

    def get_me(self) -> dict:
        """Validate bot token and return bot info."""
        resp = requests.get(f"{self.base_url}/getMe", timeout=self.timeout)
        resp.raise_for_status()
        data = resp.json()
        if not data.get("ok"):
            raise RuntimeError(f"getMe failed: {data}")
        return data["result"]

    def get_updates(self, offset: int | None = None, poll_timeout: int = 30) -> list[dict]:
        """Long-poll for new updates."""
        params: dict = {"timeout": poll_timeout, "allowed_updates": '["message","channel_post"]'}
        if offset is not None:
            params["offset"] = offset
        resp = requests.get(
            f"{self.base_url}/getUpdates",
            params=params,
            timeout=poll_timeout + self.timeout,  # HTTP timeout > poll timeout
        )
        resp.raise_for_status()
        data = resp.json()
        if not data.get("ok"):
            raise RuntimeError(f"getUpdates failed: {data}")
        return data.get("result", [])

    def get_chat(self, chat_id: str) -> dict:
        """Get info about a chat. Used to verify bot access."""
        resp = requests.get(
            f"{self.base_url}/getChat",
            params={"chat_id": chat_id},
            timeout=self.timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        if not data.get("ok"):
            raise RuntimeError(f"getChat failed: {data}")
        return data["result"]

    def get_file(self, file_id: str) -> bytes:
        """Download a file from Telegram servers. Returns raw bytes."""
        # Step 1: get file path
        resp = requests.get(
            f"{self.base_url}/getFile",
            params={"file_id": file_id},
            timeout=self.timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        if not data.get("ok"):
            raise RuntimeError(f"getFile failed: {data}")
        file_path = data["result"]["file_path"]

        # Step 2: download file
        download_url = f"https://api.telegram.org/file/bot{self.base_url.split('/bot')[1]}/{file_path}"
        resp = requests.get(download_url, timeout=30)
        resp.raise_for_status()
        return resp.content


# ---------------------------------------------------------------------------
# Signal Merger — handles multi-message signals
# ---------------------------------------------------------------------------
class SignalMerger:
    """
    Merges incomplete signals that arrive as multiple messages.

    Example:
      Message 1: "BUY GOLD"         → has action + symbol but no SL/TP
      Message 2: "SL 2320 TP 2380"  → has SL/TP but no action/symbol
      → Merged into complete signal: BUY XAUUSD SL 2320 TP 2380

    Holds incomplete signals for up to `merge_window` seconds before
    executing them as-is (or discarding if still too incomplete).
    """

    def __init__(self, merge_window: float = 8.0):
        self._merge_window = merge_window
        self._pending: dict[str, dict] = {}  # key: "{chat_id}" → pending signal info
        self._lock = threading.Lock()

    def try_merge(self, chat_id: str, parsed: ParsedSignal, raw_text: str,
                  message_id: int) -> tuple[ParsedSignal | None, bool]:
        """
        Try to merge this signal with a pending incomplete one.

        Returns:
            (merged_signal, should_wait)
            - If merged_signal is not None: execute it now
            - If should_wait is True: signal is incomplete, stored for merging
            - If both None/False: signal is complete, execute as normal
        """
        now = time.time()

        with self._lock:
            # Clean expired entries
            expired = [k for k, v in self._pending.items()
                       if now - v["time"] > self._merge_window]
            for k in expired:
                del self._pending[k]

            pending = self._pending.get(chat_id)

            # Case 1: We have a pending incomplete signal for this chat
            if pending and now - pending["time"] <= self._merge_window:
                merged = self._merge_signals(pending["parsed"], parsed)
                del self._pending[chat_id]
                if merged.action and merged.symbol:
                    return merged, False
                # Still incomplete even after merge — store again
                self._pending[chat_id] = {
                    "parsed": merged, "raw_text": raw_text,
                    "message_id": message_id, "time": pending["time"],
                }
                return None, True

            # Case 2: This signal is incomplete (has action+symbol but no SL/TP,
            # or has SL/TP but no action/symbol)
            if self._is_incomplete(parsed):
                self._pending[chat_id] = {
                    "parsed": parsed, "raw_text": raw_text,
                    "message_id": message_id, "time": now,
                }
                return None, True

            # Case 3: Signal is complete, execute normally
            return None, False

    def flush_expired(self) -> list[tuple[str, ParsedSignal, str, int]]:
        """
        Returns expired pending signals that should be executed or discarded.
        Returns list of (chat_id, parsed, raw_text, message_id).
        """
        now = time.time()
        results = []
        with self._lock:
            expired_keys = [k for k, v in self._pending.items()
                            if now - v["time"] > self._merge_window]
            for k in expired_keys:
                p = self._pending.pop(k)
                if p["parsed"].action and p["parsed"].symbol:
                    results.append((k, p["parsed"], p["raw_text"], p["message_id"]))
        return results

    def _is_incomplete(self, parsed: ParsedSignal) -> bool:
        """A signal is incomplete if it has action+symbol but no SL/TP, or vice versa."""
        has_action_symbol = bool(parsed.action and parsed.symbol)
        has_levels = bool(parsed.sl or parsed.tp_list)

        # Has action+symbol but no levels → might get SL/TP in next message
        if has_action_symbol and not has_levels and parsed.confidence < 0.9:
            return True

        # Has levels but no action/symbol → complement of a previous message
        if has_levels and not has_action_symbol:
            return True

        return False

    def _merge_signals(self, old: ParsedSignal, new: ParsedSignal) -> ParsedSignal:
        """Merge two parsed signals, preferring newer values where both exist."""
        return ParsedSignal(
            action=new.action or old.action,
            symbol=new.symbol or old.symbol,
            entry=new.entry if new.entry is not None else old.entry,
            sl=new.sl if new.sl is not None else old.sl,
            tp_list=new.tp_list if new.tp_list else old.tp_list,
            confidence=max(old.confidence, new.confidence) + 0.1,  # boost for merged
            raw_text=f"{old.raw_text}\n{new.raw_text}",
            skip_reason=None,
            management_type=new.management_type or old.management_type,
        )


# ---------------------------------------------------------------------------
# Bot polling runner (single thread)
# ---------------------------------------------------------------------------
class TelegramBotRunner:
    """Runs getUpdates long-polling for a single bot token in a daemon thread."""

    def __init__(self, api: TelegramAPI,
                 message_callback: Callable[[str, str, int], None],
                 photo_callback: Callable[[str, str, str, int], None] | None = None):
        """
        Args:
            api: TelegramAPI instance
            message_callback: called with (chat_id, text, message_id)
            photo_callback: called with (chat_id, file_id, caption, message_id)
        """
        self._api = api
        self._callback = message_callback
        self._photo_callback = photo_callback
        self._offset: int | None = None
        self._running = False
        self._thread: threading.Thread | None = None
        self._backoff = 1  # seconds, exponential backoff on errors

    @property
    def is_running(self) -> bool:
        return self._running and self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        """Start the polling loop in a daemon thread."""
        if self.is_running:
            return
        self._running = True
        self._backoff = 1
        self._thread = threading.Thread(target=self._poll_loop, daemon=True, name="tg-poll")
        self._thread.start()
        logger.info("Bot polling started")

    def stop(self) -> None:
        """Signal the polling loop to stop."""
        self._running = False
        logger.info("Bot polling stop requested")

    def _poll_loop(self) -> None:
        """Main polling loop — runs in background thread."""
        while self._running:
            try:
                updates = self._api.get_updates(offset=self._offset, poll_timeout=30)
                self._backoff = 1  # reset on success

                for update in updates:
                    self._offset = update["update_id"] + 1
                    self._process_update(update)

            except requests.exceptions.ConnectionError:
                logger.warning(f"Telegram connection error, retrying in {self._backoff}s")
                time.sleep(self._backoff)
                self._backoff = min(self._backoff * 2, 60)
            except requests.exceptions.Timeout:
                # Long-poll timeout is normal, just retry
                continue
            except Exception:
                logger.exception(f"Unexpected error in poll loop, retrying in {self._backoff}s")
                time.sleep(self._backoff)
                self._backoff = min(self._backoff * 2, 60)

        logger.info("Bot polling loop exited")

    def _process_update(self, update: dict) -> None:
        """Extract text/photo from an update and call the appropriate callback."""
        # Channel posts come as "channel_post", group/private as "message"
        message = update.get("channel_post") or update.get("message")
        if not message:
            return

        chat = message.get("chat", {})
        chat_id = str(chat.get("id", ""))
        message_id = message.get("message_id", 0)
        if not chat_id:
            return

        # Handle photos (chart screenshots)
        photo = message.get("photo")
        if photo and self._photo_callback:
            # Telegram sends multiple sizes; pick the largest
            largest = max(photo, key=lambda p: p.get("file_size", 0))
            file_id = largest.get("file_id", "")
            caption = message.get("caption", "")
            if file_id:
                try:
                    self._photo_callback(chat_id, file_id, caption, message_id)
                except Exception:
                    logger.exception(f"Error in photo callback for chat {chat_id}")
            return  # photos with captions are handled by photo_callback

        # Handle text messages
        text = message.get("text")
        if not text:
            return

        try:
            self._callback(chat_id, text, message_id)
        except Exception:
            logger.exception(f"Error in message callback for chat {chat_id}")


# ---------------------------------------------------------------------------
# Bot Manager (singleton, started at app boot)
# ---------------------------------------------------------------------------
class TelegramBotManager:
    """
    Manages the shared signal bot. Wires incoming messages to the parser
    and fans out execution to all subscribed users.

    Features:
      - Channel-scoped close: management messages close only positions from that channel
      - Signal merging: incomplete signals are held and merged with follow-ups
      - LLM async fallback: low-confidence signals queued for GPT-4o-mini
      - Photo parsing: chart screenshots sent to LLM vision
    """

    def __init__(self, store, app, process_callback: Callable,
                 bot_token: str | None = None,
                 close_callback: Callable | None = None,
                 llm_processor=None):
        """
        Args:
            store:            BridgeStore instance (for DB queries)
            app:              Flask app (for test_request_context)
            process_callback: function(user_id, signal_dict) -> result dict
            bot_token:        Telegram bot token (from env)
            close_callback:   function(user_id, channel_id) -> result dict (channel-scoped close)
            llm_processor:    LLMFallbackProcessor instance (optional)
        """
        self._store = store
        self._app = app
        self._process_callback = process_callback
        self._close_callback = close_callback
        self._llm_processor = llm_processor
        self._bot_token = bot_token
        self._runner: TelegramBotRunner | None = None
        self._api: TelegramAPI | None = None
        self._bot_info: dict | None = None
        self._lock = threading.Lock()
        # Track processed message IDs to prevent duplicates
        self._processed_messages: set[str] = set()
        self._processed_messages_lock = threading.Lock()
        self._max_processed_cache = 10000
        # Signal merger for multi-message signals
        self._merger = SignalMerger(merge_window=8.0)
        self._merger_thread: threading.Thread | None = None

    @property
    def bot_username(self) -> str | None:
        return self._bot_info.get("username") if self._bot_info else None

    @property
    def is_running(self) -> bool:
        return self._runner is not None and self._runner.is_running

    def start(self) -> None:
        """Start the shared signal bot. No-op if no token configured."""
        if not self._bot_token:
            logger.info("No TELEGRAM_SIGNAL_BOT_TOKEN configured — signal bot disabled")
            return

        with self._lock:
            if self.is_running:
                logger.info("Bot already running")
                return

            try:
                self._api = TelegramAPI(self._bot_token)
                self._bot_info = self._api.get_me()
                logger.info(f"Telegram signal bot validated: @{self._bot_info.get('username')}")
            except Exception:
                logger.exception("Failed to validate Telegram bot token")
                return

            self._runner = TelegramBotRunner(
                self._api, self._on_message, self._on_photo
            )
            self._runner.start()

            # Start merger flush thread
            self._start_merger_thread()

            # Start LLM fallback if configured
            if self._llm_processor:
                self._llm_processor.start()

    def stop(self) -> None:
        """Stop the bot polling loop."""
        with self._lock:
            if self._runner:
                self._runner.stop()
                self._runner = None
                logger.info("Telegram signal bot stopped")
            if self._llm_processor:
                self._llm_processor.stop()

    def verify_channel_access(self, chat_id: str) -> dict:
        """
        Verify the bot can access a channel. Returns chat info dict.
        Raises RuntimeError if bot has no access.
        """
        if not self._api:
            raise RuntimeError("Signal bot is not running")
        return self._api.get_chat(chat_id)

    def _start_merger_thread(self) -> None:
        """Start background thread that flushes expired merged signals."""
        def _flush_loop():
            while self._runner and self._runner.is_running:
                try:
                    expired = self._merger.flush_expired()
                    for chat_id, parsed, raw_text, message_id in expired:
                        subs = self._store.get_subscriptions_for_chat(chat_id)
                        for sub in subs:
                            try:
                                self._execute_for_subscription(
                                    sub, parsed, raw_text, message_id
                                )
                            except Exception:
                                logger.exception("Error executing merged signal")
                except Exception:
                    logger.exception("Error in merger flush loop")
                time.sleep(2)

        self._merger_thread = threading.Thread(
            target=_flush_loop, daemon=True, name="tg-merger-flush"
        )
        self._merger_thread.start()

    def _on_message(self, chat_id: str, text: str, message_id: int) -> None:
        """
        Called by the BotRunner for every text message received.
        Fans out to all subscribed users for this chat_id.
        """
        # Dedup: skip if we've already processed this message
        dedup_key = f"{chat_id}:{message_id}"
        with self._processed_messages_lock:
            if dedup_key in self._processed_messages:
                return
            self._processed_messages.add(dedup_key)
            # Evict old entries if cache gets too large
            if len(self._processed_messages) > self._max_processed_cache:
                # Remove ~half the entries (they're old enough)
                to_remove = list(self._processed_messages)[:self._max_processed_cache // 2]
                for key in to_remove:
                    self._processed_messages.discard(key)

        # Look up all subscriptions for this chat_id
        subscriptions = self._store.get_subscriptions_for_chat(chat_id)
        if not subscriptions:
            return  # no users subscribed to this channel

        # Parse the signal once (shared across all subscribers)
        parsed = parse_telegram_message(text)

        # Handle management messages (channel-scoped close)
        if parsed.management_type:
            self._handle_management_message(chat_id, parsed, text, message_id, subscriptions)
            return

        # Try signal merging for incomplete signals
        merged, should_wait = self._merger.try_merge(chat_id, parsed, text, message_id)
        if should_wait:
            logger.debug(f"Signal incomplete for chat {chat_id}, waiting for follow-up")
            return
        if merged is not None:
            parsed = merged
            logger.info(f"Merged signal for chat {chat_id}: {parsed.action} {parsed.symbol}")

        # Check if LLM fallback is needed (low confidence, not skipped for other reasons)
        if (parsed.skip_reason and "low confidence" in (parsed.skip_reason or "")
                and self._llm_processor and self._llm_processor.is_running):
            for sub in subscriptions:
                self._llm_processor.enqueue(
                    user_id=sub["user_id"],
                    channel_id=sub["channel_id"],
                    raw_text=text,
                    message_id=message_id,
                    sub=sub,
                    log_callback=self._store.add_telegram_signal_log,
                )
            return

        # Fan out to each subscribed user
        for sub in subscriptions:
            try:
                self._execute_for_subscription(sub, parsed, text, message_id)
            except Exception:
                logger.exception(
                    f"Error executing signal for user {sub.get('user_id')} "
                    f"channel {sub.get('channel_id')}"
                )

    def _on_photo(self, chat_id: str, file_id: str, caption: str, message_id: int) -> None:
        """
        Called by the BotRunner for photo messages (chart screenshots).
        Downloads the image and queues for LLM vision parsing.
        """
        if not self._llm_processor or not self._llm_processor.is_running:
            return  # no LLM configured, skip photos

        if not self._api:
            return

        subscriptions = self._store.get_subscriptions_for_chat(chat_id)
        if not subscriptions:
            return

        # If there's a caption, try regex parsing first
        if caption and caption.strip():
            parsed = parse_telegram_message(caption)
            if parsed.action and parsed.symbol and not parsed.skip_reason:
                # Caption contains a valid signal — execute directly
                for sub in subscriptions:
                    try:
                        self._execute_for_subscription(sub, parsed, caption, message_id)
                    except Exception:
                        logger.exception("Error executing caption signal")
                return

        # Download image and queue for LLM vision
        try:
            image_bytes = self._api.get_file(file_id)
        except Exception:
            logger.exception(f"Failed to download photo from chat {chat_id}")
            return

        for sub in subscriptions:
            self._llm_processor.enqueue_image(
                user_id=sub["user_id"],
                channel_id=sub["channel_id"],
                image_bytes=image_bytes,
                mime_type="image/jpeg",
                caption=caption or "",
                message_id=message_id,
                sub=sub,
                log_callback=self._store.add_telegram_signal_log,
            )

    def _handle_management_message(self, chat_id: str, parsed: ParsedSignal,
                                   raw_text: str, message_id: int,
                                   subscriptions: list[dict]) -> None:
        """
        Handle management messages (close, move SL, etc).
        Channel-scoped: only affects positions opened by signals from this channel.
        """
        for sub in subscriptions:
            user_id = sub["user_id"]
            channel_id = sub["channel_id"]

            log_entry = {
                "log_id": str(uuid.uuid4()),
                "channel_id": channel_id,
                "user_id": user_id,
                "telegram_message_id": message_id,
                "raw_text": raw_text[:2000],
                "parsed_action": None,
                "parsed_symbol": None,
                "parse_confidence": 0.0,
                "created_at": time.time(),
            }

            if parsed.management_type == "close" and self._close_callback:
                try:
                    result = self._close_callback(user_id, channel_id)
                    closed_count = result.get("closed_count", 0)
                    log_entry["execution_status"] = "executed"
                    log_entry["execution_detail"] = (
                        f"Channel-scoped close: {closed_count} position(s) closed"
                    )
                    logger.info(
                        f"Channel-scoped close for user {user_id}, "
                        f"channel {channel_id}: {closed_count} positions"
                    )
                except Exception as exc:
                    log_entry["execution_status"] = "failed"
                    log_entry["execution_detail"] = f"Close failed: {exc}"
                    logger.exception(f"Channel-scoped close failed for user {user_id}")
            else:
                # Other management types — log but don't execute yet
                log_entry["execution_status"] = "skipped"
                log_entry["execution_detail"] = (
                    f"management message: {parsed.management_type}"
                )

            self._store.add_telegram_signal_log(log_entry)

    def _execute_for_subscription(
        self, sub: dict, parsed, raw_text: str, message_id: int
    ) -> None:
        """Process a parsed signal for one user subscription."""
        user_id = sub["user_id"]
        channel_id = sub["channel_id"]

        # Build log entry
        log_entry = {
            "log_id": str(uuid.uuid4()),
            "channel_id": channel_id,
            "user_id": user_id,
            "telegram_message_id": message_id,
            "raw_text": raw_text[:2000],  # truncate very long messages
            "parsed_action": parsed.action,
            "parsed_symbol": parsed.symbol,
            "parsed_entry": parsed.entry,
            "parsed_sl": parsed.sl,
            "parsed_tp": json.dumps(parsed.tp_list) if parsed.tp_list else None,
            "parse_confidence": parsed.confidence,
            "created_at": time.time(),
        }

        # Skip if not a valid trade signal
        if parsed.skip_reason:
            log_entry["execution_status"] = "skipped"
            log_entry["execution_detail"] = parsed.skip_reason
            self._store.add_telegram_signal_log(log_entry)
            return

        # Apply channel filters
        filter_reason = self._apply_channel_filters(sub, parsed)
        if filter_reason:
            log_entry["execution_status"] = "filtered"
            log_entry["execution_detail"] = filter_reason
            self._store.add_telegram_signal_log(log_entry)
            return

        # Build signal dict for _process_signal_for_user
        signal_data = {
            "action": parsed.action,
            "symbol": parsed.symbol,
            "lot_size_pct": sub.get("risk_pct", 1.0),
            "script_name": sub.get("script_name", "Telegram"),
        }
        if parsed.sl is not None:
            signal_data["sl"] = parsed.sl
        if parsed.tp_list:
            signal_data["tp"] = parsed.tp_list[0]  # first TP for MVP

        # Execute
        try:
            result = self._process_callback(user_id, signal_data)
            status_code = result.get("status_code", 500)
            if status_code < 400:
                log_entry["execution_status"] = "executed"
                log_entry["command_id"] = result.get("command_id")
                log_entry["execution_detail"] = json.dumps(result)
            else:
                log_entry["execution_status"] = "failed"
                log_entry["execution_detail"] = result.get("error", "unknown error")
        except Exception as exc:
            log_entry["execution_status"] = "failed"
            log_entry["execution_detail"] = str(exc)

        self._store.add_telegram_signal_log(log_entry)

    def _apply_channel_filters(self, sub: dict, parsed) -> str | None:
        """
        Apply per-channel filters. Returns a reason string if filtered, None if OK.
        """
        # Check allowed symbols
        allowed_raw = sub.get("allowed_symbols")
        if allowed_raw:
            try:
                allowed = json.loads(allowed_raw)
                if isinstance(allowed, list) and parsed.symbol not in allowed:
                    return f"symbol {parsed.symbol} not in allowed list"
            except (json.JSONDecodeError, TypeError):
                pass

        # Check max trades per day
        max_trades = sub.get("max_trades_per_day", 10)
        if max_trades > 0:
            today_count = self._store.count_channel_trades_today(sub["channel_id"])
            if today_count >= max_trades:
                return f"max trades/day ({max_trades}) reached"

        return None
