"""Bot entrypoint: bootstrap, context, signal handling, polling loop."""

import logging
import os
import signal
import sys
import time
import threading

from datetime import datetime, timezone

from telebot import TeleBot, types

from app.config import (
    BOT_TOKEN,
    DATA_DIR,
    FREE_DOWNLOAD_LIMIT,
    FREE_DOWNLOAD_WINDOW_SECONDS,
    MAX_CONCURRENT_DOWNLOADS,
    MAX_QUEUE_SIZE,
    MAX_ACTIVE_TASKS_PER_USER,
    REQUIRED_CHAT_IDS,
    TELEGRAM_POLLING_ERROR_DELAY_SECONDS,
    TELEGRAM_POLLING_DNS_DELAY_SECONDS,
)
from app.download_queue import DownloadManager
from app.downloader import VideoDownloader
from app.handlers import register_all_handlers
from app.logger import setup_logging
from app.storage import Storage
from app.cleanup import DataCleanupMonitor
from app.utils import (
    ActiveDownloads,
    MembershipCache,
    is_admin,
)


class BotContext:
    """Shared context passed to all handler modules."""

    def __init__(
        self,
        bot: TeleBot,
        storage: Storage,
        downloader: VideoDownloader,
        download_manager: DownloadManager,
        membership_cache: MembershipCache,
        active_downloads: ActiveDownloads,
    ) -> None:
        self.bot = bot
        self.storage = storage
        self.downloader = downloader
        self.download_manager = download_manager
        self.membership_cache = membership_cache
        self.active_downloads = active_downloads
        self.shutdown_requested = False
        # User/admin state machine
        self._user_states: dict[int, object] = {}
        self._user_states_lock = threading.Lock()
        # Queue message tracking
        self._queue_messages: dict[int, tuple[int, int]] = {}
        self._queue_lock = threading.Lock()

    # --- User state management ---

    def get_user_state(self, user_id: int):
        with self._user_states_lock:
            return self._user_states.get(user_id)

    def set_user_state(self, user_id: int, state) -> None:
        with self._user_states_lock:
            if state is None:
                self._user_states.pop(user_id, None)
            else:
                self._user_states[user_id] = state

    # --- Queue message tracking ---

    def add_queue_message(self, user_id: int, chat_id: int, message_id: int) -> None:
        with self._queue_lock:
            self._queue_messages[user_id] = (chat_id, message_id)

    def remove_queue_message(self, user_id: int) -> None:
        with self._queue_lock:
            entry = self._queue_messages.pop(user_id, None)
        if entry:
            chat_id, message_id = entry
            try:
                self.bot.delete_message(chat_id, message_id)
            except Exception:
                pass

    # --- Dynamic settings ---

    def get_free_limit(self) -> int:
        val = self.storage.get_setting("free_download_limit")
        if val is not None:
            try:
                return int(val)
            except ValueError:
                pass
        return FREE_DOWNLOAD_LIMIT

    def get_free_window(self) -> int:
        val = self.storage.get_setting("free_download_window")
        if val is not None:
            try:
                return int(val)
            except ValueError:
                pass
        return FREE_DOWNLOAD_WINDOW_SECONDS

    # --- Core helpers ---

    def ensure_user(self, user: types.User) -> None:
        self.storage.upsert_user(
            user.id,
            user.username or "",
            user.first_name or "",
            user.last_name or "",
        )

    def clear_last_inline(self, user_id: int, chat_id: int) -> None:
        message_id = self.storage.get_last_inline_message_id(user_id)
        if not message_id:
            return
        try:
            self.bot.edit_message_reply_markup(
                chat_id, message_id, reply_markup=None,
            )
        except Exception:
            pass
        self.storage.set_last_inline_message_id(user_id, None)

    def check_access(self, user_id: int, chat_id: int) -> bool:
        if self.storage.is_blocked(user_id):
            self.bot.send_message(chat_id, "\u0412\u044b \u0437\u0430\u0431\u043b\u043e\u043a\u0438\u0440\u043e\u0432\u0430\u043d\u044b.")
            return False
        return True

    def is_required_member(self, user_id: int) -> bool:
        """Check if user is a member of ALL required chats (with caching)."""
        if is_admin(user_id):
            return True
        if not REQUIRED_CHAT_IDS:
            return True
        for required_chat in REQUIRED_CHAT_IDS:
            cached = self.membership_cache.get(required_chat, user_id)
            if cached is not None:
                if not cached:
                    return False
                continue
            try:
                member = self.bot.get_chat_member(required_chat, user_id)
                is_member = member.status not in ("left", "kicked")
            except Exception:
                is_member = False
            self.membership_cache.set(required_chat, user_id, is_member)
            if not is_member:
                return False
        return True

    def is_free_limit_reached(self, user_id: int) -> bool:
        if self.is_required_member(user_id):
            return False
        now_ts = int(datetime.now(timezone.utc).timestamp())
        free_window = self.get_free_window()
        start_ts = now_ts - free_window
        used = self.storage.count_free_downloads_since(user_id, start_ts)
        return used >= self.get_free_limit()


def main() -> None:
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is not set")

    setup_logging()
    os.makedirs(DATA_DIR, exist_ok=True)

    bot = TeleBot(BOT_TOKEN)
    storage = Storage()
    downloader = VideoDownloader(DATA_DIR)
    download_manager = DownloadManager(
        MAX_CONCURRENT_DOWNLOADS,
        MAX_QUEUE_SIZE,
        MAX_ACTIVE_TASKS_PER_USER,
    )
    membership_cache = MembershipCache()
    active_downloads = ActiveDownloads()

    ctx = BotContext(
        bot=bot,
        storage=storage,
        downloader=downloader,
        download_manager=download_manager,
        membership_cache=membership_cache,
        active_downloads=active_downloads,
    )

    cleanup_monitor = DataCleanupMonitor()
    cleanup_monitor.start()

    # Register all handlers (admin first, then support, then download/catch-all)
    register_all_handlers(ctx)

    # --- Shutdown handling ---

    def handle_shutdown(_signum: int, _frame: object | None) -> None:
        if ctx.shutdown_requested:
            logging.warning("Forced shutdown requested")
            sys.exit(1)
        ctx.shutdown_requested = True
        logging.info("Shutdown signal received, stopping all components...")
        logging.getLogger("TeleBot").setLevel(logging.CRITICAL)
        try:
            bot.stop_polling()
        except Exception as e:
            logging.debug("Error stopping bot polling: %s", e)
        download_manager.shutdown()
        cleanup_monitor.stop()
        logging.info("All components stopped")

        def force_exit():
            time.sleep(3)
            if ctx.shutdown_requested:
                logging.warning("Main thread did not exit in time, forcing exit")
                os._exit(0)

        threading.Thread(target=force_exit, daemon=True).start()

    signal.signal(signal.SIGINT, handle_shutdown)
    signal.signal(signal.SIGTERM, handle_shutdown)

    # --- Polling loop ---

    consecutive_failures = 0
    first_failure_ts: float | None = None

    def is_dns_error(exc: Exception) -> bool:
        error_text = repr(exc)
        return any(
            token in error_text
            for token in (
                "NameResolutionError",
                "Temporary failure in name resolution",
                "Failed to resolve",
            )
        )

    while True:
        if ctx.shutdown_requested:
            break
        try:
            bot.infinity_polling()
            consecutive_failures = 0
            first_failure_ts = None
        except KeyboardInterrupt:
            break
        except Exception as exc:
            if ctx.shutdown_requested:
                break
            consecutive_failures += 1
            if first_failure_ts is None:
                first_failure_ts = time.monotonic()
            elapsed = time.monotonic() - first_failure_ts
            delay = TELEGRAM_POLLING_ERROR_DELAY_SECONDS
            if is_dns_error(exc):
                delay = max(delay, TELEGRAM_POLLING_DNS_DELAY_SECONDS)
                if consecutive_failures == 1:
                    logging.warning(
                        "Cannot resolve api.telegram.org. "
                        "Check internet/DNS. Retrying in %ds.",
                        delay,
                    )
            if consecutive_failures >= 3 or elapsed >= 60:
                logging.error("Polling exception: %s", exc)
            time.sleep(delay)

    logging.info("Bot shutdown complete")


if __name__ == "__main__":
    main()
