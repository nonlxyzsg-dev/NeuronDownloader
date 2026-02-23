"""Обработчики скачивания: приём URL, выбор качества, задача загрузки."""

import html as html_mod
import logging
import os
import queue
import time
import uuid

from datetime import datetime, timezone

from telebot import types

from app.config import (
    ENABLE_REACTIONS,
    TELEGRAM_API_SERVER_URL,
    TELEGRAM_UPLOAD_TIMEOUT_SECONDS,
)
from app.constants import (
    ACTION_TYPING,
    ACTION_UPLOAD_AUDIO,
    ACTION_UPLOAD_VIDEO,
    CB_DEVICE_ANDROID,
    CB_DEVICE_INLINE,
    CB_DEVICE_IPHONE,
    CB_DOWNLOAD,
    CB_REENCODE,
    CB_SPLIT_YES,
    CB_SPLIT_NO,
    CB_TOGGLE_REENCODE,
    CB_VIDEO_REPORT,
    DEVICE_ANDROID,
    DEVICE_IPHONE,
    DOWNLOAD_TIMEOUT_SECONDS,
    EMOJI_DOWNLOAD,
    EMOJI_DONE,
    EMOJI_ERROR,
    EMOJI_HOURGLASS,
    EMOJI_ZAP,
    FORMAT_AUDIO,
    FORMAT_BEST,
    CHANNEL_LINK,
    MENU_ADMIN,
    MENU_CHANNEL,
    DIRECT_URL_SKIP_EXTRACTORS,
    STATUS_FAILED,
    STATUS_SUCCESS,
    TELEGRAM_LOCAL_API_MAX_FILE_SIZE,
    TELEGRAM_LOCAL_API_SPLIT_TARGET_SIZE,
    TELEGRAM_MAX_FILE_SIZE,
    TELEGRAM_SPLIT_TARGET_SIZE,
)
from app.keyboards import (
    build_channel_buttons,
    build_device_selection,
    build_format_keyboard,
    build_main_menu,
    build_split_confirm_keyboard,
    build_video_buttons,
    build_video_report_button,
)
from app.downloader import _get_video_codec, download_thumbnail, ensure_h264
from app.utils import (
    append_youtube_client_hint,
    format_bytes,
    format_caption,
    format_limit_message,
    format_speed,
    get_file_size,
    is_admin,
    is_youtube_url,
    notify_admin_error,
    send_with_retry,
)


def register_download_handlers(ctx) -> None:
    """Регистрирует все обработчики, связанные со скачиванием."""
    bot = ctx.bot
    storage = ctx.storage
    downloader = ctx.downloader
    download_manager = ctx.download_manager
    active_downloads = ctx.active_downloads

    # Эффективные лимиты: локальный Bot API Server (2000 МБ) или стандартный (50 МБ)
    use_local_api = bool(TELEGRAM_API_SERVER_URL)
    max_file_size = TELEGRAM_LOCAL_API_MAX_FILE_SIZE if use_local_api else TELEGRAM_MAX_FILE_SIZE
    split_target_size = TELEGRAM_LOCAL_API_SPLIT_TARGET_SIZE if use_local_api else TELEGRAM_SPLIT_TARGET_SIZE

    # Временное хранилище: токен разделения -> информация о файле
    _split_pending: dict[str, dict] = {}

    # Временное хранилище: токен отчёта -> метаданные видео
    _video_meta: dict[str, dict] = {}

    # Временное хранилище: токен перекодирования -> данные для повторной загрузки
    _reencode_meta: dict[str, dict] = {}

    # Тогл перекодирования: токен запроса -> включено/выключено
    _reencode_toggle: dict[str, bool] = {}

    # Контекст запроса для перестроения клавиатуры: токен -> {url, options, ...}
    _request_context: dict[str, dict] = {}

    def _send_media(
        user_id: int,
        chat_id: int,
        source,
        title: str,
        audio_only: bool,
        file_size: int | None = None,
        video_tag: str = "",
        reply_markup=None,
        source_url: str = "",
        thumbnail=None,
    ) -> str | None:
        """Отправляет аудио/видео пользователю с логикой повторных попыток.

        Возвращает telegram_file_id отправленного файла (для кэширования).
        """
        caption = format_caption(title, video_tag=video_tag, source_url=source_url)
        bot.send_chat_action(
            chat_id,
            ACTION_UPLOAD_AUDIO if audio_only else ACTION_UPLOAD_VIDEO,
        )
        upload_start = time.monotonic()
        sent_msg = None
        if audio_only:
            sent_msg = send_with_retry(
                bot.send_audio,
                user_id,
                source,
                caption=caption,
                parse_mode="HTML",
                timeout=TELEGRAM_UPLOAD_TIMEOUT_SECONDS,
            )
        else:
            send_kwargs = {
                "caption": caption,
                "parse_mode": "HTML",
                "timeout": TELEGRAM_UPLOAD_TIMEOUT_SECONDS,
                "supports_streaming": True,
                "reply_markup": reply_markup,
            }
            if thumbnail is not None:
                send_kwargs["thumbnail"] = thumbnail
            sent_msg = send_with_retry(
                bot.send_video,
                user_id,
                source,
                **send_kwargs,
            )
        upload_duration = time.monotonic() - upload_start
        upload_speed = (
            format_speed(file_size / upload_duration)
            if file_size and upload_duration > 0
            else "unknown"
        )
        logging.info(
            "%s отправлено пользователю %s за %.2fs (размер=%s, скорость=%s)",
            "Аудио" if audio_only else "Видео",
            user_id,
            upload_duration,
            format_bytes(file_size) if file_size else "неизвестно",
            upload_speed,
        )
        # Извлекаем file_id для кэширования
        file_id = None
        if sent_msg:
            if audio_only and sent_msg.audio:
                file_id = sent_msg.audio.file_id
            elif not audio_only and sent_msg.video:
                file_id = sent_msg.video.file_id
        return file_id

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
            if ctx.shutdown_requested:
                logging.info("Загрузка пропущена из-за завершения работы")
                return

            # Удаляем сообщение об очереди
            ctx.remove_queue_message(user_id)

            # Дедупликация: пропускаем, если тот же URL уже скачивается
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
                    raise KeyboardInterrupt("Загрузка прервана из-за завершения работы")
                if time.monotonic() - download_started > DOWNLOAD_TIMEOUT_SECONDS:
                    raise TimeoutError(
                        f"Загрузка превысила таймаут {DOWNLOAD_TIMEOUT_SECONDS}с"
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
                            "Прогресс без общего размера (скачано=%s, скорость=%s, url=%s)",
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
                    "Задача загрузки запущена: user=%s url=%s format=%s audio=%s",
                    user_id, url, selected_format or "best", audio_only,
                )
                if reaction_message_id:
                    try:
                        bot.delete_message(chat_id, reaction_message_id)
                    except Exception:
                        pass

                # ===== Проверяем кэш: мгновенная отправка по file_id =====
                user_device = storage.get_user_device_type(user_id)
                need_reencoded = not audio_only and user_device != DEVICE_ANDROID
                cached_file_id = storage.get_cached_file(
                    url, selected_format, reencoded=need_reencoded, audio_only=audio_only,
                )
                # Для iPhone: если нет перекодированной версии, пробуем оригинал (H.264 кэш)
                if not cached_file_id and need_reencoded:
                    cached_file_id = storage.get_cached_file(
                        url, selected_format, reencoded=False, audio_only=audio_only,
                    )
                # Для Android: если нет оригинала, пробуем перекодированную
                if not cached_file_id and not need_reencoded and not audio_only:
                    cached_file_id = storage.get_cached_file(
                        url, selected_format, reencoded=True, audio_only=audio_only,
                    )
                if cached_file_id:
                    logging.info(
                        "Отправка из кэша: user=%s url=%s format=%s file_id=%s...",
                        user_id, url, selected_format or "best",
                        cached_file_id[:20],
                    )
                    if progress_message_id:
                        try:
                            bot.edit_message_text(
                                f"{EMOJI_ZAP} Видео уже в кэше \u2014 отправляем мгновенно!",
                                chat_id, progress_message_id,
                            )
                        except Exception:
                            pass
                    try:
                        _send_media(
                            user_id, chat_id, cached_file_id, title,
                            audio_only, source_url=url,
                        )
                        if progress_message_id:
                            try:
                                bot.delete_message(chat_id, progress_message_id)
                            except Exception:
                                pass
                        storage.log_download(
                            user_id, "cache", STATUS_SUCCESS,
                            url=url, title=title,
                            telegram_file_id=cached_file_id,
                            audio_only=audio_only,
                        )
                        return
                    except Exception:
                        logging.warning(
                            "Отправка из кэша не удалась, загружаем заново: %s", url,
                        )

                if progress_message_id:
                    try:
                        bot.edit_message_text(
                            f"{EMOJI_HOURGLASS} Подготовка загрузки\u2026",
                            chat_id,
                            progress_message_id,
                        )
                    except Exception:
                        progress_message_id = None
                if not progress_message_id:
                    try:
                        sent = bot.send_message(
                            chat_id,
                            f"{EMOJI_HOURGLASS} Подготовка загрузки\u2026",
                        )
                        progress_message_id = sent.message_id
                    except Exception:
                        progress_message_id = None

                # Сначала пробуем отправить по прямой ссылке
                direct_info = None
                direct_url = None
                direct_size = None
                try:
                    direct_info = downloader.get_info(url)
                    direct_url, direct_size = downloader.get_direct_url(
                        direct_info, selected_format, audio_only=audio_only,
                    )
                except Exception:
                    logging.exception("Не удалось получить прямую ссылку для %s", url)

                queue_delay = time.monotonic() - download_started
                logging.info(
                    "Загрузка начинается после %.2fs ожидания в очереди (user=%s, url=%s)",
                    queue_delay, user_id, url,
                )

                # Пропускаем прямые ссылки для платформ с защищённым CDN
                extractor_key = (direct_info or {}).get("extractor_key", "")
                if direct_url and extractor_key in DIRECT_URL_SKIP_EXTRACTORS:
                    logging.info(
                        "Прямая ссылка пропущена для %s — CDN защищён (user=%s, url=%s)",
                        extractor_key, user_id, url,
                    )
                    direct_url = None

                if direct_url:
                    if direct_size and direct_size > max_file_size:
                        logging.info(
                            "Файл по прямой ссылке слишком большой (%s), переходим к скачиванию",
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
                            sent_file_id_direct = _send_media(
                                user_id, chat_id, direct_url, title,
                                audio_only, file_size=direct_size,
                                source_url=url,
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
                                url=url, title=title,
                                telegram_file_id=sent_file_id_direct or "",
                                audio_only=audio_only,
                            )
                            return
                        except Exception:
                            logging.exception(
                                "Отправка по прямой ссылке не удалась, переходим к скачиванию "
                                "(user=%s, url=%s)", user_id, url,
                            )

                # Запасной вариант: скачиваем в файл
                if progress_message_id:
                    try:
                        bot.edit_message_text(
                            f"{EMOJI_DOWNLOAD} Скачивание\u2026",
                            chat_id, progress_message_id,
                        )
                    except Exception:
                        pass
                file_path, info = downloader.download(
                    url, selected_format,
                    audio_only=audio_only,
                    progress_callback=progress_hook,
                )
                download_duration = time.monotonic() - download_started
                total_bytes = get_file_size(file_path)
                logging.info(
                    "Скачивание завершено за %.2fs (размер=%s, путь=%s, url=%s)",
                    download_duration,
                    format_bytes(total_bytes) if total_bytes else "неизвестно",
                    file_path, url,
                )

                # Проверяем кодек — если не H.264, предложим кнопку перекодирования
                # (формат-строки приоритизируют H.264, но некоторые платформы
                # вроде Instagram/TikTok всё равно отдают VP9/HEVC).
                original_codec = None
                needs_reencode = False
                if not audio_only:
                    original_codec = _get_video_codec(file_path)
                    logging.info(
                        "Кодек видео: %s (url=%s)",
                        original_codec or "неизвестен", url,
                    )
                    codec_ok = original_codec == "h264" if original_codec else True
                    if not codec_ok:
                        needs_reencode = True
                        logging.info(
                            "Кодек %s не H.264, покажем кнопку перекодирования (url=%s)",
                            original_codec, url,
                        )

                # Если файл слишком большой, предлагаем разделить
                if total_bytes and total_bytes > max_file_size:
                    split_token = uuid.uuid4().hex[:12]
                    _split_pending[split_token] = {
                        "file_path": file_path,
                        "title": title,
                        "audio_only": audio_only,
                        "user_id": user_id,
                        "chat_id": chat_id,
                        "info": info,
                        "url": url,
                    }
                    split_text = (
                        f"Файл слишком большой ({format_bytes(total_bytes)}). "
                        f"Лимит Telegram \u2014 {format_bytes(max_file_size)}.\n\n"
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
                        url=url, title=title, audio_only=audio_only,
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

                # Кнопки под видео: отчёт + опционально перекодирование
                report_markup = None
                if not audio_only:
                    report_token = uuid.uuid4().hex[:12]
                    _video_meta[report_token] = {
                        "user_id": user_id,
                        "url": url,
                        "platform": info.get("extractor_key", "unknown"),
                        "format_id": selected_format or "best",
                        "codec": original_codec,
                        "resolution": str(info.get("height") or ""),
                        "file_size": total_bytes,
                    }
                    reencode_token = None
                    if needs_reencode:
                        reencode_token = uuid.uuid4().hex[:12]
                        _reencode_meta[reencode_token] = {
                            "user_id": user_id,
                            "url": url,
                            "title": title,
                            "selected_format": selected_format,
                            "chat_id": chat_id,
                        }
                    report_markup = build_video_buttons(report_token, reencode_token)

                # Скачиваем превью-картинку для отображения в Telegram
                thumb_path = None
                if not audio_only:
                    thumb_url = info.get("thumbnail")
                    if thumb_url:
                        thumb_path = download_thumbnail(thumb_url, downloader.data_dir)

                try:
                    thumb_file = None
                    if thumb_path:
                        thumb_file = open(thumb_path, "rb")
                    with open(file_path, "rb") as handle:
                        sent_file_id = _send_media(
                            user_id, chat_id, handle, title,
                            audio_only, file_size=total_bytes,
                            reply_markup=report_markup,
                            source_url=url,
                            thumbnail=thumb_file,
                        )
                finally:
                    if thumb_file:
                        thumb_file.close()
                    if thumb_path:
                        try:
                            os.remove(thumb_path)
                        except OSError:
                            pass

                # Кэшируем file_id для мгновенной повторной отправки
                if sent_file_id:
                    try:
                        storage.cache_file(
                            url=url,
                            format_id=selected_format,
                            reencoded=False,
                            audio_only=audio_only,
                            telegram_file_id=sent_file_id,
                            codec=original_codec,
                            resolution=str(info.get("height") or ""),
                            file_size=total_bytes,
                            platform=info.get("extractor_key", "unknown"),
                        )
                        logging.info(
                            "Файл закэширован: url=%s format=%s reencoded=False file_id=%s...",
                            url, selected_format or "best",
                            sent_file_id[:20],
                        )
                    except Exception:
                        logging.exception("Не удалось закэшировать file_id")

                try:
                    os.remove(file_path)
                except OSError:
                    logging.exception("Не удалось удалить файл %s после отправки", file_path)

                if progress_message_id:
                    try:
                        bot.delete_message(chat_id, progress_message_id)
                    except Exception:
                        pass
                storage.log_download(
                    user_id, info.get("extractor_key", "unknown"), STATUS_SUCCESS,
                    url=url, title=title,
                    telegram_file_id=sent_file_id or "",
                    audio_only=audio_only,
                )

            except TimeoutError as exc:
                storage.log_download(
                    user_id, "unknown", STATUS_FAILED,
                    url=url, title=title, audio_only=audio_only,
                )
                notify_admin_error(bot, user_id, username, f"Таймаут загрузки: {url}", exc)
                if progress_message_id:
                    try:
                        bot.edit_message_text(
                            f"{EMOJI_ERROR} {exc}", chat_id, progress_message_id,
                        )
                    except Exception:
                        pass
            except Exception as exc:
                storage.log_download(
                    user_id, "unknown", STATUS_FAILED,
                    url=url, title=title, audio_only=audio_only,
                )
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
                "Состояние очереди: в_очереди=%s/%s активных_у_пользователя=%s",
                download_manager.queued_count(),
                download_manager.max_queue_size(),
                download_manager.active_count(user_id),
            )
            download_manager.submit_user(user_id, _job)
        except queue.Full:
            bot.send_message(chat_id, "Очередь переполнена. Попробуйте позже.")

    # --- Обработчики сообщений ---

    @bot.message_handler(commands=["start", "help"])
    def send_welcome(message: types.Message) -> None:
        ctx.ensure_user(message.from_user)
        if not ctx.check_access(message.from_user.id, message.chat.id):
            return
        ctx.clear_last_inline(message.from_user.id, message.chat.id)
        ctx.set_user_state(message.from_user.id, None)
        user_is_admin = is_admin(message.from_user.id)
        bot.send_message(
            message.chat.id,
            (
                "\U0001f4be Привет! Я — Нейрон-Downloader, "
                "часть экосистемы "
                f'<a href="{CHANNEL_LINK}">«Банки с нейронами»</a> \U0001f9e0\n\n'
                "Моя работа — скачивать видео. "
                "YouTube, VK Видео, Rutube, Instagram, TikTok, "
                "X (Twitter), Facebook, Twitch, Dailymotion, Vimeo, "
                "OK.ru, Pinterest, Reddit, Likee и другие — "
                "просто кидайте ссылку.\n\n"
                "Бот работает для всех, "
                f'но у подписчиков <a href="{CHANNEL_LINK}">канала</a> '
                "нет никаких ограничений — "
                "это наше спасибо за подписку, "
                "реакции, комментарии и голоса. "
                "Вы поддерживаете канал — "
                "нейроны поддерживают вас.\n\n"
                "Пока не подписаны? "
                f'<a href="{CHANNEL_LINK}">Присоединяйтесь</a> — '
                "и лимиты исчезнут.\n\n"
                "\U0001f4dd Сообщить о проблеме — "
                "если что-то пошло не так."
            ),
            parse_mode="HTML",
            reply_markup=build_main_menu(is_admin=user_is_admin),
        )
        # Спрашиваем тип устройства, если ещё не указан
        device_type = storage.get_user_device_type(message.from_user.id)
        if not device_type:
            bot.send_message(
                message.chat.id,
                "\U0001f4f1 На каком устройстве вы смотрите видео?\n\n"
                "Это поможет нам отправлять видео в наиболее совместимом формате. "
                "На iPhone некоторые видео могут не воспроизводиться без перекодирования.",
                reply_markup=build_device_selection(),
            )

    # --- Обработчики выбора устройства ---

    @bot.callback_query_handler(
        func=lambda call: call.data and (
            call.data.startswith(CB_DEVICE_ANDROID) or call.data.startswith(CB_DEVICE_IPHONE)
        )
    )
    def handle_device_selection(call: types.CallbackQuery) -> None:
        ctx.ensure_user(call.from_user)
        parts = call.data.split("|", 1)
        device_cb = parts[0]
        inline_token = parts[1] if len(parts) > 1 else None

        if device_cb == CB_DEVICE_ANDROID:
            device = DEVICE_ANDROID
        else:
            device = DEVICE_IPHONE
        storage.set_user_device_type(call.from_user.id, device)

        # Если выбор был из клавиатуры качества — возвращаем к выбору формата
        if inline_token and inline_token in _request_context:
            device_name = "Android" if device == DEVICE_ANDROID else "iPhone"
            bot.answer_callback_query(
                call.id, f"\u0423\u0441\u0442\u0440\u043e\u0439\u0441\u0442\u0432\u043e: {device_name}",
            )
            reencode_on = _reencode_toggle.get(inline_token, False)
            result = _rebuild_format_message(inline_token, reencode_on, call.from_user.id)
            if result:
                text, markup = result
                try:
                    bot.edit_message_text(
                        text,
                        call.message.chat.id,
                        call.message.message_id,
                        parse_mode="HTML",
                        reply_markup=markup,
                    )
                except Exception:
                    pass
            return

        # Обычный выбор устройства (из /start или /device)
        if device == DEVICE_ANDROID:
            response = (
                "\U0001f4f1 Отлично, Android! Видео будут отправляться максимально быстро.\n\n"
                "Если вам понадобится перекодировать видео для iPhone "
                "(например, переслать другу), кнопка будет под видео."
            )
        else:
            response = (
                "\U0001f34f Понял, iPhone! Видео с несовместимым кодеком будут "
                "автоматически перекодироваться в H.264 перед отправкой.\n\n"
                "Это может занять 1\u20133 минуты, но зато видео гарантированно воспроизведётся."
            )
        bot.answer_callback_query(call.id)
        try:
            bot.edit_message_text(
                response,
                call.message.chat.id,
                call.message.message_id,
            )
        except Exception:
            bot.send_message(call.message.chat.id, response)

    @bot.message_handler(commands=["device"])
    def handle_device_command(message: types.Message) -> None:
        """Позволяет пользователю сменить тип устройства."""
        ctx.ensure_user(message.from_user)
        if not ctx.check_access(message.from_user.id, message.chat.id):
            return
        current = storage.get_user_device_type(message.from_user.id)
        label = {"android": "Android", "iphone": "iPhone"}.get(current or "", "не указано")
        bot.send_message(
            message.chat.id,
            f"\U0001f4f1 Текущее устройство: {label}\n\nВыберите новое:",
            reply_markup=build_device_selection(),
        )

    @bot.message_handler(func=lambda msg: msg.text == MENU_CHANNEL)
    def handle_channel_button(message: types.Message) -> None:
        """Обработчик кнопки «Банка с нейронами» — отправляет инлайн-кнопку со ссылкой на канал."""
        ctx.ensure_user(message.from_user)
        if not ctx.check_access(message.from_user.id, message.chat.id):
            return
        ctx.clear_last_inline(message.from_user.id, message.chat.id)
        markup = types.InlineKeyboardMarkup()
        markup.row(
            types.InlineKeyboardButton(
                text="\U0001f4e2 Перейти в канал",
                url=CHANNEL_LINK,
            )
        )
        bot.send_message(
            message.chat.id,
            'Канал <a href="' + CHANNEL_LINK + '">«Банка с нейронами»</a> — '
            "здесь рассказываем про ИИ-технологии простым языком.\n\n"
            "Подписчики получают безлимитные загрузки!",
            parse_mode="HTML",
            reply_markup=markup,
        )

    @bot.message_handler(func=lambda msg: msg.text == MENU_ADMIN and is_admin(msg.from_user.id))
    def handle_admin_button(message: types.Message) -> None:
        """Обработчик кнопки «Админ-панель» из reply-меню."""
        ctx.ensure_user(message.from_user)
        ctx.clear_last_inline(message.from_user.id, message.chat.id)
        open_tickets = storage.count_open_tickets()
        from app.keyboards import build_admin_menu
        markup = build_admin_menu(open_tickets=open_tickets)
        bot.send_message(
            message.chat.id,
            "\u2699\ufe0f \u041f\u0430\u043d\u0435\u043b\u044c \u0430\u0434\u043c\u0438\u043d\u0438\u0441\u0442\u0440\u0430\u0442\u043e\u0440\u0430",
            reply_markup=markup,
        )

    @bot.message_handler(func=lambda msg: msg.text is not None)
    def handle_link(message: types.Message) -> None:
        ctx.ensure_user(message.from_user)
        if not ctx.check_access(message.from_user.id, message.chat.id):
            return
        # Пропускаем, если у пользователя активное состояние (репорт, ввод админа и т.д.)
        state = ctx.get_user_state(message.from_user.id)
        if state is not None:
            return
        url = message.text.strip()
        if not url.startswith("http"):
            bot.send_message(
                message.chat.id,
                "Пожалуйста, отправьте ссылку на видео.\n\n"
                "Поддерживаемые платформы: "
                "YouTube, VK Видео, Rutube, Instagram, TikTok, "
                "X (Twitter), Facebook, Twitch, Dailymotion, Vimeo, "
                "OK.ru, Pinterest, Reddit, Likee "
                "и многие другие.",
                reply_markup=build_main_menu(is_admin=is_admin(message.from_user.id)),
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
                parse_mode="HTML",
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
                # Instagram mp4: vcodec=null, но реально H.264
                or (fmt.get("vcodec") is None and fmt.get("height")
                    and (fmt.get("ext") or "").lower() == "mp4")
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
        # Проверяем кэш для пометки мгновенно доступных форматов
        cached_entries = storage.get_cached_formats(url)
        cached_format_ids: set[str] = set()
        has_cached_best = False
        has_cached_audio = False
        for fmt_id, _reenc, _ao in cached_entries:
            if fmt_id == "best" and _ao == 0:
                has_cached_best = True
            elif _ao == 1:
                has_cached_audio = True
            else:
                cached_format_ids.add(fmt_id)
        any_cached = bool(cached_format_ids) or has_cached_best or has_cached_audio

        markup = build_format_keyboard(
            token, options,
            cached_format_ids=cached_format_ids,
            has_cached_best=has_cached_best,
            has_cached_audio=has_cached_audio,
        )
        note = ""
        if not subscribed:
            free_limit = ctx.get_free_limit()
            free_window = ctx.get_free_window()
            note = f"{format_limit_message(free_limit, free_window)}\n\n"
        cache_hint = ""
        if any_cached:
            cache_hint = f"\n{EMOJI_ZAP} \u2014 \u0443\u0436\u0435 \u0441\u043a\u0430\u0447\u0430\u043d\u043e, \u043e\u0442\u043f\u0440\u0430\u0432\u0438\u043c \u043c\u0433\u043d\u043e\u0432\u0435\u043d\u043d\u043e"
        sent = bot.send_message(
            message.chat.id,
            (
                f"{note}<b>Нашли видео:</b> {html_mod.escape(title)}\n"
                "Выберите качество ниже или нажмите <b>Максимальное</b> / <b>Только звук</b>."
                f"{cache_hint}"
            ),
            parse_mode="HTML",
            reply_markup=markup,
        )
        storage.set_last_inline_message_id(message.from_user.id, sent.message_id)

    # --- Обработчики callback-запросов ---

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
        if download_manager.queued_count() >= download_manager.max_queue_size():
            bot.answer_callback_query(
                call.id, "\u041e\u0447\u0435\u0440\u0435\u0434\u044c \u043f\u0435\u0440\u0435\u043f\u043e\u043b\u043d\u0435\u043d\u0430. \u041f\u043e\u043f\u0440\u043e\u0431\u0443\u0439\u0442\u0435 \u043f\u043e\u0437\u0436\u0435.",
            )
            return
        # Считаем будущие позиции (текущие + этот запрос)
        user_count, total = ctx.get_queue_info(call.from_user.id)
        queue_text = ctx._format_queue_text(user_count + 1, total + 1)
        queue_msg = bot.send_message(call.message.chat.id, queue_text)
        ctx.add_queue_message(call.from_user.id, call.message.chat.id, queue_msg.message_id)
        bot.answer_callback_query(call.id, "\u0417\u0430\u0433\u0440\u0443\u0437\u043a\u0430 \u0434\u043e\u0431\u0430\u0432\u043b\u0435\u043d\u0430 \u0432 \u043e\u0447\u0435\u0440\u0435\u0434\u044c.")
        selected_format = None if format_id in (FORMAT_BEST, FORMAT_AUDIO) else format_id
        audio_only = format_id == FORMAT_AUDIO
        _reencode_toggle.pop(token, None)
        _request_context.pop(token, None)
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
                f"{EMOJI_HOURGLASS} \u0417\u0430\u0433\u0440\u0443\u0437\u043a\u0430 \u0432 \u043e\u0447\u0435\u0440\u0435\u0434\u0438\u2026",
                call.message.chat.id,
                call.message.message_id,
            )
        except Exception:
            pass
        storage.set_last_inline_message_id(call.from_user.id, None)

    # --- Тогл перекодирования в клавиатуре ---

    def _rebuild_format_message(token: str, reencode_on: bool, user_id: int) -> tuple[str, types.InlineKeyboardMarkup] | None:
        """Перестраивает текст и клавиатуру выбора качества."""
        rctx = _request_context.get(token)
        if not rctx:
            return None
        markup = build_format_keyboard(
            token, rctx["options"],
            cached_format_ids=rctx["cached_format_ids"],
            has_cached_best=rctx["has_cached_best"],
            has_cached_audio=rctx["has_cached_audio"],
        )
        any_cached = bool(rctx["cached_format_ids"]) or rctx["has_cached_best"] or rctx["has_cached_audio"]
        note = ""
        if not rctx["subscribed"]:
            free_limit = ctx.get_free_limit()
            free_window = ctx.get_free_window()
            note = f"{format_limit_message(free_limit, free_window)}\n\n"
        cache_hint = ""
        if any_cached:
            cache_hint = f"\n{EMOJI_ZAP} \u2014 \u0443\u0436\u0435 \u0441\u043a\u0430\u0447\u0430\u043d\u043e, \u043e\u0442\u043f\u0440\u0430\u0432\u0438\u043c \u043c\u0433\u043d\u043e\u0432\u0435\u043d\u043d\u043e"
        text = (
            f"{note}<b>Нашли видео:</b> {html_mod.escape(rctx['title'])}\n"
            "Выберите качество ниже или нажмите <b>Максимальное</b> / <b>Только звук</b>."
            f"{cache_hint}"
        )
        return text, markup

    @bot.callback_query_handler(
        func=lambda call: call.data and call.data.startswith(f"{CB_TOGGLE_REENCODE}|")
    )
    def handle_toggle_reencode(call: types.CallbackQuery) -> None:
        """Переключает тогл принудительного перекодирования."""
        token = call.data.split("|", 1)[1]
        if token not in _request_context:
            bot.answer_callback_query(call.id, "\u0417\u0430\u043f\u0440\u043e\u0441 \u0443\u0441\u0442\u0430\u0440\u0435\u043b")
            return
        current = _reencode_toggle.get(token, False)
        new_state = not current
        _reencode_toggle[token] = new_state
        result = _rebuild_format_message(token, new_state, call.from_user.id)
        if result:
            text, markup = result
            try:
                bot.edit_message_text(
                    text,
                    call.message.chat.id,
                    call.message.message_id,
                    parse_mode="HTML",
                    reply_markup=markup,
                )
            except Exception:
                pass
        status = "\u0412\u041a\u041b" if new_state else "\u0412\u042b\u041a\u041b"
        bot.answer_callback_query(call.id, f"\u041f\u0435\u0440\u0435\u043a\u043e\u0434\u0438\u0440\u043e\u0432\u0430\u043d\u0438\u0435: {status}")

    # --- Смена устройства из клавиатуры выбора качества ---

    @bot.callback_query_handler(
        func=lambda call: call.data and call.data.startswith(f"{CB_DEVICE_INLINE}|")
    )
    def handle_device_inline(call: types.CallbackQuery) -> None:
        """Показывает выбор устройства прямо из клавиатуры качества."""
        token = call.data.split("|", 1)[1]
        if token not in _request_context:
            bot.answer_callback_query(call.id, "\u0417\u0430\u043f\u0440\u043e\u0441 \u0443\u0441\u0442\u0430\u0440\u0435\u043b")
            return
        bot.answer_callback_query(call.id)
        # Показываем выбор устройства с привязкой к токену
        markup = types.InlineKeyboardMarkup(row_width=2)
        markup.row(
            types.InlineKeyboardButton(
                text="\U0001f4f1 Android",
                callback_data=f"{CB_DEVICE_ANDROID}|{token}",
            ),
            types.InlineKeyboardButton(
                text="\U0001f34f iPhone / iPad",
                callback_data=f"{CB_DEVICE_IPHONE}|{token}",
            ),
        )
        try:
            bot.edit_message_text(
                "\U0001f4f1 \u0412\u044b\u0431\u0435\u0440\u0438\u0442\u0435 \u0443\u0441\u0442\u0440\u043e\u0439\u0441\u0442\u0432\u043e:",
                call.message.chat.id,
                call.message.message_id,
                reply_markup=markup,
            )
        except Exception:
            pass

    # --- Обработчики разделения видео ---

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
        split_url = pending.get("url", "")
        msg_id = call.message.message_id

        split_info = pending.get("info") or {}

        def _split_job() -> None:
            try:
                bot.edit_message_text(
                    f"{EMOJI_HOURGLASS} \u0420\u0430\u0437\u0434\u0435\u043b\u044f\u0435\u043c \u0432\u0438\u0434\u0435\u043e \u043d\u0430 \u0447\u0430\u0441\u0442\u0438\u2026",
                    chat_id, msg_id,
                )
            except Exception:
                pass

            # Скачиваем превью один раз для всех частей
            split_thumb_path = None
            if not audio_only:
                thumb_url = split_info.get("thumbnail")
                if thumb_url:
                    split_thumb_path = download_thumbnail(thumb_url, downloader.data_dir)

            parts = downloader.split_video(file_path, split_target_size)
            total_parts = len(parts)
            video_tag = f"#nd_{uuid.uuid4().hex[:6]}" if total_parts > 1 else ""
            for i, part_path in enumerate(parts, 1):
                part_title = f"{title} (\u0447\u0430\u0441\u0442\u044c {i}/{total_parts})"
                part_size = get_file_size(part_path)
                try:
                    split_thumb_file = None
                    if split_thumb_path:
                        split_thumb_file = open(split_thumb_path, "rb")
                    try:
                        with open(part_path, "rb") as handle:
                            _send_media(
                                user_id, chat_id, handle, part_title,
                                audio_only, file_size=part_size,
                                video_tag=video_tag,
                                source_url=split_url,
                                thumbnail=split_thumb_file,
                            )
                    finally:
                        if split_thumb_file:
                            split_thumb_file.close()
                except Exception:
                    logging.exception("\u041d\u0435 \u0443\u0434\u0430\u043b\u043e\u0441\u044c \u043e\u0442\u043f\u0440\u0430\u0432\u0438\u0442\u044c \u0447\u0430\u0441\u0442\u044c %d/%d", i, total_parts)
                finally:
                    try:
                        os.remove(part_path)
                    except OSError:
                        pass

            if split_thumb_path:
                try:
                    os.remove(split_thumb_path)
                except OSError:
                    pass
            # Удаляем исходный файл
            try:
                os.remove(file_path)
            except OSError:
                pass
            try:
                bot.edit_message_text(
                    f"{EMOJI_DONE} \u0412\u0438\u0434\u0435\u043e \u043e\u0442\u043f\u0440\u0430\u0432\u043b\u0435\u043d\u043e \u0432 {total_parts} \u0447\u0430\u0441\u0442\u044f\u0445.",
                    chat_id, msg_id,
                )
            except Exception:
                pass

        try:
            bot.edit_message_text(
                f"{EMOJI_HOURGLASS} \u0417\u0430\u0433\u0440\u0443\u0437\u043a\u0430 \u0432 \u043e\u0447\u0435\u0440\u0435\u0434\u0438\u2026",
                chat_id, msg_id,
            )
        except Exception:
            pass
        try:
            download_manager.submit_user(user_id, _split_job)
        except queue.Full:
            bot.send_message(chat_id, "\u041e\u0447\u0435\u0440\u0435\u0434\u044c \u043f\u0435\u0440\u0435\u043f\u043e\u043b\u043d\u0435\u043d\u0430. \u041f\u043e\u043f\u0440\u043e\u0431\u0443\u0439\u0442\u0435 \u043f\u043e\u0437\u0436\u0435.")

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
                "Хорошо. Попробуйте выбрать более низкое качество.",
                call.message.chat.id,
                call.message.message_id,
            )
        except Exception:
            pass

    # --- Обработчик отчётов о проблемах с воспроизведением ---

    @bot.callback_query_handler(
        func=lambda call: call.data and call.data.startswith(f"{CB_VIDEO_REPORT}|")
    )
    def handle_video_report(call: types.CallbackQuery) -> None:
        token = call.data.split("|", 1)[1]
        meta = _video_meta.pop(token, None)
        if meta is None:
            bot.answer_callback_query(
                call.id,
                "Данные устарели. Если проблема сохраняется, скачайте видео заново.",
                show_alert=True,
            )
            return
        bot.answer_callback_query(call.id)
        incident_id = storage.create_video_incident(
            user_id=call.from_user.id,
            url=meta.get("url"),
            platform=meta.get("platform"),
            format_id=meta.get("format_id"),
            codec=meta.get("codec"),
            resolution=meta.get("resolution"),
            file_size=meta.get("file_size"),
        )
        bot.send_message(
            call.message.chat.id,
            "Спасибо за обратную связь! Мы зафиксировали проблему "
            f"(#{incident_id}) и обязательно её изучим.",
        )
        # Уведомляем админов
        platform = meta.get("platform") or "?"
        codec = meta.get("codec") or "?"
        resolution = meta.get("resolution") or "?"
        user_row = storage.get_user(call.from_user.id)
        username = ""
        if user_row:
            username = user_row[1] or user_row[2] or ""
        user_label = f"@{username}" if username else str(call.from_user.id)
        notify_admin_error(
            bot, call.from_user.id, username,
            f"Инцидент #{incident_id}: видео не воспроизводится "
            f"({platform}, кодек={codec}, {resolution}p)",
            Exception(meta.get("url", "")),
        )

    # --- Обработчик перекодирования по запросу ---

    @bot.callback_query_handler(
        func=lambda call: call.data and call.data.startswith(f"{CB_REENCODE}|")
    )
    def handle_reencode(call: types.CallbackQuery) -> None:
        token = call.data.split("|", 1)[1]
        meta = _reencode_meta.pop(token, None)
        if meta is None:
            bot.answer_callback_query(
                call.id,
                "Данные устарели. Скачайте видео заново.",
                show_alert=True,
            )
            return
        bot.answer_callback_query(call.id)
        re_user_id = meta["user_id"]
        re_url = meta["url"]
        re_title = meta["title"]
        re_format = meta["selected_format"]
        re_chat_id = meta["chat_id"]

        progress_msg = bot.send_message(
            re_chat_id,
            f"\U0001f504 Перекодировка запрошена. Скачиваем видео повторно\u2026",
        )
        progress_mid = progress_msg.message_id

        def _reencode_job() -> None:
            try:
                # Проверяем кэш перекодированной версии
                cached_fid = storage.get_cached_file(
                    re_url, re_format, reencoded=True, audio_only=False,
                )
                if cached_fid:
                    logging.info(
                        "Перекодированная версия в кэше: url=%s file_id=%s...",
                        re_url, cached_fid[:20],
                    )
                    try:
                        bot.edit_message_text(
                            f"{EMOJI_ZAP} Перекодированное видео уже в кэше \u2014 отправляем!",
                            re_chat_id, progress_mid,
                        )
                    except Exception:
                        pass
                    _send_media(
                        re_user_id, re_chat_id, cached_fid,
                        f"{re_title} (H.264)", False,
                        source_url=re_url,
                    )
                    try:
                        bot.delete_message(re_chat_id, progress_mid)
                    except Exception:
                        pass
                    return

                file_path, info = downloader.download(
                    re_url, re_format, audio_only=False,
                )
                total_bytes = get_file_size(file_path)
                size_mb = (total_bytes or 0) / (1024 * 1024)
                est_minutes = max(1, int(size_mb * 0.6))
                try:
                    bot.edit_message_text(
                        f"\U0001f504 Перекодируем видео в H.264...\n"
                        f"\u23f3 Это может занять ~{est_minutes} мин.",
                        re_chat_id, progress_mid,
                    )
                except Exception:
                    pass
                reencode_start = time.monotonic()
                file_path, was_reencoded, codec = ensure_h264(file_path)
                reencode_duration = time.monotonic() - reencode_start
                total_bytes = get_file_size(file_path)
                logging.info(
                    "Перекодирование по запросу за %.2fs (кодек=%s, размер=%s, url=%s)",
                    reencode_duration, codec,
                    format_bytes(total_bytes) if total_bytes else "?",
                    re_url,
                )
                if total_bytes and total_bytes > max_file_size:
                    try:
                        bot.edit_message_text(
                            f"{EMOJI_ERROR} Перекодированный файл слишком большой "
                            f"({format_bytes(total_bytes)}). Попробуйте меньшее качество.",
                            re_chat_id, progress_mid,
                        )
                    except Exception:
                        pass
                    try:
                        os.remove(file_path)
                    except OSError:
                        pass
                    return
                try:
                    bot.edit_message_text(
                        f"{EMOJI_DONE} Перекодировано. Отправляем\u2026",
                        re_chat_id, progress_mid,
                    )
                except Exception:
                    pass
                # Скачиваем превью для перекодированного видео
                re_thumb_path = None
                thumb_url = info.get("thumbnail")
                if thumb_url:
                    re_thumb_path = download_thumbnail(thumb_url, downloader.data_dir)

                try:
                    re_thumb_file = None
                    if re_thumb_path:
                        re_thumb_file = open(re_thumb_path, "rb")
                    with open(file_path, "rb") as handle:
                        sent_fid = _send_media(
                            re_user_id, re_chat_id, handle,
                            f"{re_title} (H.264)", False,
                            file_size=total_bytes,
                            source_url=re_url,
                            thumbnail=re_thumb_file,
                        )
                finally:
                    if re_thumb_file:
                        re_thumb_file.close()
                    if re_thumb_path:
                        try:
                            os.remove(re_thumb_path)
                        except OSError:
                            pass

                # Кэшируем перекодированную версию
                if sent_fid:
                    try:
                        storage.cache_file(
                            url=re_url,
                            format_id=re_format,
                            reencoded=True,
                            audio_only=False,
                            telegram_file_id=sent_fid,
                            codec="h264",
                            file_size=total_bytes,
                        )
                    except Exception:
                        logging.exception("Не удалось закэшировать перекодированный file_id")
                try:
                    os.remove(file_path)
                except OSError:
                    pass
                try:
                    bot.delete_message(re_chat_id, progress_mid)
                except Exception:
                    pass
            except Exception as exc:
                logging.exception("Ошибка перекодировки по запросу: %s", re_url)
                try:
                    bot.edit_message_text(
                        f"{EMOJI_ERROR} Не удалось перекодировать: {exc}",
                        re_chat_id, progress_mid,
                    )
                except Exception:
                    pass

        try:
            download_manager.submit_user(re_user_id, _reencode_job)
        except queue.Full:
            bot.send_message(re_chat_id, "Очередь переполнена. Попробуйте позже.")
