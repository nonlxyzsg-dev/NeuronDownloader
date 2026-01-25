import os

from datetime import datetime

from telebot import TeleBot, types

from app.config import (
    ADMIN_IDS,
    BOT_TOKEN,
    DATA_DIR,
    MAX_CONCURRENT_DOWNLOADS,
    REQUIRED_CHAT_IDS,
)
from app.download_queue import DownloadManager
from app.downloader import VideoDownloader
from app.storage import Storage
from app.subscriptions import SubscriptionMonitor


def build_format_keyboard(token: str, options: list) -> types.InlineKeyboardMarkup:
    markup = types.InlineKeyboardMarkup()
    for option in options:
        markup.row(
            types.InlineKeyboardButton(
                text=f"🎬 {option.label}",
                callback_data=f"dl|{token}|{option.format_id}",
            ),
            types.InlineKeyboardButton(
                text=f"⭐ Подписаться {option.label}",
                callback_data=f"sub|{token}|{option.label}",
            ),
        )
    markup.row(
        types.InlineKeyboardButton(
            text="🚀 Максимальное качество",
            callback_data=f"dl|{token}|best",
        ),
        types.InlineKeyboardButton(
            text="⭐ Подписаться (max)",
            callback_data=f"sub|{token}|best",
        ),
    )
    return markup


def build_subscription_keyboard(token: str) -> types.InlineKeyboardMarkup:
    markup = types.InlineKeyboardMarkup()
    markup.add(
        types.InlineKeyboardButton(
            text="🧹 Отписаться",
            callback_data=f"unsub|{token}",
        )
    )
    return markup


def build_main_menu() -> types.ReplyKeyboardMarkup:
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.row("📥 Скачать", "📌 Мои подписки")
    markup.row("ℹ️ Помощь")
    return markup


def main() -> None:
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN не задан")

    os.makedirs(DATA_DIR, exist_ok=True)
    bot = TeleBot(BOT_TOKEN)
    storage = Storage()
    downloader = VideoDownloader(DATA_DIR)
    download_manager = DownloadManager(MAX_CONCURRENT_DOWNLOADS)
    monitor = SubscriptionMonitor(bot, storage, downloader, download_manager)
    monitor.start()

    def is_admin(user_id: int) -> bool:
        return user_id in ADMIN_IDS

    def ensure_user(user: types.User) -> None:
        storage.upsert_user(
            user.id,
            user.username or "",
            user.first_name or "",
            user.last_name or "",
        )

    def clear_last_inline(user_id: int, chat_id: int) -> None:
        message_id = storage.get_last_inline_message_id(user_id)
        if not message_id:
            return
        try:
            bot.edit_message_reply_markup(chat_id, message_id, reply_markup=None)
        except Exception:
            pass
        storage.set_last_inline_message_id(user_id, None)

    def check_access(user_id: int, chat_id: int) -> bool:
        if storage.is_blocked(user_id):
            bot.send_message(chat_id, "Вы заблокированы.")
            return False
        return True

    def is_required_member(user_id: int) -> bool:
        if not REQUIRED_CHAT_IDS:
            return True
        for required_chat in REQUIRED_CHAT_IDS:
            try:
                member = bot.get_chat_member(required_chat, user_id)
            except Exception:
                return False
            if member.status in ("left", "kicked"):
                return False
        return True

    def queue_download(
        user_id: int,
        url: str,
        selected_format: str | None,
        description: str,
    ) -> None:
        def _job() -> None:
            if storage.is_blocked(user_id):
                return
            try:
                file_path, info = downloader.download(url, selected_format)
                if description:
                    bot.send_message(user_id, description[:4000])
                with open(file_path, "rb") as handle:
                    bot.send_video(user_id, handle)
                storage.log_download(user_id, info.get("extractor_key", "unknown"), "success")
            except Exception as exc:
                storage.log_download(user_id, "unknown", "failed")
                bot.send_message(user_id, f"Ошибка загрузки: {exc}")

        download_manager.submit(_job)

    @bot.message_handler(commands=["start", "help"])
    def send_welcome(message: types.Message) -> None:
        ensure_user(message.from_user)
        if not check_access(message.from_user.id, message.chat.id):
            return
        clear_last_inline(message.from_user.id, message.chat.id)
        bot.send_message(
            message.chat.id,
            (
                "Привет! Я Нейрон Downloader из экосистемы канала «Банка с нейронами». "
                "На канале я рассказываю про ИИ технологии простым языком для нетехнической аудитории.\n\n"
                "Отправьте ссылку на видео YouTube/Instagram/VK или ссылку на канал YouTube. "
                "Бот предложит варианты качества и скачает видео с описанием."
            ),
            reply_markup=build_main_menu(),
        )

    @bot.message_handler(commands=["subscriptions"])
    def list_subscriptions(message: types.Message) -> None:
        ensure_user(message.from_user)
        if not check_access(message.from_user.id, message.chat.id):
            return
        clear_last_inline(message.from_user.id, message.chat.id)
        subscriptions = storage.list_user_subscriptions(message.from_user.id)
        if not subscriptions:
            bot.send_message(message.chat.id, "У вас нет активных подписок.")
            return
        markup = types.InlineKeyboardMarkup()
        lines = []
        for channel_url, resolution in subscriptions:
            token = storage.create_subscription_action(message.from_user.id, channel_url)
            label = f"{channel_url} ({resolution or 'max'})"
            lines.append(f"• {label}")
            markup.add(
                types.InlineKeyboardButton(
                    text=f"🗑️ Удалить {resolution or 'max'}",
                    callback_data=f"subdel|{token}",
                )
            )
        markup.add(
            types.InlineKeyboardButton(
                text="🧹 Отключить все",
                callback_data="subdel_all",
            )
        )
        sent = bot.send_message(
            message.chat.id,
            "Ваши подписки:\n" + "\n".join(lines),
            reply_markup=markup,
        )
        storage.set_last_inline_message_id(message.from_user.id, sent.message_id)

    @bot.message_handler(commands=["stats"])
    def show_stats(message: types.Message) -> None:
        ensure_user(message.from_user)
        if not is_admin(message.from_user.id):
            bot.send_message(message.chat.id, "Команда доступна только администратору.")
            return
        clear_last_inline(message.from_user.id, message.chat.id)
        total_users, total_downloads = storage.get_usage_stats()
        per_user = storage.get_user_stats()
        lines = [
            f"Всего пользователей: {total_users}",
            f"Всего загрузок: {total_downloads}",
            "Статистика по пользователям:",
        ]
        for user_id, count in per_user:
            lines.append(f"- {user_id}: {count}")
        bot.send_message(message.chat.id, "\n".join(lines))

    @bot.message_handler(commands=["users"])
    def show_users(message: types.Message) -> None:
        ensure_user(message.from_user)
        if not is_admin(message.from_user.id):
            bot.send_message(message.chat.id, "Команда доступна только администратору.")
            return
        clear_last_inline(message.from_user.id, message.chat.id)
        lines = ["Пользователи:"]
        for user_id, username, first_name, last_name, blocked in storage.list_users():
            display = " ".join(part for part in [first_name, last_name] if part)
            blocked_label = "заблокирован" if blocked else "активен"
            lines.append(f"- {user_id} @{username} {display} ({blocked_label})")
        bot.send_message(message.chat.id, "\n".join(lines))

    @bot.message_handler(commands=["block"])
    def block_user(message: types.Message) -> None:
        ensure_user(message.from_user)
        if not is_admin(message.from_user.id):
            bot.send_message(message.chat.id, "Команда доступна только администратору.")
            return
        clear_last_inline(message.from_user.id, message.chat.id)
        parts = message.text.split()
        if len(parts) < 2:
            bot.send_message(message.chat.id, "Использование: /block <user_id>")
            return
        try:
            target_id = int(parts[1])
        except ValueError:
            bot.send_message(message.chat.id, "Некорректный user_id.")
            return
        storage.set_blocked(target_id, True)
        bot.send_message(message.chat.id, f"Пользователь {target_id} заблокирован.")

    @bot.message_handler(commands=["unblock"])
    def unblock_user(message: types.Message) -> None:
        ensure_user(message.from_user)
        if not is_admin(message.from_user.id):
            bot.send_message(message.chat.id, "Команда доступна только администратору.")
            return
        clear_last_inline(message.from_user.id, message.chat.id)
        parts = message.text.split()
        if len(parts) < 2:
            bot.send_message(message.chat.id, "Использование: /unblock <user_id>")
            return
        try:
            target_id = int(parts[1])
        except ValueError:
            bot.send_message(message.chat.id, "Некорректный user_id.")
            return
        storage.set_blocked(target_id, False)
        bot.send_message(message.chat.id, f"Пользователь {target_id} разблокирован.")

    @bot.message_handler(func=lambda msg: msg.text is not None)
    def handle_link(message: types.Message) -> None:
        ensure_user(message.from_user)
        if not check_access(message.from_user.id, message.chat.id):
            return
        url = message.text.strip()
        if url == "📌 Мои подписки":
            list_subscriptions(message)
            return
        if url == "ℹ️ Помощь":
            send_welcome(message)
            return
        if url == "📥 Скачать":
            clear_last_inline(message.from_user.id, message.chat.id)
            bot.send_message(message.chat.id, "Отправьте ссылку на видео.")
            return
        if not url.startswith("http"):
            bot.send_message(message.chat.id, "Пожалуйста, отправьте ссылку.")
            return
        clear_last_inline(message.from_user.id, message.chat.id)
        subscribed = is_required_member(message.from_user.id)
        if not subscribed:
            today = datetime.utcnow().date().isoformat()
            downloads_today = storage.get_daily_downloads(message.from_user.id, today)
            if downloads_today >= 1:
                bot.send_message(
                    message.chat.id,
                    (
                        "Сегодня уже было одно скачивание. "
                        "Поддержите разработчика и подпишитесь на наши ресурсы, "
                        "чтобы получить неограниченные загрузки."
                    ),
                )
                return
        try:
            info = downloader.get_info(url)
        except Exception as exc:
            bot.send_message(message.chat.id, f"Не удалось обработать ссылку: {exc}")
            return
        title = info.get("title") or "Видео"
        description = info.get("description") or ""
        channel_url = info.get("channel_url") or info.get("uploader_url")
        if not subscribed:
            today = datetime.utcnow().date().isoformat()
            storage.increment_daily_downloads(message.from_user.id, today)
            bot.send_message(
                message.chat.id,
                (
                    "Я скачаю это видео, но без подписки доступно только одно скачивание в день. "
                    "Поддержите разработчика и подпишитесь на наши ресурсы для снятия ограничений."
                ),
            )
            queue_download(message.from_user.id, url, None, description)
            return
        token = storage.create_request(url, title, description, channel_url)
        options = downloader.list_formats(info)
        markup = build_format_keyboard(token, options)
        sent = bot.send_message(message.chat.id, f"{title}\nВыберите качество:", reply_markup=markup)
        storage.set_last_inline_message_id(message.from_user.id, sent.message_id)

    @bot.callback_query_handler(func=lambda call: call.data and call.data.startswith("dl|"))
    def handle_download(call: types.CallbackQuery) -> None:
        ensure_user(call.from_user)
        if not check_access(call.from_user.id, call.message.chat.id):
            return
        _, token, format_id = call.data.split("|", 2)
        request = storage.get_request(token)
        if request is None:
            bot.answer_callback_query(call.id, "Запрос устарел")
            return
        url, _, description, _ = request
        bot.answer_callback_query(call.id, "Загрузка добавлена в очередь.")
        selected_format = None if format_id == "best" else format_id
        queue_download(call.from_user.id, url, selected_format, description)
        storage.delete_request(token)
        try:
            bot.edit_message_text(
                "Загрузка добавлена в очередь.",
                call.message.chat.id,
                call.message.message_id,
            )
        except Exception:
            pass
        storage.set_last_inline_message_id(call.from_user.id, None)

    @bot.callback_query_handler(func=lambda call: call.data and call.data.startswith("sub|"))
    def handle_subscribe(call: types.CallbackQuery) -> None:
        ensure_user(call.from_user)
        if not check_access(call.from_user.id, call.message.chat.id):
            return
        _, token, resolution = call.data.split("|", 2)
        request = storage.get_request(token)
        if request is None:
            bot.answer_callback_query(call.id, "Запрос устарел")
            return
        _, title, _, channel_url = request
        if not channel_url:
            bot.send_message(call.message.chat.id, "Не удалось определить канал для подписки.")
            return
        storage.upsert_subscription(call.from_user.id, channel_url, resolution)
        try:
            bot.edit_message_text(
                f"Подписка на {title} оформлена. Бот будет отслеживать новые видео.",
                call.message.chat.id,
                call.message.message_id,
                reply_markup=build_subscription_keyboard(token),
            )
        except Exception:
            bot.send_message(
                call.message.chat.id,
                f"Подписка на {title} оформлена. Бот будет отслеживать новые видео.",
                reply_markup=build_subscription_keyboard(token),
            )
        storage.set_last_inline_message_id(call.from_user.id, call.message.message_id)

    @bot.callback_query_handler(func=lambda call: call.data and call.data.startswith("unsub|"))
    def handle_unsubscribe(call: types.CallbackQuery) -> None:
        ensure_user(call.from_user)
        if not check_access(call.from_user.id, call.message.chat.id):
            return
        _, token = call.data.split("|", 1)
        request = storage.get_request(token)
        if request is None:
            bot.answer_callback_query(call.id, "Запрос устарел")
            return
        _, title, _, channel_url = request
        if not channel_url:
            bot.answer_callback_query(call.id, "Канал не найден")
            return
        storage.remove_subscription(call.from_user.id, channel_url)
        try:
            bot.edit_message_text(
                f"Подписка на {title} отменена.",
                call.message.chat.id,
                call.message.message_id,
            )
        except Exception:
            bot.send_message(call.message.chat.id, f"Подписка на {title} отменена.")
        storage.set_last_inline_message_id(call.from_user.id, None)

    @bot.callback_query_handler(func=lambda call: call.data == "subdel_all")
    def handle_delete_all(call: types.CallbackQuery) -> None:
        ensure_user(call.from_user)
        if not check_access(call.from_user.id, call.message.chat.id):
            return
        subscriptions = storage.list_user_subscriptions(call.from_user.id)
        for channel_url, _ in subscriptions:
            storage.remove_subscription(call.from_user.id, channel_url)
        bot.answer_callback_query(call.id, "Все подписки удалены.")
        try:
            bot.edit_message_text(
                "Все подписки удалены.",
                call.message.chat.id,
                call.message.message_id,
            )
        except Exception:
            bot.send_message(call.message.chat.id, "Все подписки удалены.")
        storage.set_last_inline_message_id(call.from_user.id, None)

    @bot.callback_query_handler(func=lambda call: call.data and call.data.startswith("subdel|"))
    def handle_delete_subscription(call: types.CallbackQuery) -> None:
        ensure_user(call.from_user)
        if not check_access(call.from_user.id, call.message.chat.id):
            return
        _, token = call.data.split("|", 1)
        action = storage.get_subscription_action(token)
        if action is None:
            bot.answer_callback_query(call.id, "Запрос устарел")
            return
        action_user_id, channel_url = action
        if action_user_id != call.from_user.id:
            bot.answer_callback_query(call.id, "Недостаточно прав")
            return
        storage.remove_subscription(call.from_user.id, channel_url)
        storage.delete_subscription_action(token)
        bot.answer_callback_query(call.id, "Подписка удалена.")
        try:
            bot.edit_message_text(
                "Подписка удалена.",
                call.message.chat.id,
                call.message.message_id,
            )
        except Exception:
            bot.send_message(call.message.chat.id, "Подписка удалена.")
        storage.set_last_inline_message_id(call.from_user.id, None)

    bot.infinity_polling()


if __name__ == "__main__":
    main()
