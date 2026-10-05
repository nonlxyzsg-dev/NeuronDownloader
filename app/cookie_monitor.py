"""Фоновая проверка актуальности cookies для YouTube и Instagram."""

import logging
import os
import threading
import time

from app import instagram_api
from app.config import (
    COOKIE_CHECK_INTERVAL_SECONDS,
    YOUTUBE_TEST_URL,
)

logger = logging.getLogger(__name__)


def _instagram_sessionid_expired(cookiefile: str | None) -> bool | None:
    """Истечение sessionid cookie для Instagram по timestamp в файле кукис.

    Читает Netscape-файл построчно (tab-split, 7 полей; float-expires терпимо) —
    без cookiejar, который падает на малиформенном файле. Если подходящих строк
    sessionid несколько, решение принимается по МАКСИМАЛЬНОМУ expires_at (та же
    политика «свежайшая строка побеждает», что и у load_sessionid).
    True — по timestamp точно просрочена; False — по timestamp свежа (может быть
    отозвана — это решает пинг); None — файла/строки нет или о сроке ничего
    неизвестно (мусорный timestamp либо 0 — сессионная кука).
    """
    if not cookiefile or not os.path.exists(cookiefile):
        return None
    best_expires: int | None = None
    try:
        with open(cookiefile, "r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                stripped = line.strip()
                if not stripped or stripped.startswith("#"):
                    continue
                parts = stripped.split("\t")
                if len(parts) != 7:
                    continue
                domain, _flag, _path, _secure, expires, name, _value = parts
                if name != "sessionid" or "instagram.com" not in domain.lower():
                    continue
                try:
                    expires_at = int(float(expires))
                except (ValueError, OverflowError):
                    # Нечитаемый или непредставимый timestamp (мусор, inf) —
                    # у этой строки данных о сроке нет.
                    continue
                if expires_at <= 0:
                    # По политике Netscape 0 — сессионная кука без срока, она НЕ
                    # просрочена; данных о сроке нет — решает живой пинг.
                    continue
                if best_expires is None or expires_at > best_expires:
                    best_expires = expires_at
    except OSError as exc:
        logger.debug("Не удалось прочитать cookies для Instagram-проверки: %s", exc)
        return None
    if best_expires is None:
        return None  # sessionid не найдена (или срока нет ни у одной строки)
    return best_expires < time.time()


def _stat_mtime(path: str | None) -> float | None:
    """mtime файла кукис; None — файла нет или stat не удался."""
    if not path or not os.path.exists(path):
        return None
    try:
        return os.stat(path).st_mtime
    except OSError as exc:
        logger.debug("Cookie-check: не удалось снять mtime cookies: %s", exc)
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
            self._run_checks_once()

    def _run_checks_once(self) -> None:
        """Один шаг цикла мониторинга.

        Каждая проверка под своей обёрткой: непредвиденная ошибка логируется,
        но не убивает daemon-поток, и проверка YouTube не гибнет от Instagram
        (и наоборот).
        """
        try:
            self._check_youtube()
        except Exception:
            logger.exception("Cookie-check YouTube: непредвиденная ошибка, продолжаю")
        try:
            self._check_instagram()
        except Exception:
            logger.exception("Cookie-check Instagram: непредвиденная ошибка, продолжаю")

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

        cookiefile = getattr(self._downloader, "cookiefile", None)
        # mtime фиксируем ДО любого чтения файла (load_sessionid и
        # _instagram_sessionid_expired тоже его читают): подмена кукис в любой
        # момент проверки должна подавить алерт, а не дать ложный «мёртв».
        mtime_before = _stat_mtime(cookiefile)
        sid = instagram_api.load_sessionid(cookiefile)
        if sid is None:
            # sessionid не найдена в файле cookies — Instagram не настроен.
            logger.debug("Cookie-check Instagram: sessionid не найдена, пропуск")
            return

        # Локальный timestamp — лишь довесок к живой проверке: Instagram
        # продлевает сессию sliding-renewal, поэтому серверное состояние
        # главнее данных из файла.
        expired = _instagram_sessionid_expired(cookiefile)

        # Дешёвая живая проверка: 1 запрос к приватному API (без yt-dlp).
        # mtime_before снят выше, до чтения файла — сверяем после пинга.
        alive = instagram_api.ping_session(sid)
        if _stat_mtime(cookiefile) != mtime_before:
            logger.debug(
                "Cookie-check Instagram: cookies обновились во время проверки, "
                "перепроверю следующим циклом"
            )
            return
        if alive is True:
            # Сервер подтвердил живость — даже если локальный timestamp старше.
            logger.debug("Cookie-check Instagram: OK (ping)")
        elif alive is False:
            logger.warning("Cookie-check Instagram: sessionid мёртв (ping)")
            notify_admin_cookies_expired(self._bot, "Instagram")
        elif expired is True:
            logger.warning(
                "Cookie-check Instagram: sessionid просрочена (по файлу), "
                "живую проверку провести не удалось"
            )
            notify_admin_cookies_expired(self._bot, "Instagram")
        else:
            logger.debug(
                "Cookie-check Instagram: статус неизвестен (сеть/rate-limit), без алерта"
            )
