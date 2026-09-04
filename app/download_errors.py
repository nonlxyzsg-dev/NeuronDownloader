"""Классификация ошибок скачивания для повторных попыток."""

import re

CLASS_TIMEOUT = "timeout"
CLASS_403 = "403"
CLASS_429 = "429"
CLASS_FORMAT = "format"
CLASS_SITE = "site"

AUTO_RETRYABLE_CLASSES = frozenset({
    CLASS_TIMEOUT,
    CLASS_403,
    CLASS_429,
    CLASS_FORMAT,
})

# Канонический список из cookie_monitor.py.
INSTAGRAM_COOKIE_ERROR_MARKERS = (
    "login required",
    "rate-limit reached",
    "requested content is not available",
    "locked behind the login page",
    "checkpoint required",
    "unable to extract video url",
    "empty media response",
)


def classify_download_error(exc: Exception, url: str) -> str:
    """Возвращает класс ошибки скачивания."""
    error_text = str(exc).lower()
    if isinstance(exc, TimeoutError) or "превысила таймаут" in error_text:
        return CLASS_TIMEOUT
    if re.search(r"\bhttp error 403\b", error_text):
        return CLASS_403
    if re.search(r"\bhttp error 429\b", error_text) or "too many requests" in error_text:
        return CLASS_429
    if "requested format is not available" in error_text:
        return CLASS_FORMAT
    # Instagram-«cookies»-ошибки сознательно уходят в site (авторетрай не
    # положен, у них свой механизм pending_cookie_downloads); ветка оставлена
    # как явная граница классификации.
    if "instagram.com" in url.lower() and any(
        marker in error_text for marker in INSTAGRAM_COOKIE_ERROR_MARKERS
    ):
        return CLASS_SITE
    return CLASS_SITE


def is_auto_retryable(error_class: str) -> bool:
    """Проверяет, можно ли автоматически повторить загрузку после ошибки."""
    return error_class in AUTO_RETRYABLE_CLASSES
