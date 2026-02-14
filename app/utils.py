"""Shared utility functions: formatting, URL helpers, caching, retry logic."""

import logging
import os
import time
import traceback
import threading

from app.constants import (
    BOT_SIGNATURE,
    EMOJI_ALERT,
    MEMBERSHIP_CACHE_TTL,
    TELEGRAM_CAPTION_MAX_LENGTH,
    TELEGRAM_MAX_FILE_SIZE,
    UPLOAD_MAX_RETRIES,
    UPLOAD_RETRY_DELAYS,
)
from app.config import (
    ADMIN_IDS,
    FREE_DOWNLOAD_LIMIT,
    FREE_DOWNLOAD_WINDOW_SECONDS,
)


# --- URL helpers ---


def is_youtube_url(url: str) -> bool:
    lowered = url.lower()
    return "youtube.com" in lowered or "youtu.be" in lowered


def append_youtube_client_hint(message: str) -> str:
    hint = (
        '\u041f\u043e\u0434\u0441\u043a\u0430\u0437\u043a\u0430: \u043a\u043b\u0438\u0435\u043d\u0442 YouTube "android_creator" \u043c\u043e\u0436\u0435\u0442 \u0431\u044b\u0442\u044c \u043d\u0435\u043f\u043e\u0434\u0434\u0435\u0440\u0436\u0438\u0432\u0430\u0435\u043c. '
        "\u041f\u043e\u043f\u0440\u043e\u0431\u0443\u0439\u0442\u0435 \u0443\u0431\u0440\u0430\u0442\u044c \u0435\u0433\u043e \u0438\u0437 YOUTUBE_PLAYER_CLIENTS \u0438\u043b\u0438 \u0437\u0430\u043c\u0435\u043d\u0438\u0442\u044c \u043d\u0430 android/web."
    )
    return f"{message}\n\n{hint}"


# --- Text formatting ---


def format_caption(title: str) -> str:
    title = title.strip()
    if title:
        caption = f"{title}\n\n{BOT_SIGNATURE}"
    else:
        caption = BOT_SIGNATURE
    if len(caption) <= TELEGRAM_CAPTION_MAX_LENGTH:
        return caption
    allowed_title = max(0, TELEGRAM_CAPTION_MAX_LENGTH - len(BOT_SIGNATURE) - 2)
    trimmed_title = title[:allowed_title].rstrip()
    if trimmed_title:
        return f"{trimmed_title}\n\n{BOT_SIGNATURE}"
    return BOT_SIGNATURE[:TELEGRAM_CAPTION_MAX_LENGTH]


def format_bytes(value: float | None) -> str:
    if value is None:
        return "0 B"
    units = ["B", "KB", "MB", "GB", "TB"]
    size = float(value)
    for unit in units:
        if size < 1024 or unit == units[-1]:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} {units[-1]}"


def format_speed(value: float | None) -> str:
    if value is None:
        return "0 B/s"
    return f"{format_bytes(value)}/s"


def format_limit_message(free_limit: int | None = None, window_seconds: int | None = None) -> str:
    limit = free_limit if free_limit is not None else FREE_DOWNLOAD_LIMIT
    window = window_seconds if window_seconds is not None else FREE_DOWNLOAD_WINDOW_SECONDS
    if window % 3600 == 0:
        hours = window // 3600
        period = f"{hours} \u0447\u0430\u0441(\u0430)" if hours != 1 else "1 \u0447\u0430\u0441"
    elif window % 60 == 0:
        minutes = window // 60
        period = f"{minutes} \u043c\u0438\u043d\u0443\u0442"
    else:
        period = f"{window} \u0441\u0435\u043a\u0443\u043d\u0434"
    return (
        f"\u0414\u043e\u0441\u0442\u0443\u043f\u043d\u043e {limit} \u0441\u043a\u0430\u0447\u0438\u0432\u0430\u043d\u0438\u0435(\u044f) \u0437\u0430 {period}.\n"
        "\u042d\u0442\u043e\u0442 \u0441\u0435\u0440\u0432\u0438\u0441 \u2014 \u0431\u043b\u0430\u0433\u043e\u0434\u0430\u0440\u043d\u043e\u0441\u0442\u044c \u043f\u043e\u0434\u043f\u0438\u0441\u0447\u0438\u043a\u0430\u043c \u043a\u0430\u043d\u0430\u043b\u0430 \u00ab\u0411\u0430\u043d\u043a\u0430 \u0441 \u043d\u0435\u0439\u0440\u043e\u043d\u0430\u043c\u0438\u00bb. "
        "\u041f\u043e\u0434\u043f\u0438\u0448\u0438\u0442\u0435\u0441\u044c, \u0447\u0442\u043e\u0431\u044b \u043f\u043e\u043b\u0443\u0447\u0438\u0442\u044c \u0431\u0435\u0437\u043b\u0438\u043c\u0438\u0442\u043d\u044b\u0435 \u0437\u0430\u0433\u0440\u0443\u0437\u043a\u0438!"
    )


# --- Access helpers ---


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


# --- File helpers ---


def validate_file_size(file_path: str) -> bool:
    """Check if file size is within Telegram's upload limit (50 MB)."""
    try:
        return os.path.getsize(file_path) <= TELEGRAM_MAX_FILE_SIZE
    except OSError:
        return False


def get_file_size(file_path: str) -> int | None:
    try:
        return os.path.getsize(file_path)
    except OSError:
        return None


# --- Upload retry ---


def send_with_retry(send_func, *args, **kwargs):
    """Call send_func with retry logic for transient Telegram errors."""
    last_exc = None
    for attempt in range(UPLOAD_MAX_RETRIES):
        try:
            return send_func(*args, **kwargs)
        except Exception as exc:
            last_exc = exc
            error_text = str(exc).lower()
            is_transient = any(
                token in error_text
                for token in ("timeout", "connection", "network", "429", "502", "503")
            )
            if not is_transient:
                raise
            if attempt < UPLOAD_MAX_RETRIES - 1:
                delay = UPLOAD_RETRY_DELAYS[min(attempt, len(UPLOAD_RETRY_DELAYS) - 1)]
                logging.warning(
                    "Upload attempt %d/%d failed: %s, retrying in %ds",
                    attempt + 1,
                    UPLOAD_MAX_RETRIES,
                    exc,
                    delay,
                )
                time.sleep(delay)
    raise last_exc


# --- Error notification ---


def notify_admin_error(bot, user_id: int, username: str, action: str, error: Exception) -> None:
    """Send error notification to all admins with user context."""
    tb = traceback.format_exc()
    if len(tb) > 800:
        tb = "..." + tb[-800:]
    message = (
        f"{EMOJI_ALERT} <b>\u041e\u0448\u0438\u0431\u043a\u0430 \u0431\u043e\u0442\u0430</b>\n\n"
        f"\U0001f464 <b>\u041f\u043e\u043b\u044c\u0437\u043e\u0432\u0430\u0442\u0435\u043b\u044c:</b> {user_id}"
    )
    if username:
        message += f" (@{username})"
    message += (
        f"\n\U0001f4cb <b>\u0414\u0435\u0439\u0441\u0442\u0432\u0438\u0435:</b> {action}"
        f"\n\u274c <b>\u041e\u0448\u0438\u0431\u043a\u0430:</b> {error}"
        f"\n\n<pre>{tb}</pre>"
    )
    # Truncate to Telegram limit
    if len(message) > 4000:
        message = message[:4000] + "..."
    for admin_id in ADMIN_IDS:
        try:
            bot.send_message(admin_id, message, parse_mode="HTML")
        except Exception:
            logging.debug("Failed to notify admin %s about error", admin_id)


# --- Membership cache ---


class MembershipCache:
    """Thread-safe cache for chat membership checks to reduce Telegram API calls."""

    def __init__(self, ttl: int = MEMBERSHIP_CACHE_TTL) -> None:
        self._cache: dict[tuple[int, int], tuple[bool, float]] = {}
        self._lock = threading.Lock()
        self._ttl = ttl

    def get(self, chat_id: int, user_id: int) -> bool | None:
        with self._lock:
            key = (chat_id, user_id)
            entry = self._cache.get(key)
            if entry is None:
                return None
            is_member, timestamp = entry
            if time.monotonic() - timestamp > self._ttl:
                del self._cache[key]
                return None
            return is_member

    def set(self, chat_id: int, user_id: int, is_member: bool) -> None:
        with self._lock:
            self._cache[(chat_id, user_id)] = (is_member, time.monotonic())


# --- Download deduplication ---


class ActiveDownloads:
    """Track active downloads to prevent duplicate concurrent downloads of the same URL."""

    def __init__(self) -> None:
        self._active: dict[str, int] = {}
        self._lock = threading.Lock()

    def try_acquire(self, url: str) -> bool:
        with self._lock:
            if self._active.get(url, 0) > 0:
                return False
            self._active[url] = self._active.get(url, 0) + 1
            return True

    def release(self, url: str) -> None:
        with self._lock:
            count = self._active.get(url, 0)
            if count <= 1:
                self._active.pop(url, None)
            else:
                self._active[url] = count - 1

    def is_active(self, url: str) -> bool:
        with self._lock:
            return self._active.get(url, 0) > 0
