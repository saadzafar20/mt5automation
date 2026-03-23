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

    def send_message(self, chat_id: str, text: str) -> bool:
        """Send a text message to a chat/group. Returns True on success."""
        try:
            resp = requests.post(
                f"{self.base_url}/sendMessage",
                json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
                timeout=self.timeout,
            )
            data = resp.json()
            if not data.get("ok"):
                logger.warning(f"sendMessage failed for chat {chat_id}: {data}")
                return False
            return True
        except Exception as exc:
            logger.warning(f"sendMessage error for chat {chat_id}: {exc}")
            return False

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
                 photo_callback: Callable[[str, str, str, int], None] | None = None,
                 command_callback: Callable | None = None):
        """
        Args:
            api: TelegramAPI instance
            message_callback: called with (chat_id, text, message_id)
            photo_callback: called with (chat_id, file_id, caption, message_id)
            command_callback: called with (from_user_id, chat_id, text, message_id, is_private)
                              for bot command messages (text starting with '/')
        """
        self._api = api
        self._callback = message_callback
        self._photo_callback = photo_callback
        self._command_callback = command_callback
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

        # Route bot commands (/start, /status, etc.) to the command callback
        if text.strip().startswith("/") and self._command_callback:
            from_user = message.get("from", {})
            from_user_id = str(from_user.get("id", ""))
            is_private = chat.get("type") == "private"
            try:
                self._command_callback(from_user_id, chat_id, text, message_id, is_private,
                                       from_user)
            except Exception:
                logger.exception(f"Error in command callback for chat {chat_id}")
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
                 llm_processor=None,
                 admin_telegram_id: str | None = None):
        """
        Args:
            store:              BridgeStore instance (for DB queries)
            app:                Flask app (for test_request_context)
            process_callback:   function(user_id, signal_dict) -> result dict
            bot_token:          Telegram bot token (from env)
            close_callback:     function(user_id, channel_id) -> result dict
            llm_processor:      LLMFallbackProcessor instance (optional)
            admin_telegram_id:  Telegram user_id of the admin (from env)
        """
        self._store = store
        self._app = app
        self._process_callback = process_callback
        self._close_callback = close_callback
        self._llm_processor = llm_processor
        self._bot_token = bot_token
        self._admin_telegram_id = admin_telegram_id
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
                self._api, self._on_message, self._on_photo,
                command_callback=self._on_command,
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

        # Pips-format signals skip merging — they need LLM interpretation, not completion
        _needs_llm = parsed.skip_reason and "pips format" in parsed.skip_reason

        # Try signal merging for incomplete signals (skip for pips-format — LLM handles those)
        if not _needs_llm:
            merged, should_wait = self._merger.try_merge(chat_id, parsed, text, message_id)
            if should_wait:
                logger.debug(f"Signal incomplete for chat {chat_id}, waiting for follow-up")
                return
            if merged is not None:
                parsed = merged
                logger.info(f"Merged signal for chat {chat_id}: {parsed.action} {parsed.symbol}")

        # Check if LLM fallback is needed.
        # Route to LLM when regex failed to produce a complete signal but the text
        # has enough signal-like content (confidence > 0.0, meaning at least one of
        # action/symbol was found).  This catches:
        #   - "missing action or symbol": one field found, LLM can fill the other
        #   - "low confidence — likely not a trade signal": ambiguous parse
        #   - "pips format signal": values in pips, not absolute prices — LLM converts
        # Pure noise ("Good morning!") has confidence == 0.0 and is skipped.
        _llm_worthy = ("low confidence", "missing action or symbol", "pips format")
        if (parsed.skip_reason
                and any(r in parsed.skip_reason for r in _llm_worthy)
                and parsed.confidence > 0.0
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
                    self._reply_to_chat(
                        chat_id=sub.get("chat_id", ""),
                        text=f"Closed {closed_count} position(s) for this channel.",
                    )
                except Exception as exc:
                    log_entry["execution_status"] = "failed"
                    log_entry["execution_detail"] = f"Close failed: {exc}"
                    logger.exception(f"Channel-scoped close failed for user {user_id}")
                    self._reply_to_chat(
                        chat_id=sub.get("chat_id", ""),
                        text=f"Close failed: {exc}",
                    )
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

        # Fetch user defaults (default_lot_size, default_sl_pips, default_tp_pips)
        user_defaults = {}
        try:
            user_defaults = self._store.get_user_defaults(user_id)
        except Exception:
            pass

        # Build signal dict for _process_signal_for_user
        signal_data = {
            "action": parsed.action,
            "symbol": parsed.symbol,
            "script_name": sub.get("script_name", "Telegram"),
        }

        # Lot size: use default_lot_size if set, otherwise use channel risk_pct
        default_lot = user_defaults.get("default_lot_size")
        if default_lot:
            signal_data["size"] = float(default_lot)
        else:
            signal_data["lot_size_pct"] = sub.get("risk_pct", 1.0)

        # SL: use parsed value, or fall back to pips default
        if parsed.sl is not None:
            signal_data["sl"] = parsed.sl
        elif user_defaults.get("default_sl_pips"):
            signal_data["sl_pips"] = float(user_defaults["default_sl_pips"])

        # TP: use parsed value, or fall back to pips default
        if parsed.tp_list:
            signal_data["tp"] = parsed.tp_list[0]
        elif user_defaults.get("default_tp_pips"):
            signal_data["tp_pips"] = float(user_defaults["default_tp_pips"])

        # Execute
        try:
            result = self._process_callback(user_id, signal_data)
            status_code = result.get("status_code", 500)
            mode = result.get("mode", "relay")
            if status_code < 400:
                log_entry["execution_status"] = "executed"
                log_entry["command_id"] = result.get("command_id")
                log_entry["execution_detail"] = json.dumps(result)
                action_lbl = "executed" if mode == "managed-vps" else "queued"
                group_text = (
                    f"Trade {action_lbl}: <b>{parsed.action} {parsed.symbol}</b>"
                    + (f" SL {parsed.sl}" if parsed.sl else "")
                    + (f" TP {parsed.tp_list[0]}" if parsed.tp_list else "")
                )
                # Reply to group channel
                self._reply_to_chat(chat_id=sub.get("chat_id", ""), text=group_text)
                # Private confirmation to the user
                private_text = (
                    f"✅ Trade {action_lbl}: {parsed.action} {parsed.symbol}"
                    + (f" @ {result.get('result', {}).get('price', '')}" if mode == "managed-vps" else "")
                    + (f" SL {parsed.sl}" if parsed.sl else "")
                    + (f" TP {parsed.tp_list[0]}" if parsed.tp_list else "")
                )
                self._send_private(user_id, private_text)
            else:
                log_entry["execution_status"] = "failed"
                log_entry["execution_detail"] = result.get("error", "unknown error")
                err_msg = result.get("error", "unknown error")
                self._reply_to_chat(
                    chat_id=sub.get("chat_id", ""),
                    text=f"Trade failed: <b>{parsed.action} {parsed.symbol}</b> — {err_msg}",
                )
                self._send_private(user_id, f"❌ Trade failed: {parsed.action} {parsed.symbol} — {err_msg}")
        except Exception as exc:
            log_entry["execution_status"] = "failed"
            log_entry["execution_detail"] = str(exc)
            self._reply_to_chat(
                chat_id=sub.get("chat_id", ""),
                text=f"Trade error: <b>{parsed.action} {parsed.symbol}</b> — {exc}",
            )
            self._send_private(user_id, f"❌ Trade error: {parsed.action} {parsed.symbol} — {exc}")

        self._store.add_telegram_signal_log(log_entry)

    def _reply_to_chat(self, chat_id: str, text: str) -> None:
        """Send a reply message to the originating chat/group. No-op if bot not running."""
        if not chat_id or not self._api:
            return
        try:
            self._api.send_message(chat_id, text)
        except Exception as exc:
            logger.warning(f"Failed to send reply to chat {chat_id}: {exc}")

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

        # Check per-user symbol whitelist (Section 3)
        if parsed.symbol:
            _uid = sub.get("user_id", "")
            allowed_syms = []
            try:
                allowed_syms = self._store.get_user_allowed_symbols(_uid)
            except Exception:
                pass
            if allowed_syms and parsed.symbol not in allowed_syms:
                return f"symbol {parsed.symbol} not in your allowed symbols list"

        return None

    # ── Private message helpers ─────────────────────────────────────────────

    def _send_private(self, user_id: str, text: str) -> None:
        """Send a private Telegram message to the user via the shared bot (Section 6)."""
        if not self._api:
            return
        try:
            private_chat_id = self._store.get_private_chat_id_for_user(user_id)
            if private_chat_id:
                self._api.send_message(private_chat_id, text)
        except Exception as exc:
            logger.debug(f"Failed to send private message to {user_id}: {exc}")

    def send_session_notification(self, user_id: str, message: str) -> None:
        """Send a session status notification (Section 7 — called from heartbeat worker)."""
        self._send_private(user_id, message)

    # ── Bot command handling ────────────────────────────────────────────────

    def _on_command(self, from_user_id: str, chat_id: str, text: str,
                    message_id: int, is_private: bool, from_user: dict) -> None:
        """Route a bot command message to the appropriate handler."""
        parts = text.strip().split()
        cmd = parts[0].lower().split("@")[0]  # strip bot username suffix
        args = parts[1:]

        # Admin commands (Section 8) — only from the configured admin
        if cmd == "/admin" and self._admin_telegram_id:
            if from_user_id == str(self._admin_telegram_id):
                self._handle_admin_command(chat_id, args)
            else:
                self._reply_to_chat(chat_id, "⛔ Admin access denied.")
            return

        # All other commands require the user to be linked (/start is the exception)
        if cmd == "/start":
            self._cmd_start(from_user_id, chat_id, args, from_user)
            return

        # Look up linked user
        user_id = None
        try:
            user_id = self._store.get_user_id_by_telegram_id(from_user_id)
        except Exception:
            pass

        if not user_id and cmd not in ("/help",):
            self._reply_to_chat(
                chat_id,
                "🔗 Please link your account first.\n"
                "Visit your PlatAlgo dashboard → Settings → Link Telegram, "
                "then send the /start command with your one-time token.",
            )
            return

        dispatch = {
            "/status":       self._cmd_status,
            "/disconnect":   self._cmd_disconnect,
            "/setlot":       self._cmd_setlot,
            "/setsl":        self._cmd_setsl,
            "/settp":        self._cmd_settp,
            "/addsymbol":    self._cmd_addsymbol,
            "/removesymbol": self._cmd_removesymbol,
            "/symbols":      self._cmd_symbols,
            "/subscribe":    self._cmd_subscribe,
            "/unsubscribe":  self._cmd_unsubscribe,
            "/groups":       self._cmd_groups,
            "/help":         self._cmd_help,
        }
        handler = dispatch.get(cmd)
        if handler:
            handler(user_id, chat_id, args)
        else:
            self._reply_to_chat(chat_id, "Unknown command. Send /help for the list.")

    # ── /start ──────────────────────────────────────────────────────────────

    def _cmd_start(self, from_user_id: str, chat_id: str, args: list,
                   from_user: dict) -> None:
        """Link Telegram account to PlatAlgo account via one-time token."""
        if not args:
            self._reply_to_chat(
                chat_id,
                "👋 Welcome to PlatAlgo!\n\n"
                "To link your account, visit your dashboard → Settings → Link Telegram "
                "and use the generated link.\n\n"
                "Send /help for a list of commands.",
            )
            return

        token = args[0].strip()
        try:
            user_id = self._store.consume_telegram_link_token(token)
        except Exception as exc:
            self._reply_to_chat(chat_id, f"❌ Failed to link account: {exc}")
            return

        if not user_id:
            self._reply_to_chat(
                chat_id,
                "❌ Invalid or expired token. Please generate a new link from your dashboard.",
            )
            return

        username = from_user.get("username") or from_user.get("first_name", "")
        try:
            self._store.link_telegram_user(from_user_id, user_id, chat_id, username)
        except Exception as exc:
            self._reply_to_chat(chat_id, f"❌ Failed to save link: {exc}")
            return

        self._reply_to_chat(
            chat_id,
            f"✅ Account linked! Welcome, {username or user_id}.\n\n"
            "You will now receive private trade confirmations here.\n"
            "Send /help to see all available commands.",
        )

    # ── /status ─────────────────────────────────────────────────────────────

    def _cmd_status(self, user_id: str, chat_id: str, args: list) -> None:
        try:
            settings = self._store.get_user_settings(user_id)
            managed = self._store.is_managed_enabled(user_id)
            defaults = self._store.get_user_defaults(user_id)
            symbols = self._store.get_user_allowed_symbols(user_id)
            subs = self._store.get_channels_for_user(user_id)

            mt5_status = "🟢 Connected (managed VPS)" if managed else "⚪ Self-hosted relay"
            sym_str = ", ".join(symbols) if symbols else "all symbols"
            groups_str = str(len(subs)) if subs else "0"

            msg = (
                f"📊 <b>Account Status</b>\n\n"
                f"MT5: {mt5_status}\n"
                f"Max lot: {settings.get('max_lot_size', 0.5)}\n"
                f"Default lot: {defaults.get('default_lot_size') or 'not set'}\n"
                f"Default SL: {defaults.get('default_sl_pips') or 'not set'} pips\n"
                f"Default TP: {defaults.get('default_tp_pips') or 'not set'} pips\n"
                f"Allowed symbols: {sym_str}\n"
                f"Active groups: {groups_str}"
            )
        except Exception as exc:
            msg = f"❌ Error fetching status: {exc}"
        self._reply_to_chat(chat_id, msg)

    # ── /disconnect ─────────────────────────────────────────────────────────

    def _cmd_disconnect(self, user_id: str, chat_id: str, args: list) -> None:
        """Unlink this Telegram account from the PlatAlgo account."""
        try:
            tg_user_id = self._store.get_telegram_id_for_user(user_id)
            if tg_user_id:
                self._store.unlink_telegram_user(tg_user_id)
        except Exception as exc:
            self._reply_to_chat(chat_id, f"❌ Failed to disconnect: {exc}")
            return
        self._reply_to_chat(chat_id, "✅ Telegram account unlinked from PlatAlgo.")

    # ── /setlot, /setsl, /settp ─────────────────────────────────────────────

    def _cmd_setlot(self, user_id: str, chat_id: str, args: list) -> None:
        if not args:
            self._reply_to_chat(chat_id, "Usage: /setlot <lot_size>  (e.g. /setlot 0.01)")
            return
        try:
            val = float(args[0])
            if val <= 0 or val > 100:
                raise ValueError("out of range")
            self._store.set_user_default(user_id, "default_lot_size", val)
            self._reply_to_chat(chat_id, f"✅ Default lot size set to {val}")
        except ValueError:
            self._reply_to_chat(chat_id, "❌ Invalid lot size. Use a positive number (e.g. 0.01).")

    def _cmd_setsl(self, user_id: str, chat_id: str, args: list) -> None:
        if not args:
            self._reply_to_chat(chat_id, "Usage: /setsl <pips>  (e.g. /setsl 50)")
            return
        try:
            val = float(args[0])
            if val <= 0:
                raise ValueError("must be positive")
            self._store.set_user_default(user_id, "default_sl_pips", val)
            self._reply_to_chat(chat_id, f"✅ Default SL set to {val} pips")
        except ValueError:
            self._reply_to_chat(chat_id, "❌ Invalid value. Use a positive number of pips (e.g. 50).")

    def _cmd_settp(self, user_id: str, chat_id: str, args: list) -> None:
        if not args:
            self._reply_to_chat(chat_id, "Usage: /settp <pips>  (e.g. /settp 100)")
            return
        try:
            val = float(args[0])
            if val <= 0:
                raise ValueError("must be positive")
            self._store.set_user_default(user_id, "default_tp_pips", val)
            self._reply_to_chat(chat_id, f"✅ Default TP set to {val} pips")
        except ValueError:
            self._reply_to_chat(chat_id, "❌ Invalid value. Use a positive number of pips (e.g. 100).")

    # ── /addsymbol, /removesymbol, /symbols ─────────────────────────────────

    def _cmd_addsymbol(self, user_id: str, chat_id: str, args: list) -> None:
        if not args:
            self._reply_to_chat(chat_id, "Usage: /addsymbol <SYMBOL>  (e.g. /addsymbol EURUSD)")
            return
        symbol = args[0].upper().strip()
        try:
            self._store.add_user_allowed_symbol(user_id, symbol)
            symbols = self._store.get_user_allowed_symbols(user_id)
            self._reply_to_chat(chat_id, f"✅ Added {symbol}. Allowed: {', '.join(symbols)}")
        except Exception as exc:
            self._reply_to_chat(chat_id, f"❌ Failed to add symbol: {exc}")

    def _cmd_removesymbol(self, user_id: str, chat_id: str, args: list) -> None:
        if not args:
            self._reply_to_chat(chat_id, "Usage: /removesymbol <SYMBOL>  (e.g. /removesymbol EURUSD)")
            return
        symbol = args[0].upper().strip()
        try:
            self._store.remove_user_allowed_symbol(user_id, symbol)
            symbols = self._store.get_user_allowed_symbols(user_id)
            sym_str = ", ".join(symbols) if symbols else "all symbols (no filter)"
            self._reply_to_chat(chat_id, f"✅ Removed {symbol}. Now allowing: {sym_str}")
        except Exception as exc:
            self._reply_to_chat(chat_id, f"❌ Failed to remove symbol: {exc}")

    def _cmd_symbols(self, user_id: str, chat_id: str, args: list) -> None:
        try:
            symbols = self._store.get_user_allowed_symbols(user_id)
            if symbols:
                self._reply_to_chat(chat_id, f"📋 Allowed symbols:\n{', '.join(symbols)}")
            else:
                self._reply_to_chat(chat_id, "📋 All symbols allowed (no filter active).")
        except Exception as exc:
            self._reply_to_chat(chat_id, f"❌ Error: {exc}")

    # ── /subscribe, /unsubscribe, /groups ───────────────────────────────────

    def _cmd_subscribe(self, user_id: str, chat_id: str, args: list) -> None:
        """Subscribe to a Telegram group/channel for signals."""
        if not args:
            self._reply_to_chat(
                chat_id,
                "Usage: /subscribe <chat_id_or_username>\n"
                "Add the bot to the target group first, then use this command.",
            )
            return
        target_chat = args[0].strip()
        try:
            chat_info = self._api.get_chat(target_chat) if self._api else None
            if not chat_info:
                self._reply_to_chat(chat_id, "❌ Bot has no access to that group. Add the bot first.")
                return
            resolved_chat_id = str(chat_info.get("id", target_chat))
            title = chat_info.get("title") or chat_info.get("username") or resolved_chat_id
            # Add subscription using store
            self._store.add_telegram_channel_simple(
                user_id=user_id,
                chat_id=resolved_chat_id,
                chat_title=title,
            )
            self._reply_to_chat(chat_id, f"✅ Subscribed to <b>{title}</b> ({resolved_chat_id}).")
        except Exception as exc:
            self._reply_to_chat(chat_id, f"❌ Failed to subscribe: {exc}")

    def _cmd_unsubscribe(self, user_id: str, chat_id: str, args: list) -> None:
        """Unsubscribe from a group/channel."""
        if not args:
            self._reply_to_chat(chat_id, "Usage: /unsubscribe <chat_id>")
            return
        target_chat = args[0].strip()
        try:
            self._store.remove_telegram_channel(user_id=user_id, chat_id=target_chat)
            self._reply_to_chat(chat_id, f"✅ Unsubscribed from {target_chat}.")
        except Exception as exc:
            self._reply_to_chat(chat_id, f"❌ Failed to unsubscribe: {exc}")

    def _cmd_groups(self, user_id: str, chat_id: str, args: list) -> None:
        """List all subscribed groups."""
        try:
            channels = self._store.get_channels_for_user(user_id)
            if not channels:
                self._reply_to_chat(chat_id, "📋 No subscribed groups. Use /subscribe to add one.")
                return
            lines = ["📋 <b>Subscribed groups:</b>"]
            for ch in channels:
                status = "✅" if ch.get("enabled") else "⏸"
                lines.append(f"{status} {ch.get('chat_title') or ch.get('chat_id')} ({ch.get('chat_id')})")
            self._reply_to_chat(chat_id, "\n".join(lines))
        except Exception as exc:
            self._reply_to_chat(chat_id, f"❌ Error: {exc}")

    # ── /help ────────────────────────────────────────────────────────────────

    def _cmd_help(self, user_id: str | None, chat_id: str, args: list) -> None:
        self._reply_to_chat(
            chat_id,
            "<b>PlatAlgo Bot Commands</b>\n\n"
            "<b>Account</b>\n"
            "/start &lt;token&gt; — Link your PlatAlgo account\n"
            "/status — Show account status and settings\n"
            "/disconnect — Unlink your Telegram account\n\n"
            "<b>Defaults</b>\n"
            "/setlot &lt;size&gt; — Set default lot size (e.g. 0.01)\n"
            "/setsl &lt;pips&gt; — Set default stop loss in pips\n"
            "/settp &lt;pips&gt; — Set default take profit in pips\n\n"
            "<b>Symbol Filter</b>\n"
            "/addsymbol &lt;SYMBOL&gt; — Allow a symbol\n"
            "/removesymbol &lt;SYMBOL&gt; — Remove a symbol from the filter\n"
            "/symbols — List allowed symbols\n\n"
            "<b>Groups</b>\n"
            "/subscribe &lt;chat_id&gt; — Subscribe to a group's signals\n"
            "/unsubscribe &lt;chat_id&gt; — Unsubscribe from a group\n"
            "/groups — List subscribed groups\n\n"
            "/help — Show this message",
        )

    # ── Admin commands (Section 8) ───────────────────────────────────────────

    def _handle_admin_command(self, chat_id: str, args: list) -> None:
        if not args:
            self._reply_to_chat(
                chat_id,
                "<b>Admin Commands</b>\n"
                "/admin users — List all users\n"
                "/admin signals — Recent signals (last 20)\n"
                "/admin stats — Platform statistics\n"
                "/admin user &lt;name&gt; — User details\n"
                "/admin kick &lt;name&gt; — Stop user's MT5 session",
            )
            return

        sub_cmd = args[0].lower()

        if sub_cmd == "users":
            try:
                users = self._store.get_all_users_summary()
                if not users:
                    self._reply_to_chat(chat_id, "No users found.")
                    return
                lines = [f"<b>Users ({len(users)})</b>"]
                for u in users[:30]:
                    managed = "🖥" if u.get("managed") else "🔌"
                    lines.append(f"{managed} {u['user_id']} (created {u.get('created_at_str', '?')})")
                self._reply_to_chat(chat_id, "\n".join(lines))
            except Exception as exc:
                self._reply_to_chat(chat_id, f"❌ Error: {exc}")

        elif sub_cmd == "signals":
            try:
                logs = self._store.get_recent_signal_logs(limit=20)
                if not logs:
                    self._reply_to_chat(chat_id, "No recent signals.")
                    return
                lines = [f"<b>Recent signals ({len(logs)})</b>"]
                for log in logs:
                    status = log.get("execution_status", "?")
                    lines.append(
                        f"{log.get('user_id', '?')} | {log.get('parsed_action', '?')} "
                        f"{log.get('parsed_symbol', '?')} | {status}"
                    )
                self._reply_to_chat(chat_id, "\n".join(lines))
            except Exception as exc:
                self._reply_to_chat(chat_id, f"❌ Error: {exc}")

        elif sub_cmd == "stats":
            try:
                stats = self._store.get_platform_stats()
                self._reply_to_chat(
                    chat_id,
                    f"<b>Platform Stats</b>\n"
                    f"Users: {stats.get('total_users', 0)}\n"
                    f"Managed sessions: {stats.get('managed_count', 0)}\n"
                    f"Signals today: {stats.get('signals_today', 0)}\n"
                    f"Executed today: {stats.get('executed_today', 0)}\n"
                    f"Active channels: {stats.get('active_channels', 0)}",
                )
            except Exception as exc:
                self._reply_to_chat(chat_id, f"❌ Error: {exc}")

        elif sub_cmd == "user" and len(args) > 1:
            target = args[1]
            try:
                info = self._store.get_user_admin_info(target)
                if not info:
                    self._reply_to_chat(chat_id, f"User '{target}' not found.")
                    return
                self._reply_to_chat(
                    chat_id,
                    f"<b>User: {target}</b>\n"
                    f"Managed: {'yes' if info.get('managed') else 'no'}\n"
                    f"Max lot: {info.get('max_lot_size', '?')}\n"
                    f"Channels: {info.get('channel_count', 0)}\n"
                    f"Signals: {info.get('signal_count', 0)}\n"
                    f"Created: {info.get('created_at_str', '?')}",
                )
            except Exception as exc:
                self._reply_to_chat(chat_id, f"❌ Error: {exc}")

        elif sub_cmd == "kick" and len(args) > 1:
            target = args[1]
            try:
                self._store.admin_stop_managed_session(target)
                self._reply_to_chat(chat_id, f"✅ Stopped MT5 session for {target}.")
            except Exception as exc:
                self._reply_to_chat(chat_id, f"❌ Error: {exc}")

        else:
            self._reply_to_chat(chat_id, "Unknown admin command. Send /admin for help.")
