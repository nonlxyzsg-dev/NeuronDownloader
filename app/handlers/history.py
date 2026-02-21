"""Обработчики истории загрузок: пользовательская и админская."""

import logging

from telebot import types

from app.constants import (
    CB_ADMIN_BACK,
    CB_ADMIN_HIST_ALL,
    CB_ADMIN_HIST_PLAT_VIEW,
    CB_ADMIN_HIST_PLATFORMS,
    CB_ADMIN_HIST_SEND,
    CB_ADMIN_HIST_USER_VIEW,
    CB_ADMIN_HIST_USERS,
    CB_ADMIN_HISTORY,
    CB_MY_HIST_ALL,
    CB_MY_HIST_PLAT_VIEW,
    CB_MY_HIST_PLATFORMS,
    CB_MY_HIST_SEND,
    CB_MY_HISTORY,
    EMOJI_AUDIO,
    EMOJI_BACK,
    EMOJI_ERROR,
    EMOJI_VIDEO,
    MENU_HISTORY,
)
from app.keyboards import (
    HISTORY_PAGE_SIZE,
    build_admin_history_menu,
    build_admin_history_platforms,
    build_admin_history_users,
    build_my_history_list,
    build_my_history_menu,
    build_my_history_platforms,
)
from app.utils import is_admin, send_with_retry

logger = logging.getLogger(__name__)


def register_history_handlers(ctx) -> None:
    """Регистрирует обработчики истории загрузок."""
    bot = ctx.bot
    storage = ctx.storage

    # ------------------------------------------------------------------
    # Вспомогательная функция
    # ------------------------------------------------------------------

    def _safe_edit(chat_id, message_id, text, reply_markup=None, parse_mode=None):
        try:
            bot.edit_message_text(
                text, chat_id, message_id,
                reply_markup=reply_markup,
                parse_mode=parse_mode,
            )
        except Exception:
            bot.send_message(
                chat_id, text,
                reply_markup=reply_markup,
                parse_mode=parse_mode,
            )

    def _send_download_to_user(chat_id: int, download: tuple) -> None:
        """Отправляет закэшированное видео/аудио пользователю."""
        dl_id, _uid, platform, _status, _created_at, url, title, file_id, audio_only = download
        if not file_id:
            bot.send_message(chat_id, f"{EMOJI_ERROR} Файл больше не доступен в кэше.")
            return
        try:
            if audio_only:
                send_with_retry(
                    bot.send_audio,
                    chat_id,
                    file_id,
                    caption=title[:200] if title else None,
                    parse_mode="HTML",
                )
            else:
                send_with_retry(
                    bot.send_video,
                    chat_id,
                    file_id,
                    caption=title[:200] if title else None,
                    parse_mode="HTML",
                    supports_streaming=True,
                )
        except Exception:
            logger.exception("Не удалось отправить файл из истории (dl_id=%s)", dl_id)
            bot.send_message(chat_id, f"{EMOJI_ERROR} Не удалось отправить файл. Возможно, он устарел.")

    # ==================================================================
    # Пользовательская история
    # ==================================================================

    @bot.message_handler(func=lambda m: m.text and m.text.strip() == MENU_HISTORY)
    def handle_my_history_menu(message: types.Message) -> None:
        user_id = message.from_user.id
        total = storage.count_download_history(user_id=user_id)
        if total == 0:
            bot.send_message(message.chat.id, "У вас пока нет загрузок.")
            return
        bot.send_message(
            message.chat.id,
            f"\U0001f4c2 Ваша история загрузок ({total}):",
            reply_markup=build_my_history_menu(total),
        )

    @bot.message_handler(commands=["history"])
    def handle_history_command(message: types.Message) -> None:
        handle_my_history_menu(message)

    # --- Главное меню истории (callback) ---

    @bot.callback_query_handler(func=lambda call: call.data == CB_MY_HISTORY)
    def handle_my_history_main(call: types.CallbackQuery) -> None:
        bot.answer_callback_query(call.id)
        user_id = call.from_user.id
        total = storage.count_download_history(user_id=user_id)
        _safe_edit(
            call.message.chat.id, call.message.message_id,
            f"\U0001f4c2 Ваша история загрузок ({total}):",
            reply_markup=build_my_history_menu(total),
        )

    # --- Все загрузки пользователя ---

    @bot.callback_query_handler(
        func=lambda call: call.data and call.data.startswith(f"{CB_MY_HIST_ALL}|")
    )
    def handle_my_history_all(call: types.CallbackQuery) -> None:
        bot.answer_callback_query(call.id)
        user_id = call.from_user.id
        page = int(call.data.split("|")[1])
        total = storage.count_download_history(user_id=user_id)
        downloads = storage.get_download_history(user_id=user_id, page=page)
        _safe_edit(
            call.message.chat.id, call.message.message_id,
            f"\U0001f4c2 Все загрузки (стр. {page + 1}):",
            reply_markup=build_my_history_list(
                downloads, page, total,
                back_cb=CB_MY_HISTORY,
                send_prefix=CB_MY_HIST_SEND,
                page_prefix=CB_MY_HIST_ALL,
            ),
        )

    # --- Площадки пользователя ---

    @bot.callback_query_handler(func=lambda call: call.data == CB_MY_HIST_PLATFORMS)
    def handle_my_history_platforms(call: types.CallbackQuery) -> None:
        bot.answer_callback_query(call.id)
        user_id = call.from_user.id
        platforms = storage.get_download_platforms(user_id=user_id)
        if not platforms:
            _safe_edit(
                call.message.chat.id, call.message.message_id,
                "Нет загрузок.",
                reply_markup=None,
            )
            return
        _safe_edit(
            call.message.chat.id, call.message.message_id,
            "\U0001f4f1 Выберите площадку:",
            reply_markup=build_my_history_platforms(platforms),
        )

    # --- Загрузки по площадке ---

    @bot.callback_query_handler(
        func=lambda call: call.data and call.data.startswith(f"{CB_MY_HIST_PLAT_VIEW}|")
    )
    def handle_my_history_platform_view(call: types.CallbackQuery) -> None:
        bot.answer_callback_query(call.id)
        user_id = call.from_user.id
        parts = call.data.split("|")
        platform = parts[1]
        page = int(parts[2]) if len(parts) > 2 else 0
        total = storage.count_download_history(user_id=user_id, platform=platform)
        downloads = storage.get_download_history(user_id=user_id, platform=platform, page=page)
        _safe_edit(
            call.message.chat.id, call.message.message_id,
            f"\U0001f4f1 {platform} (стр. {page + 1}):",
            reply_markup=build_my_history_list(
                downloads, page, total,
                back_cb=CB_MY_HIST_PLATFORMS,
                send_prefix=CB_MY_HIST_SEND,
                page_prefix=CB_MY_HIST_PLAT_VIEW,
                page_suffix=platform,
            ),
        )

    # --- Отправка видео из истории (пользователь) ---

    @bot.callback_query_handler(
        func=lambda call: call.data and call.data.startswith(f"{CB_MY_HIST_SEND}|")
    )
    def handle_my_history_send(call: types.CallbackQuery) -> None:
        user_id = call.from_user.id
        dl_id = int(call.data.split("|")[1])
        download = storage.get_download_by_id(dl_id)
        if not download:
            bot.answer_callback_query(call.id, "Загрузка не найдена", show_alert=True)
            return
        # Пользователь может получить только свои загрузки
        if download[1] != user_id:
            bot.answer_callback_query(call.id, "Доступ запрещён", show_alert=True)
            return
        bot.answer_callback_query(call.id, "Отправляю\u2026")
        _send_download_to_user(call.message.chat.id, download)

    # ==================================================================
    # Админская история
    # ==================================================================

    @bot.callback_query_handler(func=lambda call: call.data == CB_ADMIN_HISTORY)
    def handle_admin_history_main(call: types.CallbackQuery) -> None:
        if not is_admin(call.from_user.id):
            bot.answer_callback_query(call.id, "Нет доступа", show_alert=True)
            return
        bot.answer_callback_query(call.id)
        total = storage.count_download_history()
        _safe_edit(
            call.message.chat.id, call.message.message_id,
            f"\U0001f4c2 История загрузок ({total}):",
            reply_markup=build_admin_history_menu(total),
        )

    # --- Все загрузки (админ) ---

    @bot.callback_query_handler(
        func=lambda call: call.data and call.data.startswith(f"{CB_ADMIN_HIST_ALL}|")
    )
    def handle_admin_history_all(call: types.CallbackQuery) -> None:
        if not is_admin(call.from_user.id):
            bot.answer_callback_query(call.id, "Нет доступа", show_alert=True)
            return
        bot.answer_callback_query(call.id)
        page = int(call.data.split("|")[1])
        total = storage.count_download_history()
        downloads = storage.get_download_history(page=page)
        _safe_edit(
            call.message.chat.id, call.message.message_id,
            f"\U0001f4c2 Все загрузки (стр. {page + 1}):",
            reply_markup=build_my_history_list(
                downloads, page, total,
                back_cb=CB_ADMIN_HISTORY,
                send_prefix=CB_ADMIN_HIST_SEND,
                page_prefix=CB_ADMIN_HIST_ALL,
            ),
        )

    # --- Площадки (админ) ---

    @bot.callback_query_handler(func=lambda call: call.data == CB_ADMIN_HIST_PLATFORMS)
    def handle_admin_history_platforms(call: types.CallbackQuery) -> None:
        if not is_admin(call.from_user.id):
            bot.answer_callback_query(call.id, "Нет доступа", show_alert=True)
            return
        bot.answer_callback_query(call.id)
        platforms = storage.get_download_platforms()
        _safe_edit(
            call.message.chat.id, call.message.message_id,
            "\U0001f4f1 Площадки:",
            reply_markup=build_admin_history_platforms(platforms),
        )

    # --- Загрузки по площадке (админ) ---

    @bot.callback_query_handler(
        func=lambda call: call.data and call.data.startswith(f"{CB_ADMIN_HIST_PLAT_VIEW}|")
    )
    def handle_admin_history_platform_view(call: types.CallbackQuery) -> None:
        if not is_admin(call.from_user.id):
            bot.answer_callback_query(call.id, "Нет доступа", show_alert=True)
            return
        bot.answer_callback_query(call.id)
        parts = call.data.split("|")
        platform = parts[1]
        page = int(parts[2]) if len(parts) > 2 else 0
        total = storage.count_download_history(platform=platform)
        downloads = storage.get_download_history(platform=platform, page=page)
        _safe_edit(
            call.message.chat.id, call.message.message_id,
            f"\U0001f4f1 {platform} (стр. {page + 1}):",
            reply_markup=build_my_history_list(
                downloads, page, total,
                back_cb=CB_ADMIN_HIST_PLATFORMS,
                send_prefix=CB_ADMIN_HIST_SEND,
                page_prefix=CB_ADMIN_HIST_PLAT_VIEW,
                page_suffix=platform,
            ),
        )

    # --- Пользователи (админ) ---

    @bot.callback_query_handler(
        func=lambda call: call.data and call.data.startswith(f"{CB_ADMIN_HIST_USERS}|")
    )
    def handle_admin_history_users(call: types.CallbackQuery) -> None:
        if not is_admin(call.from_user.id):
            bot.answer_callback_query(call.id, "Нет доступа", show_alert=True)
            return
        bot.answer_callback_query(call.id)
        page = int(call.data.split("|")[1])
        total_users = storage.count_users_with_downloads()
        users = storage.get_users_with_downloads(page=page, per_page=HISTORY_PAGE_SIZE)
        _safe_edit(
            call.message.chat.id, call.message.message_id,
            f"Пользователи с загрузками (стр. {page + 1}):",
            reply_markup=build_admin_history_users(users, page, total_users),
        )

    # --- Загрузки конкретного пользователя (админ) ---

    @bot.callback_query_handler(
        func=lambda call: call.data and call.data.startswith(f"{CB_ADMIN_HIST_USER_VIEW}|")
    )
    def handle_admin_history_user_view(call: types.CallbackQuery) -> None:
        if not is_admin(call.from_user.id):
            bot.answer_callback_query(call.id, "Нет доступа", show_alert=True)
            return
        bot.answer_callback_query(call.id)
        parts = call.data.split("|")
        target_user_id = int(parts[1])
        page = int(parts[2]) if len(parts) > 2 else 0
        total = storage.count_download_history(user_id=target_user_id)
        downloads = storage.get_download_history(user_id=target_user_id, page=page)
        user = storage.get_user(target_user_id)
        display = f"@{user[1]}" if user and user[1] else str(target_user_id)
        _safe_edit(
            call.message.chat.id, call.message.message_id,
            f"Загрузки {display} (стр. {page + 1}):",
            reply_markup=build_my_history_list(
                downloads, page, total,
                back_cb=f"{CB_ADMIN_HIST_USERS}|0",
                send_prefix=CB_ADMIN_HIST_SEND,
                page_prefix=CB_ADMIN_HIST_USER_VIEW,
                page_suffix=str(target_user_id),
            ),
        )

    # --- Отправка видео из истории (админ) ---

    @bot.callback_query_handler(
        func=lambda call: call.data and call.data.startswith(f"{CB_ADMIN_HIST_SEND}|")
    )
    def handle_admin_history_send(call: types.CallbackQuery) -> None:
        if not is_admin(call.from_user.id):
            bot.answer_callback_query(call.id, "Нет доступа", show_alert=True)
            return
        dl_id = int(call.data.split("|")[1])
        download = storage.get_download_by_id(dl_id)
        if not download:
            bot.answer_callback_query(call.id, "Загрузка не найдена", show_alert=True)
            return
        bot.answer_callback_query(call.id, "Отправляю\u2026")
        _send_download_to_user(call.message.chat.id, download)
