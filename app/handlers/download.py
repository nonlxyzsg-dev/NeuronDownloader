"""Download flow handlers: URL reception, format selection, download job."""

import logging
import os
import queue
import time
import uuid

from datetime import datetime, timezone

from telebot import types

from app.config import (
    ENABLE_REACTIONS,
    MAX_ACTIVE_TASKS_PER_USER,
    TELEGRAM_UPLOAD_TIMEOUT_SECONDS,
)
from app.constants import (
    ACTION_TYPING,
    ACTION_UPLOAD_AUDIO,
    ACTION_UPLOAD_VIDEO,
    CB_DOWNLOAD,
    CB_SPLIT_YES,
    CB_SPLIT_NO,
    DOWNLOAD_TIMEOUT_SECONDS,
    EMOJI_DOWNLOAD,
    EMOJI_DONE,
    EMOJI_ERROR,
    EMOJI_HOURGLASS,
    EMOJI_ZAP,
    FORMAT_AUDIO,
    FORMAT_BEST,
    MENU_HELP,
    STATUS_FAILED,
    STATUS_SUCCESS,
    TELEGRAM_MAX_FILE_SIZE,
    TELEGRAM_SPLIT_TARGET_SIZE,
)
from app.keyboards import (
    build_channel_buttons,
    build_format_keyboard,
    build_main_menu,
    build_split_confirm_keyboard,
)
from app.utils import (
    append_youtube_client_hint,
    format_bytes,
    format_caption,
    format_limit_message,
    format_speed,
    get_file_size,
    is_youtube_url,
    notify_admin_error,
    send_with_retry,
)


def register_download_handlers(ctx) -> None:
    """Register all download-related handlers."""
    bot = ctx.bot
    storage = ctx.storage
    downloader = ctx.downloader
    download_manager = ctx.download_manager
    active_downloads = ctx.active_downloads

    # Temporary storage for split tokens -> file info
    _split_pending: dict[str, dict] = {}

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

            # Remove queue message
            ctx.remove_queue_message(user_id)

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

            username = ""
            try:
                user_row = storage.get_user(user_id)
                if user_row:
                    username = user_row[1] or user_row[2] or ""
            except Exception:
                pass

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
                                    "\U0001f680 Отправляем напрямую в Telegram\u2026",
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

                # If file too large, offer to split
                if total_bytes and total_bytes > TELEGRAM_MAX_FILE_SIZE:
                    split_token = uuid.uuid4().hex[:12]
                    _split_pending[split_token] = {
                        "file_path": file_path,
                        "title": title,
                        "audio_only": audio_only,
                        "user_id": user_id,
                        "chat_id": chat_id,
                        "info": info,
                    }
                    split_text = (
                        f"Файл слишком большой ({format_bytes(total_bytes)}). "
                        f"Лимит Telegram \u2014 {format_bytes(TELEGRAM_MAX_FILE_SIZE)}.\n\n"
                        "Хотите разделить видео на части и отправить?"
                    )
                    if progress_message_id:
                        try:
                            bot.edit_message_text(
                                split_text,
                                chat_id, progress_message_id,
                                reply_markup=build_split_confirm_keyboard(split_token),
                            )
                        except Exception:
                            bot.send_message(
                                chat_id, split_text,
                                reply_markup=build_split_confirm_keyboard(split_token),
                            )
                    else:
                        bot.send_message(
                            chat_id, split_text,
                            reply_markup=build_split_confirm_keyboard(split_token),
                        )
                    storage.log_download(
                        user_id, info.get("extractor_key", "unknown"), STATUS_SUCCESS,
                    )
                    return

                if progress_message_id:
                    try:
                        bot.edit_message_text(
                            f"{EMOJI_DONE} Скачано. Отправляем в Telegram\u2026",
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
                notify_admin_error(bot, user_id, username, f"Таймаут загрузки: {url}", exc)
                if progress_message_id:
                    try:
                        bot.edit_message_text(
                            f"{EMOJI_ERROR} {exc}", chat_id, progress_message_id,
                        )
                    except Exception:
                        pass
            except Exception as exc:
                storage.log_download(user_id, "unknown", STATUS_FAILED)
                notify_admin_error(bot, user_id, username, f"Ошибка загрузки: {url}", exc)
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
        ctx.set_user_state(message.from_user.id, None)
        bot.send_message(
            message.chat.id,
            (
                "\u041f\u0440\u0438\u0432\u0435\u0442! \u042f \u0431\u043e\u0442 \u0434\u043b\u044f \u0441\u043a\u0430\u0447\u0438\u0432\u0430\u043d\u0438\u044f \u0432\u0438\u0434\u0435\u043e \u2014 \u0431\u043b\u0430\u0433\u043e\u0434\u0430\u0440\u043d\u043e\u0441\u0442\u044c \u043f\u043e\u0434\u043f\u0438\u0441\u0447\u0438\u043a\u0430\u043c "
                "\u043a\u0430\u043d\u0430\u043b\u0430 \u00ab\u0411\u0430\u043d\u043a\u0430 \u0441 \u043d\u0435\u0439\u0440\u043e\u043d\u0430\u043c\u0438\u00bb \U0001f9e0\n\n"
                "\u041f\u0440\u043e\u0441\u0442\u043e \u043e\u0442\u043f\u0440\u0430\u0432\u044c\u0442\u0435 \u043c\u043d\u0435 \u0441\u0441\u044b\u043b\u043a\u0443 \u043d\u0430 \u0432\u0438\u0434\u0435\u043e \u0438\u0437 YouTube, Instagram \u0438\u043b\u0438 VK, "
                "\u0438 \u044f \u043f\u0440\u0435\u0434\u043b\u043e\u0436\u0443 \u0432\u0430\u0440\u0438\u0430\u043d\u0442\u044b \u043a\u0430\u0447\u0435\u0441\u0442\u0432\u0430.\n\n"
                "\u041f\u043e\u0434\u043f\u0438\u0441\u0447\u0438\u043a\u0438 \u043a\u0430\u043d\u0430\u043b\u0430 \u043f\u043e\u043b\u0443\u0447\u0430\u044e\u0442 \u0431\u0435\u0437\u043b\u0438\u043c\u0438\u0442\u043d\u044b\u0435 \u0437\u0430\u0433\u0440\u0443\u0437\u043a\u0438!\n\n"
                "\u0415\u0441\u043b\u0438 \u0432\u043e\u0437\u043d\u0438\u043a\u043d\u0443\u0442 \u043f\u0440\u043e\u0431\u043b\u0435\u043c\u044b \u2014 \u043d\u0430\u0436\u043c\u0438\u0442\u0435 \u00ab\U0001f4dd \u0421\u043e\u043e\u0431\u0449\u0438\u0442\u044c \u043e \u043f\u0440\u043e\u0431\u043b\u0435\u043c\u0435\u00bb."
            ),
            reply_markup=build_main_menu(),
        )

    @bot.message_handler(func=lambda msg: msg.text == MENU_HELP)
    def handle_help(message: types.Message) -> None:
        send_welcome(message)

    @bot.message_handler(func=lambda msg: msg.text is not None)
    def handle_link(message: types.Message) -> None:
        ctx.ensure_user(message.from_user)
        if not ctx.check_access(message.from_user.id, message.chat.id):
            return
        # Skip if user has an active state (report, admin input, etc.)
        state = ctx.get_user_state(message.from_user.id)
        if state is not None:
            return
        url = message.text.strip()
        if not url.startswith("http"):
            bot.send_message(
                message.chat.id,
                "\u041f\u043e\u0436\u0430\u043b\u0443\u0439\u0441\u0442\u0430, \u043e\u0442\u043f\u0440\u0430\u0432\u044c\u0442\u0435 \u0441\u0441\u044b\u043b\u043a\u0443 \u043d\u0430 \u0432\u0438\u0434\u0435\u043e (YouTube, Instagram, VK).",
            )
            return
        ctx.clear_last_inline(message.from_user.id, message.chat.id)
        subscribed = ctx.is_required_member(message.from_user.id)
        if not subscribed and ctx.is_free_limit_reached(message.from_user.id):
            free_limit = ctx.get_free_limit()
            free_window = ctx.get_free_window()
            channels = storage.get_required_channels()
            channel_markup = build_channel_buttons(channels)
            bot.send_message(
                message.chat.id,
                format_limit_message(free_limit, free_window),
                reply_markup=channel_markup,
            )
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
                    "YouTube \u0442\u0440\u0435\u0431\u0443\u0435\u0442 \u043f\u043e\u0434\u0442\u0432\u0435\u0440\u0436\u0434\u0435\u043d\u0438\u044f \u0432\u0445\u043e\u0434\u0430. "
                    "\u0414\u043e\u0431\u0430\u0432\u044c\u0442\u0435 cookies \u0438 \u043f\u043e\u0432\u0442\u043e\u0440\u0438\u0442\u0435 \u043f\u043e\u043f\u044b\u0442\u043a\u0443.",
                )
            else:
                error_message = f"\u041d\u0435 \u0443\u0434\u0430\u043b\u043e\u0441\u044c \u043e\u0431\u0440\u0430\u0431\u043e\u0442\u0430\u0442\u044c \u0441\u0441\u044b\u043b\u043a\u0443: {exc}"
                if is_youtube_url(url):
                    error_message = append_youtube_client_hint(error_message)
                bot.send_message(message.chat.id, error_message)
            return
        title = info.get("title") or "\u0412\u0438\u0434\u0435\u043e"
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
                    "\u041d\u0435 \u0443\u0434\u0430\u043b\u043e\u0441\u044c \u043f\u043e\u043b\u0443\u0447\u0438\u0442\u044c \u0432\u0438\u0434\u0435\u043e\u0444\u043e\u0440\u043c\u0430\u0442\u044b. "
                    "\u0412\u043e\u0437\u043c\u043e\u0436\u043d\u043e, \u0442\u0440\u0435\u0431\u0443\u0435\u0442\u0441\u044f \u043e\u0431\u043d\u043e\u0432\u0438\u0442\u044c cookies \u0438\u043b\u0438 \u043d\u0430\u0441\u0442\u0440\u043e\u0439\u043a\u0438 \u043a\u043b\u0438\u0435\u043d\u0442\u0430."
                )
                if is_youtube_url(url):
                    warning_text = append_youtube_client_hint(warning_text)
                bot.send_message(message.chat.id, warning_text)
                return
        markup = build_format_keyboard(token, options)
        note = ""
        if not subscribed:
            free_limit = ctx.get_free_limit()
            free_window = ctx.get_free_window()
            note = f"{format_limit_message(free_limit, free_window)}\n\n"
        sent = bot.send_message(
            message.chat.id,
            (
                f"{note}**\u041d\u0430\u0448\u043b\u0438 \u0432\u0438\u0434\u0435\u043e:** {title}\n"
                "\u0412\u044b\u0431\u0435\u0440\u0438\u0442\u0435 \u043a\u0430\u0447\u0435\u0441\u0442\u0432\u043e \u043d\u0438\u0436\u0435 \u0438\u043b\u0438 \u043d\u0430\u0436\u043c\u0438\u0442\u0435 *\u041c\u0430\u043a\u0441\u0438\u043c\u0430\u043b\u044c\u043d\u043e\u0435* / *\u0422\u043e\u043b\u044c\u043a\u043e \u0437\u0432\u0443\u043a*."
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
            bot.answer_callback_query(call.id, "\u0417\u0430\u043f\u0440\u043e\u0441 \u0443\u0441\u0442\u0430\u0440\u0435\u043b")
            return
        url, title, reaction_hint, _ = request
        reaction_message_id = None
        if reaction_hint and reaction_hint.isdigit():
            reaction_message_id = int(reaction_hint)
        if not ctx.is_required_member(call.from_user.id):
            if ctx.is_free_limit_reached(call.from_user.id):
                bot.answer_callback_query(call.id, "\u041b\u0438\u043c\u0438\u0442 \u043d\u0430 \u043f\u0435\u0440\u0438\u043e\u0434 \u0438\u0441\u0447\u0435\u0440\u043f\u0430\u043d.")
                return
            now_ts = int(datetime.now(timezone.utc).timestamp())
            storage.log_free_download(call.from_user.id, now_ts)
        if (
            MAX_ACTIVE_TASKS_PER_USER > 0
            and download_manager.active_count(call.from_user.id)
            >= MAX_ACTIVE_TASKS_PER_USER
        ):
            bot.answer_callback_query(
                call.id, "\u0414\u043e\u0441\u0442\u0438\u0433\u043d\u0443\u0442 \u043b\u0438\u043c\u0438\u0442 \u0430\u043a\u0442\u0438\u0432\u043d\u044b\u0445 \u0437\u0430\u0433\u0440\u0443\u0437\u043e\u043a. \u041f\u043e\u043f\u0440\u043e\u0431\u0443\u0439\u0442\u0435 \u043f\u043e\u0437\u0436\u0435.",
            )
            return
        queue_length = download_manager.queued_count()
        if queue_length >= download_manager.max_queue_size():
            bot.answer_callback_query(
                call.id, "\u041e\u0447\u0435\u0440\u0435\u0434\u044c \u043f\u0435\u0440\u0435\u043f\u043e\u043b\u043d\u0435\u043d\u0430. \u041f\u043e\u043f\u0440\u043e\u0431\u0443\u0439\u0442\u0435 \u043f\u043e\u0437\u0436\u0435.",
            )
            return
        queue_msg = bot.send_message(
            call.message.chat.id,
            f"{EMOJI_HOURGLASS} \u0412\u0430\u0448 \u0437\u0430\u043f\u0440\u043e\u0441 \u0432 \u043e\u0447\u0435\u0440\u0435\u0434\u0438. "
            f"\u041f\u043e\u0437\u0438\u0446\u0438\u044f: {queue_length + 1}",
        )
        ctx.add_queue_message(call.from_user.id, call.message.chat.id, queue_msg.message_id)
        bot.answer_callback_query(call.id, "\u0417\u0430\u0433\u0440\u0443\u0437\u043a\u0430 \u0434\u043e\u0431\u0430\u0432\u043b\u0435\u043d\u0430 \u0432 \u043e\u0447\u0435\u0440\u0435\u0434\u044c.")
        selected_format = None if format_id in (FORMAT_BEST, FORMAT_AUDIO) else format_id
        audio_only = format_id == FORMAT_AUDIO
        queue_download(
            call.from_user.id,
            call.message.chat.id,
            url,
            selected_format,
            title,
            status_message_id=call.message.message_id,
            queue_message_id=queue_msg.message_id,
            audio_only=audio_only,
            reaction_message_id=reaction_message_id,
        )
        storage.delete_request(token)
        try:
            bot.edit_message_text(
                f"{EMOJI_HOURGLASS} \u0417\u0430\u0433\u0440\u0443\u0437\u043a\u0430 \u0432 \u043e\u0447\u0435\u0440\u0435\u0434\u0438\u2026",
                call.message.chat.id,
                call.message.message_id,
            )
        except Exception:
            pass
        storage.set_last_inline_message_id(call.from_user.id, None)

    # --- Split handlers ---

    @bot.callback_query_handler(
        func=lambda call: call.data and call.data.startswith(f"{CB_SPLIT_YES}|")
    )
    def handle_split_yes(call: types.CallbackQuery) -> None:
        token = call.data.split("|", 1)[1]
        pending = _split_pending.pop(token, None)
        if not pending:
            bot.answer_callback_query(call.id, "\u0417\u0430\u043f\u0440\u043e\u0441 \u0443\u0441\u0442\u0430\u0440\u0435\u043b")
            return
        bot.answer_callback_query(call.id)
        file_path = pending["file_path"]
        title = pending["title"]
        audio_only = pending["audio_only"]
        user_id = pending["user_id"]
        chat_id = pending["chat_id"]
        try:
            bot.edit_message_text(
                f"{EMOJI_HOURGLASS} \u0420\u0430\u0437\u0434\u0435\u043b\u044f\u0435\u043c \u0432\u0438\u0434\u0435\u043e \u043d\u0430 \u0447\u0430\u0441\u0442\u0438\u2026",
                call.message.chat.id,
                call.message.message_id,
            )
        except Exception:
            pass
        parts = downloader.split_video(file_path, TELEGRAM_SPLIT_TARGET_SIZE)
        total_parts = len(parts)
        for i, part_path in enumerate(parts, 1):
            part_title = f"{title} (\u0447\u0430\u0441\u0442\u044c {i}/{total_parts})"
            part_size = get_file_size(part_path)
            try:
                with open(part_path, "rb") as handle:
                    _send_media(
                        user_id, chat_id, handle, part_title,
                        audio_only, file_size=part_size,
                    )
            except Exception:
                logging.exception("Failed to send part %d/%d", i, total_parts)
            finally:
                try:
                    os.remove(part_path)
                except OSError:
                    pass
        # Clean up original file
        try:
            os.remove(file_path)
        except OSError:
            pass
        try:
            bot.edit_message_text(
                f"{EMOJI_DONE} \u0412\u0438\u0434\u0435\u043e \u043e\u0442\u043f\u0440\u0430\u0432\u043b\u0435\u043d\u043e \u0432 {total_parts} \u0447\u0430\u0441\u0442\u044f\u0445.",
                call.message.chat.id,
                call.message.message_id,
            )
        except Exception:
            pass

    @bot.callback_query_handler(
        func=lambda call: call.data and call.data.startswith(f"{CB_SPLIT_NO}|")
    )
    def handle_split_no(call: types.CallbackQuery) -> None:
        token = call.data.split("|", 1)[1]
        pending = _split_pending.pop(token, None)
        if not pending:
            bot.answer_callback_query(call.id, "\u0417\u0430\u043f\u0440\u043e\u0441 \u0443\u0441\u0442\u0430\u0440\u0435\u043b")
            return
        bot.answer_callback_query(call.id)
        file_path = pending["file_path"]
        try:
            os.remove(file_path)
        except OSError:
            pass
        try:
            bot.edit_message_text(
                "\u0425\u043e\u0440\u043e\u0448\u043e. \u041f\u043e\u043f\u0440\u043e\u0431\u0443\u0439\u0442\u0435 \u0432\u044b\u0431\u0440\u0430\u0442\u044c \u0431\u043e\u043b\u0435\u0435 \u043d\u0438\u0437\u043a\u043e\u0435 \u043a\u0430\u0447\u0435\u0441\u0442\u0432\u043e.",
                call.message.chat.id,
                call.message.message_id,
            )
        except Exception:
            pass
