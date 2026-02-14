"""Точка входа бота: инициализация, контекст, обработка сигналов, polling-цикл."""

import logging
import os
import signal
import sys
import time
import threading

from datetime import datetime, timezone

from telebot import TeleBot, types

from app.config import (
    ADMIN_IDS,
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
    """Общий контекст, передаваемый во все модули обработчиков."""

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
        # Машина состояний пользователя/админа
        self._user_states: dict[int, object] = {}
        self._user_states_lock = threading.Lock()
        # Отслеживание сообщений об очереди (список словарей, упорядоченный по добавлению)
        self._queue_items: list[dict] = []  # [{user_id, chat_id, message_id}, ...]
        self._queue_lock = threading.Lock()

    # --- Управление состоянием пользователя ---

    def get_user_state(self, user_id: int):
        """Возвращает текущее состояние пользователя."""
        with self._user_states_lock:
            return self._user_states.get(user_id)

    def set_user_state(self, user_id: int, state) -> None:
        """Устанавливает или сбрасывает состояние пользователя."""
        with self._user_states_lock:
            if state is None:
                self._user_states.pop(user_id, None)
            else:
                self._user_states[user_id] = state

    # --- Отслеживание сообщений об очереди ---

    def add_queue_message(self, user_id: int, chat_id: int, message_id: int) -> None:
        """Добавляет элемент очереди и обновляет все сообщения о позициях."""
        with self._queue_lock:
            self._queue_items.append({
                "user_id": user_id,
                "chat_id": chat_id,
                "message_id": message_id,
            })
        self._update_queue_messages()

    def remove_queue_message(self, user_id: int) -> None:
        """Удаляет первый элемент очереди для пользователя, обновляет остальные."""
        removed = None
        with self._queue_lock:
            for i, item in enumerate(self._queue_items):
                if item["user_id"] == user_id:
                    removed = self._queue_items.pop(i)
                    break
        if removed:
            try:
                self.bot.delete_message(removed["chat_id"], removed["message_id"])
            except Exception:
                pass
            self._update_queue_messages()

    def get_queue_info(self, user_id: int) -> tuple[int, int]:
        """Возвращает (кол-во запросов пользователя в очереди, общая очередь)."""
        with self._queue_lock:
            total = len(self._queue_items)
            user_count = sum(1 for item in self._queue_items if item["user_id"] == user_id)
        return user_count, total

    def _format_queue_text(self, user_count: int, total: int) -> str:
        """Форматирует сообщение о позиции в очереди."""
        from app.constants import EMOJI_HOURGLASS
        return (
            f"{EMOJI_HOURGLASS} Запрос в очереди на скачивание.\n"
            f"Ваша очередь: {user_count} | Общая очередь: {total}"
        )

    def _update_queue_messages(self) -> None:
        """Обновляет текст всех сообщений о позициях в очереди."""
        with self._queue_lock:
            items = list(self._queue_items)
        if not items:
            return
        total = len(items)
        # Считаем количество запросов на каждого пользователя
        user_counts: dict[int, int] = {}
        for item in items:
            uid = item["user_id"]
            user_counts[uid] = user_counts.get(uid, 0) + 1
        # Обновляем каждое сообщение
        for item in items:
            uid = item["user_id"]
            text = self._format_queue_text(user_counts[uid], total)
            try:
                self.bot.edit_message_text(text, item["chat_id"], item["message_id"])
            except Exception:
                pass

    # --- Динамические настройки ---

    def get_free_limit(self) -> int:
        """Возвращает текущий лимит бесплатных загрузок (из БД или конфига)."""
        val = self.storage.get_setting("free_download_limit")
        if val is not None:
            try:
                return int(val)
            except ValueError:
                pass
        return FREE_DOWNLOAD_LIMIT

    def get_free_window(self) -> int:
        """Возвращает текущее окно лимита в секундах (из БД или конфига)."""
        val = self.storage.get_setting("free_download_window")
        if val is not None:
            try:
                return int(val)
            except ValueError:
                pass
        return FREE_DOWNLOAD_WINDOW_SECONDS

    # --- Основные вспомогательные методы ---

    def ensure_user(self, user: types.User) -> None:
        """Создаёт или обновляет пользователя в БД."""
        self.storage.upsert_user(
            user.id,
            user.username or "",
            user.first_name or "",
            user.last_name or "",
        )

    def clear_last_inline(self, user_id: int, chat_id: int) -> None:
        """Удаляет инлайн-клавиатуру из последнего сообщения пользователя."""
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
        """Проверяет, не заблокирован ли пользователь."""
        if self.storage.is_blocked(user_id):
            self.bot.send_message(chat_id, "\u0412\u044b \u0437\u0430\u0431\u043b\u043e\u043a\u0438\u0440\u043e\u0432\u0430\u043d\u044b.")
            return False
        return True

    def is_required_member(self, user_id: int) -> bool:
        """Проверяет, подписан ли пользователь на ВСЕ обязательные каналы (с кэшированием)."""
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
        """Проверяет, исчерпан ли лимит бесплатных загрузок."""
        if self.is_required_member(user_id):
            return False
        now_ts = int(datetime.now(timezone.utc).timestamp())
        free_window = self.get_free_window()
        start_ts = now_ts - free_window
        used = self.storage.count_free_downloads_since(user_id, start_ts)
        return used >= self.get_free_limit()


def main() -> None:
    """Основная функция запуска бота."""
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN не задан")

    setup_logging()
    os.makedirs(DATA_DIR, exist_ok=True)
    logging.info("Бот запускается...")

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

    # Регистрация обработчиков (сначала админ, затем поддержка, потом скачивание/catch-all)
    register_all_handlers(ctx)
    logging.info(
        "Бот запущен (пользователей: %d, админов: %d, каналов: %d)",
        len(storage.list_users()),
        len(ADMIN_IDS),
        len(REQUIRED_CHAT_IDS),
    )

    # --- Обработка завершения ---

    def handle_shutdown(_signum: int, _frame: object | None) -> None:
        if ctx.shutdown_requested:
            logging.warning("Принудительное завершение")
            sys.exit(1)
        ctx.shutdown_requested = True
        logging.info("Получен сигнал завершения, останавливаем все компоненты...")
        logging.getLogger("TeleBot").setLevel(logging.CRITICAL)
        try:
            bot.stop_polling()
        except Exception as e:
            logging.debug("Ошибка при остановке polling: %s", e)
        download_manager.shutdown()
        cleanup_monitor.stop()
        logging.info("Все компоненты остановлены")

        def force_exit():
            time.sleep(3)
            if ctx.shutdown_requested:
                logging.warning("Основной поток не завершился вовремя, принудительный выход")
                os._exit(0)

        threading.Thread(target=force_exit, daemon=True).start()

    signal.signal(signal.SIGINT, handle_shutdown)
    signal.signal(signal.SIGTERM, handle_shutdown)

    # --- Polling-цикл ---

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
                        "Не удаётся разрешить api.telegram.org. "
                        "Проверьте интернет/DNS. Повтор через %dс.",
                        delay,
                    )
            if consecutive_failures >= 3 or elapsed >= 60:
                logging.error("Ошибка polling: %s", exc)
            time.sleep(delay)

    logging.info("Бот завершил работу")


if __name__ == "__main__":
    main()
