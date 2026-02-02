import logging
import os
import signal
import time

from datetime import datetime, timezone

from telebot import TeleBot, types

from app.config import (
    ADMIN_IDS,
    BOT_TOKEN,
    DATA_DIR,
    FREE_DOWNLOAD_LIMIT,
    FREE_DOWNLOAD_WINDOW_SECONDS,
    MAX_CONCURRENT_DOWNLOADS,
    REQUIRED_CHAT_IDS,
    ENABLE_REACTIONS,
    TELEGRAM_UPLOAD_TIMEOUT_SECONDS,
)
from app.download_queue import DownloadManager
from app.downloader import VideoDownloader
from app.storage import Storage
from app.subscriptions import SubscriptionMonitor


def build_format_keyboard(token: str, options: list) -> types.InlineKeyboardMarkup:
    markup = types.InlineKeyboardMarkup()
    for option in options:
        markup.add(
            types.InlineKeyboardButton(
                text=f"🎬 {option.label}",
                callback_data=f"dl|{token}|{option.format_id}",
            )
        )
    markup.add(
        types.InlineKeyboardButton(
            text="🚀 Максимальное качество",
            callback_data=f"dl|{token}|best",
        ),
    )
    markup.add(
        types.InlineKeyboardButton(
            text="🎧 Только звук",
            callback_data=f"dl|{token}|audio",
        ),
    )
    markup.add(
        types.InlineKeyboardButton(
            text="⭐ Подписка на канал (уведомления)",
            callback_data=f"submenu|{token}",
        )
    )
    return markup


def build_subscription_menu(
    token: str, options: list
) -> types.InlineKeyboardMarkup:
    markup = types.InlineKeyboardMarkup()
    for option in options:
        markup.add(
            types.InlineKeyboardButton(
                text=f"⭐ {option.label}",
                callback_data=f"sub|{token}|{option.label}",
            )
        )
    markup.add(
        types.InlineKeyboardButton(
            text="⭐ Максимальное качество",
            callback_data=f"sub|{token}|best",
        )
    )
    markup.add(
        types.InlineKeyboardButton(
            text="⭐ Только звук",
            callback_data=f"sub|{token}|audio",
        )
    )
    markup.add(
        types.InlineKeyboardButton(
            text="⬅️ Назад к скачиванию",
            callback_data=f"back|{token}",
        )
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


def is_youtube_url(url: str) -> bool:
    lowered = url.lower()
    return "youtube.com" in lowered or "youtu.be" in lowered


def append_youtube_client_hint(message: str) -> str:
    hint = (
        "Подсказка: клиент YouTube \"android_creator\" может быть неподдерживаем. "
        "Попробуйте убрать его из YOUTUBE_PLAYER_CLIENTS или заменить на android/web."
    )
    return f"{message}\n\n{hint}"


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
    shutdown_requested = False

    def handle_shutdown(_signum: int, _frame: object | None) -> None:
        nonlocal shutdown_requested
        shutdown_requested = True
        logging.getLogger("TeleBot").setLevel(logging.CRITICAL)
        try:
            bot.stop_polling()
        except Exception:
            pass

    signal.signal(signal.SIGINT, handle_shutdown)
    signal.signal(signal.SIGTERM, handle_shutdown)

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
        if is_admin(user_id):
            return True
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

    def format_bytes(value: float | None) -> str:
        if value is None:
            return "0 Б"
        units = ["Б", "КБ", "МБ", "ГБ", "ТБ"]
        size = float(value)
        for unit in units:
            if size < 1024 or unit == units[-1]:
                return f"{size:.1f} {unit}"
            size /= 1024
        return f"{size:.1f} {units[-1]}"

    def format_speed(value: float | None) -> str:
        if value is None:
            return "0 Б/с"
        return f"{format_bytes(value)}/с"

    def is_free_limit_reached(user_id: int) -> bool:
        if is_required_member(user_id):
            return False
        now_ts = int(datetime.now(timezone.utc).timestamp())
        start_ts = now_ts - FREE_DOWNLOAD_WINDOW_SECONDS
        used = storage.count_free_downloads_since(user_id, start_ts)
        return used >= FREE_DOWNLOAD_LIMIT

    def queue_download(
        user_id: int,
        chat_id: int,
        url: str,
        selected_format: str | None,
        title: str,
        status_message_id: int | None = None,
        audio_only: bool = False,
        reaction_message_id: int | None = None,
    ) -> None:
        def _job() -> None:
            if storage.is_blocked(user_id):
                return
            progress_message_id: int | None = None
            last_update = 0.0
            last_text = ""

            def progress_hook(data: dict) -> None:
                nonlocal last_update, last_text
                if not progress_message_id:
                    return
                if data.get("status") != "downloading":
                    return
                now = time.monotonic()
                if now - last_update < 1:
                    return
                downloaded = data.get("downloaded_bytes") or 0
                total = data.get("total_bytes") or data.get("total_bytes_estimate")
                speed = data.get("speed")
                if total:
                    percent = min(downloaded / total * 100, 100)
                    text = (
                        f"⬇️ Скачивание: {percent:.1f}% "
                        f"• {format_speed(speed)}"
                    )
                else:
                    text = (
                        f"⬇️ Скачивание: {format_bytes(downloaded)} "
                        f"• {format_speed(speed)}"
                    )
                if text == last_text:
                    return
                try:
                    bot.edit_message_text(text, chat_id, progress_message_id)
                    last_update = now
                    last_text = text
                except Exception:
                    pass
            try:
                if reaction_message_id:
                    try:
                        bot.delete_message(chat_id, reaction_message_id)
                    except Exception:
                        pass
                try:
                    sent = bot.send_message(chat_id, "⬇️ Скачивание: 0.0% • 0 Б/с")
                    progress_message_id = sent.message_id
                except Exception:
                    progress_message_id = None
                file_path, info = downloader.download(
                    url,
                    selected_format,
                    audio_only=audio_only,
                    progress_callback=progress_hook,
                )
                with open(file_path, "rb") as handle:
                    if audio_only:
                        bot.send_chat_action(user_id, "upload_audio")
                        upload_start = time.monotonic()
                        bot.send_audio(
                            user_id,
                            handle,
                            caption=title[:1024],
                            timeout=TELEGRAM_UPLOAD_TIMEOUT_SECONDS,
                        )
                        upload_duration = time.monotonic() - upload_start
                        logging.info(
                            "Audio uploaded to user %s in %.2f seconds",
                            user_id,
                            upload_duration,
                        )
                        try:
                            os.remove(file_path)
                        except OSError:
                            logging.exception(
                                "Failed to удалить аудиофайл %s после отправки",
                                file_path,
                            )
                    else:
                        bot.send_chat_action(user_id, "upload_video")
                        upload_start = time.monotonic()
                        bot.send_video(
                            user_id,
                            handle,
                            caption=title[:1024],
                            timeout=TELEGRAM_UPLOAD_TIMEOUT_SECONDS,
                            supports_streaming=True,
                        )
                        upload_duration = time.monotonic() - upload_start
                        logging.info(
                            "Video uploaded to user %s in %.2f seconds",
                            user_id,
                            upload_duration,
                        )
                        try:
                            os.remove(file_path)
                        except OSError:
                            logging.exception(
                                "Failed to удалить видеофайл %s после отправки",
                                file_path,
                            )
                if progress_message_id:
                    try:
                        bot.delete_message(chat_id, progress_message_id)
                    except Exception:
                        pass
                storage.log_download(user_id, info.get("extractor_key", "unknown"), "success")
            except Exception as exc:
                storage.log_download(user_id, "unknown", "failed")
                error_message = f"Ошибка загрузки: {exc}"
                if is_youtube_url(url):
                    error_message = append_youtube_client_hint(error_message)
                if progress_message_id:
                    try:
                        bot.edit_message_text(
                            f"❌ {error_message}",
                            chat_id,
                            progress_message_id,
                        )
                    except Exception:
                        pass
                else:
                    bot.send_message(user_id, error_message)

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
                "Привет! Отправьте ссылку на видео YouTube/Instagram/VK или ссылку на канал YouTube. "
                "Бот предложит варианты качества и скачает видео."
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
        if not subscribed and is_free_limit_reached(message.from_user.id):
            bot.send_message(message.chat.id, format_limit_message())
            return
        reaction_message_id = None
        if ENABLE_REACTIONS:
            try:
                if hasattr(bot, "set_message_reaction"):
                    if hasattr(types, "ReactionTypeEmoji"):
                        reaction = [types.ReactionTypeEmoji("⚡️")]
                    else:
                        reaction = ["⚡️"]
                    bot.set_message_reaction(
                        message.chat.id,
                        message.message_id,
                        reaction=reaction,
                    )
                else:
                    sent = bot.send_message(
                        message.chat.id,
                        "⚡️",
                        reply_to_message_id=message.message_id,
                    )
                    reaction_message_id = sent.message_id
            except Exception:
                try:
                    sent = bot.send_message(
                        message.chat.id,
                        "⚡️",
                        reply_to_message_id=message.message_id,
                    )
                    reaction_message_id = sent.message_id
                except Exception:
                    reaction_message_id = None
        bot.send_chat_action(message.chat.id, "typing")
        try:
            info = downloader.get_info(url)
        except Exception as exc:
            error_text = str(exc)
            if "sign in to confirm" in error_text.lower():
                bot.send_message(
                    message.chat.id,
                    (
                        "YouTube требует подтверждения входа. "
                        "Добавьте cookies и повторите попытку."
                    ),
                )
            else:
                error_message = f"Не удалось обработать ссылку: {exc}"
                if is_youtube_url(url):
                    error_message = append_youtube_client_hint(error_message)
                bot.send_message(message.chat.id, error_message)
            return
        title = info.get("title") or "Видео"
        channel_url = info.get("channel_url") or info.get("uploader_url")
        token = storage.create_request(
            url,
            title,
            str(reaction_message_id or ""),
            channel_url,
        )
        options = downloader.list_formats(info)
        if not options:
            has_video = any(
                fmt.get("vcodec") not in (None, "none")
                for fmt in info.get("formats", [])
            )
            if not has_video:
                warning_text = (
                    "Не удалось получить видеоформаты. "
                    "Возможно, требуется обновить cookies или настройки клиента."
                )
                if is_youtube_url(url):
                    warning_text = append_youtube_client_hint(warning_text)
                bot.send_message(message.chat.id, warning_text)
                return
        markup = build_format_keyboard(token, options)
        note = "" if subscribed else f"{format_limit_message()}\n\n"
        sent = bot.send_message(
            message.chat.id,
            (
                f"{note}**Нашли видео:** {title}\n"
                "Выберите качество ниже или нажмите *Максимальное* / *Только звук*."
            ),
            parse_mode="Markdown",
            reply_markup=markup,
        )
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
        url, title, reaction_hint, _ = request
        reaction_message_id = None
        if reaction_hint and reaction_hint.isdigit():
            reaction_message_id = int(reaction_hint)
        if not is_required_member(call.from_user.id):
            if is_free_limit_reached(call.from_user.id):
                bot.answer_callback_query(call.id, "Лимит на период исчерпан.")
                return
            now_ts = int(datetime.now(timezone.utc).timestamp())
            storage.log_free_download(call.from_user.id, now_ts)
        bot.answer_callback_query(call.id, "Загрузка добавлена в очередь.")
        selected_format = None if format_id in ("best", "audio") else format_id
        audio_only = format_id == "audio"
        queue_download(
            call.from_user.id,
            call.message.chat.id,
            url,
            selected_format,
            title,
            status_message_id=call.message.message_id,
            audio_only=audio_only,
            reaction_message_id=reaction_message_id,
        )
        storage.delete_request(token)
        try:
            bot.edit_message_text(
                "⏳ Загрузка в очереди...",
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

    @bot.callback_query_handler(func=lambda call: call.data and call.data.startswith("submenu|"))
    def handle_subscription_menu(call: types.CallbackQuery) -> None:
        ensure_user(call.from_user)
        if not check_access(call.from_user.id, call.message.chat.id):
            return
        _, token = call.data.split("|", 1)
        request = storage.get_request(token)
        if request is None:
            bot.answer_callback_query(call.id, "Запрос устарел")
            return
        url, title, _, _ = request
        try:
            info = downloader.get_info(url)
        except Exception as exc:
            bot.answer_callback_query(call.id, f"Не удалось обновить список: {exc}")
            return
        options = downloader.list_formats(info)
        try:
            bot.edit_message_text(
                f"{title}\nВы выбираете качество для подписки.\nВыберите качество подписки:",
                call.message.chat.id,
                call.message.message_id,
                reply_markup=build_subscription_menu(token, options),
            )
        except Exception:
            bot.send_message(
                call.message.chat.id,
                f"{title}\nВы выбираете качество для подписки.\nВыберите качество подписки:",
                reply_markup=build_subscription_menu(token, options),
            )
        storage.set_last_inline_message_id(call.from_user.id, call.message.message_id)

    @bot.callback_query_handler(func=lambda call: call.data and call.data.startswith("back|"))
    def handle_back_to_download(call: types.CallbackQuery) -> None:
        ensure_user(call.from_user)
        if not check_access(call.from_user.id, call.message.chat.id):
            return
        _, token = call.data.split("|", 1)
        request = storage.get_request(token)
        if request is None:
            bot.answer_callback_query(call.id, "Запрос устарел")
            return
        url, title, _, _ = request
        try:
            info = downloader.get_info(url)
        except Exception as exc:
            bot.answer_callback_query(call.id, f"Не удалось обновить список: {exc}")
            return
        options = downloader.list_formats(info)
        try:
            bot.edit_message_text(
                (
                    f"{title}\n"
                    "Возвращаемся к выбору качества скачивания.\n"
                    "Выберите качество или формат:"
                ),
                call.message.chat.id,
                call.message.message_id,
                reply_markup=build_format_keyboard(token, options),
            )
        except Exception:
            bot.send_message(
                call.message.chat.id,
                (
                    f"{title}\n"
                    "Возвращаемся к выбору качества скачивания.\n"
                    "Выберите качество или формат:"
                ),
                reply_markup=build_format_keyboard(token, options),
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

    consecutive_failures = 0
    first_failure_ts: float | None = None
    while True:
        try:
            bot.infinity_polling()
            consecutive_failures = 0
            first_failure_ts = None
        except KeyboardInterrupt:
            break
        except Exception as exc:
            consecutive_failures += 1
            if first_failure_ts is None:
                first_failure_ts = time.monotonic()
            elapsed = time.monotonic() - first_failure_ts
            if consecutive_failures >= 3 or elapsed >= 60:
                logging.error("Infinity polling exception: %s", exc)
            time.sleep(5)
        if shutdown_requested:
            break


if __name__ == "__main__":
    main()
