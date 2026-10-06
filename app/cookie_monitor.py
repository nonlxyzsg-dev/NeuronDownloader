"""Фоновая проверка актуальности cookies YouTube."""

# Плановая проверка Instagram демонтирована (решение владельца 06.10.2026):
# фоновые обращения к IG-эндпоинтам — причина банов аккаунта. Реактивные
# IG-скачивания (downloader) и ручная админ-проверка кукис не затронуты.

import logging
import threading

from app.config import (
    COOKIE_CHECK_INTERVAL_SECONDS,
    YOUTUBE_TEST_URL,
)

logger = logging.getLogger(__name__)


class CookieHealthMonitor:
    """Периодически проверяет, работают ли cookies YouTube."""

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
            self._run_checks_once()

    def _run_checks_once(self) -> None:
        """Один шаг цикла мониторинга.

        Проверка под защитной обёрткой: непредвиденная ошибка логируется,
        но не убивает daemon-поток.
        """
        try:
            self._check_youtube()
        except Exception:
            logger.exception("Cookie-check YouTube: непредвиденная ошибка, продолжаю")

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

