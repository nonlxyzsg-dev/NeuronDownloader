"""Фоновая проверка актуальности cookies для YouTube и Instagram."""

import http.cookiejar
import logging
import os
import threading
import time

from app.config import (
    COOKIE_CHECK_INTERVAL_SECONDS,
    COOKIES_FILE,
    INSTAGRAM_TEST_URL,
    YOUTUBE_TEST_URL,
)

logger = logging.getLogger(__name__)

# Ошибки yt-dlp, указывающие на протухшие/невалидные cookies Instagram.
_INSTAGRAM_COOKIE_ERROR_MARKERS = (
    "login required",
    "rate-limit reached",
    "requested content is not available",
    "locked behind the login page",
    "checkpoint required",
    "unable to extract video url",
    "empty media response",
)


def _instagram_sessionid_expired() -> bool | None:
    """Проверяет истечение sessionid cookie для Instagram из файла cookies.

    Возвращает True если cookie просрочена, False если валидна,
    None если cookie не найдена или файл недоступен.
    """
    cookie_path = COOKIES_FILE
    if not cookie_path or not os.path.exists(cookie_path):
        return None
    try:
        cj = http.cookiejar.MozillaCookieJar(cookie_path)
        cj.load(ignore_discard=True, ignore_expires=True)
        for cookie in cj:
            if cookie.name == "sessionid" and ".instagram.com" in cookie.domain:
                if cookie.expires and cookie.expires < time.time():
                    return True
                return False
        return None  # sessionid не найдена
    except Exception:
        logger.debug("Не удалось прочитать cookies для Instagram-проверки")
        return None


class CookieHealthMonitor:
    """Периодически проверяет, работают ли cookies YouTube и Instagram."""

    def __init__(self, bot, downloader) -> None:
        self._bot = bot
        self._downloader = downloader
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._stop_event = threading.Event()

    def start(self) -> None:
        if COOKIE_CHECK_INTERVAL_SECONDS <= 0:
            logger.info("Проверка cookies отключена (COOKIE_CHECK_INTERVAL_SECONDS=0)")
            return
        self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        self._stop_event.set()
        if self._thread.is_alive():
            self._thread.join(timeout=timeout)

    def _run(self) -> None:
        # Первую проверку делаем через интервал, а не сразу при старте,
        # чтобы не замедлять запуск бота.
        while not self._stop_event.is_set():
            self._stop_event.wait(COOKIE_CHECK_INTERVAL_SECONDS)
            if self._stop_event.is_set():
                break
            self._check_youtube()
            self._check_instagram()

    def _check_youtube(self) -> None:
        from app.utils import notify_admin_cookies_expired

        try:
            self._downloader.get_info(YOUTUBE_TEST_URL)
            logger.debug("Cookie-check YouTube: OK")
        except Exception as exc:
            error_text = str(exc).lower()
            if "sign in to confirm" in error_text:
                logger.warning("Cookie-check YouTube: cookies протухли")
                notify_admin_cookies_expired(self._bot, "YouTube")
            else:
                # Другая ошибка (сеть, DNS и т.д.) — не считаем протуханием
                logger.debug("Cookie-check YouTube: ошибка (не cookies): %s", exc)

    def _check_instagram(self) -> None:
        from app.utils import notify_admin_cookies_expired

        # 1) Быстрая проверка: есть ли sessionid и не просрочена ли она по timestamp
        expired = _instagram_sessionid_expired()
        if expired is True:
            logger.warning("Cookie-check Instagram: sessionid просрочена (по timestamp)")
            notify_admin_cookies_expired(self._bot, "Instagram")
            return
        if expired is None:
            # sessionid не найдена в файле cookies — Instagram не настроен,
            # проверять через yt-dlp нет смысла.
            logger.debug("Cookie-check Instagram: sessionid не найдена, пропуск")
            return

        # 2) Реальная проверка: пробуем получить метаданные публичного профиля
        try:
            self._downloader.get_info(INSTAGRAM_TEST_URL)
            logger.debug("Cookie-check Instagram: OK")
        except Exception as exc:
            error_text = str(exc).lower()
            if any(marker in error_text for marker in _INSTAGRAM_COOKIE_ERROR_MARKERS):
                logger.warning("Cookie-check Instagram: cookies протухли: %s", exc)
                notify_admin_cookies_expired(self._bot, "Instagram")
            else:
                logger.debug("Cookie-check Instagram: ошибка (не cookies): %s", exc)
