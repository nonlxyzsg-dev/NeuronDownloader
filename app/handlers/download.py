"""Download flow handlers: URL reception, format selection, download job."""

import logging
import os
import queue
import time

from datetime import datetime, timezone

from telebot import types

from app.config import (
    ENABLE_REACTIONS,
    FREE_DOWNLOAD_WINDOW_SECONDS,
    MAX_ACTIVE_TASKS_PER_USER,
    TELEGRAM_UPLOAD_TIMEOUT_SECONDS,
)
from app.constants import (
    ACTION_TYPING,
    ACTION_UPLOAD_AUDIO,
    ACTION_UPLOAD_VIDEO,
    CB_DOWNLOAD,
    DOWNLOAD_TIMEOUT_SECONDS,
    EMOJI_DOWNLOAD,
    EMOJI_DONE,
    EMOJI_ERROR,
    EMOJI_HOURGLASS,
    EMOJI_ZAP,
    FORMAT_AUDIO,
    FORMAT_BEST,
    MENU_DOWNLOAD,
    MENU_HELP,
    MENU_SUBSCRIPTIONS,
    STATUS_FAILED,
    STATUS_SUCCESS,
    TELEGRAM_MAX_FILE_SIZE,
)
from app.keyboards import build_format_keyboard, build_main_menu
from app.utils import (
    append_youtube_client_hint,
    format_bytes,
    format_caption,
    format_limit_message,
    format_speed,
    get_file_size,
    is_youtube_url,
    send_with_retry,
)


def register_download_handlers(ctx) -> None:
    """Register all download-related handlers."""
    bot = ctx.bot
    storage = ctx.storage
    downloader = ctx.downloader
    download_manager = ctx.download_manager
    active_downloads = ctx.active_downloads

    def _send_media(
        user_id: int,
        chat_id: int,
        source,
        title: str,
        audio_only: bool,
        file_size: int | None = None,
    ) -> None:
        """Send audio/video to user with retry logic."""
        caption = format_caption(title)
        bot.send_chat_action(
            chat_id,
            ACTION_UPLOAD_AUDIO if audio_only else ACTION_UPLOAD_VIDEO,
        )
        upload_start = time.monotonic()
        if audio_only:
            send_with_retry(
                bot.send_audio,
                user_id,
                source,
                caption=caption,
                timeout=TELEGRAM_UPLOAD_TIMEOUT_SECONDS,
            )
        else:
            send_with_retry(
                bot.send_video,
                user_id,
                source,
                caption=caption,
                timeout=TELEGRAM_UPLOAD_TIMEOUT_SECONDS,
                supports_streaming=True,
            )
        upload_duration = time.monotonic() - upload_start
        upload_speed = (
            format_speed(file_size / upload_duration)
            if file_size and upload_duration > 0
            else "unknown"
        )
        logging.info(
            "%s uploaded to user %s in %.2fs (size=%s, speed=%s)",
            "Audio" if audio_only else "Video",
            user_id,
            upload_duration,
            format_bytes(file_size) if file_size else "unknown",
            upload_speed,
        )

    def queue_download(
        user_id: int,
        chat_id: int,
        url: str,
        selected_format: str | None,
        title: str,
        status_message_id: int | None = None,
        queue_message_id: int | None = None,
        audio_only: bool = False,
        reaction_message_id: int | None = None,
    ) -> None:
        def _job() -> None:
            if storage.is_blocked(user_id):
                return
            if ctx.shutdown_requested:
                logging.info("Skipping download due to shutdown")
                return

            # Deduplication: skip if same URL is already being downloaded
            if not active_downloads.try_acquire(url):
                bot.send_message(
                    chat_id,
                    "Это видео уже скачивается. Дождитесь завершения.",
                )
                return

            progress_message_id: int | None = status_message_id
            last_update = [0.0]
            last_text = [""]
            download_started = time.monotonic()
            logged_missing_total = [False]

            def progress_hook(data: dict) -> None:
                if ctx.shutdown_requested:
                    raise KeyboardInterrupt("Download interrupted by shutdown")
                if time.monotonic() - download_started > DOWNLOAD_TIMEOUT_SECONDS:
                    raise TimeoutError(
                        f"Download exceeded {DOWNLOAD_TIMEOUT_SECONDS}s timeout"
                    )
                if not progress_message_id:
                    return
                if data.get("status") != "downloading":
                    return
                downloaded = data.get("downloaded_bytes") or 0
                total = data.get("total_bytes") or data.get("total_bytes_estimate")
                speed = data.get("speed")
                eta = data.get("eta")
                if total:
                    percent = min(downloaded / total * 100, 100)
                    text = (
                        f"{EMOJI_DOWNLOAD} Скачивание: {percent:.1f}% "
                        f"({format_bytes(downloaded)}/{format_bytes(total)}) "
                        f"• {format_speed(speed)}"
                    )
                else:
                    text = (
                        f"{EMOJI_DOWNLOAD} Скачивание: {format_bytes(downloaded)} "
                        f"• {format_speed(speed)}"
                    )
                    if not logged_missing_total[0]:
                        logging.info(
                            "Progress without total size (downloaded=%s, speed=%s, url=%s)",
                            format_bytes(downloaded),
                            format_speed(speed),
                            url,
                        )
                        logged_missing_total[0] = True
                if eta is not None:
                    text = f"{text} • ETA {int(eta)}s"
                now = time.monotonic()
                if now - last_update[0] < 1:
                    return
                if text == last_text[0]:
                    return
                try:
                    bot.edit_message_text(text, chat_id, progress_message_id)
                    last_update[0] = now
                    last_text[0] = text
                except Exception:
                    pass

            try:
                logging.info(
                    "Download job started: user=%s url=%s format=%s audio=%s",
                    user_id, url, selected_format or "best", audio_only,
                )
                if reaction_message_id:
                    try:
                        bot.delete_message(chat_id, reaction_message_id)
                    except Exception:
                        pass
                if queue_message_id:
                    try:
                        bot.delete_message(chat_id, queue_message_id)
                    except Exception:
                        pass
                if progress_message_id:
                    try:
                        bot.edit_message_text(
                            f"{EMOJI_DOWNLOAD} Скачивание: 0.0% • 0 B/s",
                            chat_id,
                            progress_message_id,
                        )
                    except Exception:
                        progress_message_id = None
                if not progress_message_id:
                    try:
                        sent = bot.send_message(
                            chat_id,
                            f"{EMOJI_DOWNLOAD} Скачивание: 0.0% • 0 B/s",
                        )
                        progress_message_id = sent.message_id
                    except Exception:
                        progress_message_id = None

                # Try direct URL upload first
                direct_info = None
                direct_url = None
                direct_size = None
                try:
                    direct_info = downloader.get_info(url)
                    direct_url, direct_size = downloader.get_direct_url(
                        direct_info, selected_format, audio_only=audio_only,
                    )
                except Exception:
                    logging.exception("Failed to resolve direct URL for %s", url)

                queue_delay = time.monotonic() - download_started
                logging.info(
                    "Download starting after %.2fs queue delay (user=%s, url=%s)",
                    queue_delay, user_id, url,
                )

                if direct_url:
                    if direct_size and direct_size > TELEGRAM_MAX_FILE_SIZE:
                        logging.info(
                            "Direct URL file too large (%s), falling back to download",
                            format_bytes(direct_size),
                        )
                    else:
                        if progress_message_id:
                            try:
                                bot.edit_message_text(
                                    "🚀 Отправляем напрямую в Telegram…",
                                    chat_id, progress_message_id,
                                )
                            except Exception:
                                pass
                        try:
                            _send_media(
                                user_id, chat_id, direct_url, title,
                                audio_only, file_size=direct_size,
                            )
                            if progress_message_id:
                                try:
                                    bot.delete_message(chat_id, progress_message_id)
                                except Exception:
                                    pass
                            storage.log_download(
                                user_id,
                                (direct_info or {}).get("extractor_key", "unknown"),
                                STATUS_SUCCESS,
                            )
                            return
                        except Exception:
                            logging.exception(
                                "Direct URL upload failed, falling back to download "
                                "(user=%s, url=%s)", user_id, url,
                            )

                # Fallback: download to file
                file_path, info = downloader.download(
                    url, selected_format,
                    audio_only=audio_only,
                    progress_callback=progress_hook,
                )
                download_duration = time.monotonic() - download_started
                total_bytes = get_file_size(file_path)
                logging.info(
                    "Download finished in %.2fs (size=%s, path=%s, url=%s)",
                    download_duration,
                    format_bytes(total_bytes) if total_bytes else "unknown",
                    file_path, url,
                )

                # Validate file size before upload
                if total_bytes and total_bytes > TELEGRAM_MAX_FILE_SIZE:
                    if progress_message_id:
                        try:
                            bot.edit_message_text(
                                f"{EMOJI_ERROR} Файл слишком большой "
                                f"({format_bytes(total_bytes)}). "
                                f"Лимит Telegram — {format_bytes(TELEGRAM_MAX_FILE_SIZE)}. "
                                "Попробуйте выбрать более низкое качество.",
                                chat_id, progress_message_id,
                            )
                        except Exception:
                            pass
                    try:
                        os.remove(file_path)
                    except OSError:
                        pass
                    storage.log_download(
                        user_id, info.get("extractor_key", "unknown"), STATUS_FAILED,
                    )
                    return

                if progress_message_id:
                    try:
                        bot.edit_message_text(
                            f"{EMOJI_DONE} Скачано. Отправляем в Telegram…",
                            chat_id, progress_message_id,
                        )
                    except Exception:
                        pass

                with open(file_path, "rb") as handle:
                    _send_media(
                        user_id, chat_id, handle, title,
                        audio_only, file_size=total_bytes,
                    )

                try:
                    os.remove(file_path)
                except OSError:
                    logging.exception("Failed to delete file %s after upload", file_path)

                if progress_message_id:
                    try:
                        bot.delete_message(chat_id, progress_message_id)
                    except Exception:
                        pass
                storage.log_download(
                    user_id, info.get("extractor_key", "unknown"), STATUS_SUCCESS,
                )

            except TimeoutError as exc:
                storage.log_download(user_id, "unknown", STATUS_FAILED)
                if progress_message_id:
                    try:
                        bot.edit_message_text(
                            f"{EMOJI_ERROR} {exc}", chat_id, progress_message_id,
                        )
                    except Exception:
                        pass
            except Exception as exc:
                storage.log_download(user_id, "unknown", STATUS_FAILED)
                error_message = f"Ошибка загрузки: {exc}"
                if is_youtube_url(url):
                    error_message = append_youtube_client_hint(error_message)
                if progress_message_id:
                    try:
                        bot.edit_message_text(
                            f"{EMOJI_ERROR} {error_message}",
                            chat_id, progress_message_id,
                        )
                    except Exception:
                        pass
                else:
                    bot.send_message(user_id, error_message)
            finally:
                active_downloads.release(url)

        try:
            logging.info(
                "Queue status: queued=%s/%s active_user=%s",
                download_manager.queued_count(),
                download_manager.max_queue_size(),
                download_manager.active_count(user_id),
            )
            download_manager.submit_user(user_id, _job)
        except queue.Full:
            bot.send_message(chat_id, "Очередь переполнена. Попробуйте позже.")

    # --- Message handlers ---

    @bot.message_handler(commands=["start", "help"])
    def send_welcome(message: types.Message) -> None:
        ctx.ensure_user(message.from_user)
        if not ctx.check_access(message.from_user.id, message.chat.id):
            return
        ctx.clear_last_inline(message.from_user.id, message.chat.id)
        bot.send_message(
            message.chat.id,
            (
                "Привет! Отправьте ссылку на видео YouTube/Instagram/VK "
                "или ссылку на канал YouTube. "
                "Бот предложит варианты качества и скачает видео."
            ),
            reply_markup=build_main_menu(),
        )

    @bot.message_handler(func=lambda msg: msg.text is not None)
    def handle_link(message: types.Message) -> None:
        ctx.ensure_user(message.from_user)
        if not ctx.check_access(message.from_user.id, message.chat.id):
            return
        url = message.text.strip()
        if url == MENU_SUBSCRIPTIONS:
            from app.handlers.subscription import _list_subscriptions
            _list_subscriptions(ctx, message)
            return
        if url == MENU_HELP:
            send_welcome(message)
            return
        if url == MENU_DOWNLOAD:
            ctx.clear_last_inline(message.from_user.id, message.chat.id)
            bot.send_message(message.chat.id, "Отправьте ссылку на видео.")
            return
        if not url.startswith("http"):
            bot.send_message(message.chat.id, "Пожалуйста, отправьте ссылку.")
            return
        ctx.clear_last_inline(message.from_user.id, message.chat.id)
        subscribed = ctx.is_required_member(message.from_user.id)
        if not subscribed and ctx.is_free_limit_reached(message.from_user.id):
            bot.send_message(message.chat.id, format_limit_message())
            return
        reaction_message_id = None
        if ENABLE_REACTIONS:
            try:
                if hasattr(bot, "set_message_reaction"):
                    if hasattr(types, "ReactionTypeEmoji"):
                        reaction = [types.ReactionTypeEmoji(EMOJI_ZAP)]
                    else:
                        reaction = [EMOJI_ZAP]
                    bot.set_message_reaction(
                        message.chat.id, message.message_id, reaction=reaction,
                    )
                else:
                    sent = bot.send_message(
                        message.chat.id, EMOJI_ZAP,
                        reply_to_message_id=message.message_id,
                    )
                    reaction_message_id = sent.message_id
            except Exception:
                try:
                    sent = bot.send_message(
                        message.chat.id, EMOJI_ZAP,
                        reply_to_message_id=message.message_id,
                    )
                    reaction_message_id = sent.message_id
                except Exception:
                    reaction_message_id = None
        bot.send_chat_action(message.chat.id, ACTION_TYPING)
        try:
            info = downloader.get_info(url)
        except Exception as exc:
            error_text = str(exc)
            if "sign in to confirm" in error_text.lower():
                bot.send_message(
                    message.chat.id,
                    "YouTube требует подтверждения входа. "
                    "Добавьте cookies и повторите попытку.",
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
            url, title, str(reaction_message_id or ""), channel_url,
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

    # --- Callback handlers ---

    @bot.callback_query_handler(
        func=lambda call: call.data and call.data.startswith(f"{CB_DOWNLOAD}|")
    )
    def handle_download(call: types.CallbackQuery) -> None:
        ctx.ensure_user(call.from_user)
        if not ctx.check_access(call.from_user.id, call.message.chat.id):
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
        if not ctx.is_required_member(call.from_user.id):
            if ctx.is_free_limit_reached(call.from_user.id):
                bot.answer_callback_query(call.id, "Лимит на период исчерпан.")
                return
            now_ts = int(datetime.now(timezone.utc).timestamp())
            storage.log_free_download(call.from_user.id, now_ts)
        if (
            MAX_ACTIVE_TASKS_PER_USER > 0
            and download_manager.active_count(call.from_user.id)
            >= MAX_ACTIVE_TASKS_PER_USER
        ):
            bot.answer_callback_query(
                call.id, "Достигнут лимит активных загрузок. Попробуйте позже.",
            )
            return
        queue_length = download_manager.queued_count()
        if queue_length >= download_manager.max_queue_size():
            bot.answer_callback_query(
                call.id, "Очередь переполнена. Попробуйте позже.",
            )
            return
        queue_message = bot.send_message(
            call.message.chat.id,
            f"Ваш запрос №{queue_length + 1} в очереди",
        )
        bot.answer_callback_query(call.id, "Загрузка добавлена в очередь.")
        selected_format = None if format_id in (FORMAT_BEST, FORMAT_AUDIO) else format_id
        audio_only = format_id == FORMAT_AUDIO
        queue_download(
            call.from_user.id,
            call.message.chat.id,
            url,
            selected_format,
            title,
            status_message_id=call.message.message_id,
            queue_message_id=queue_message.message_id,
            audio_only=audio_only,
            reaction_message_id=reaction_message_id,
        )
        storage.delete_request(token)
        try:
            bot.edit_message_text(
                f"{EMOJI_HOURGLASS} Загрузка в очереди...",
                call.message.chat.id,
                call.message.message_id,
            )
        except Exception:
            pass
        storage.set_last_inline_message_id(call.from_user.id, None)
