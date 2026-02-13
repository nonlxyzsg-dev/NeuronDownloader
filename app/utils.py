"""Shared utility functions: formatting, URL helpers, caching, retry logic."""

import logging
import os
import time
import threading

from app.constants import (
    BOT_SIGNATURE,
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
        'Подсказка: клиент YouTube "android_creator" может быть неподдерживаем. '
        "Попробуйте убрать его из YOUTUBE_PLAYER_CLIENTS или заменить на android/web."
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


def format_limit_message() -> str:
    if FREE_DOWNLOAD_WINDOW_SECONDS % 3600 == 0:
        hours = FREE_DOWNLOAD_WINDOW_SECONDS // 3600
        period = f"{hours} час(а)" if hours != 1 else "1 час"
    elif FREE_DOWNLOAD_WINDOW_SECONDS % 60 == 0:
        minutes = FREE_DOWNLOAD_WINDOW_SECONDS // 60
        period = f"{minutes} минут"
    else:
        period = f"{FREE_DOWNLOAD_WINDOW_SECONDS} секунд"
    return (
        f"Доступно {FREE_DOWNLOAD_LIMIT} скачивание(я) за {period}. "
        "Поддержите разработчика и подпишитесь на наши ресурсы, "
        "чтобы получить неограниченные загрузки."
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
        self._active: dict[str, int] = {}  # url -> count of active downloads
        self._lock = threading.Lock()

    def try_acquire(self, url: str) -> bool:
        """Try to start a download for this URL. Returns False if already downloading."""
        with self._lock:
            if self._active.get(url, 0) > 0:
                return False
            self._active[url] = self._active.get(url, 0) + 1
            return True

    def release(self, url: str) -> None:
        """Mark download as finished."""
        with self._lock:
            count = self._active.get(url, 0)
            if count <= 1:
                self._active.pop(url, None)
            else:
                self._active[url] = count - 1

    def is_active(self, url: str) -> bool:
        with self._lock:
            return self._active.get(url, 0) > 0
