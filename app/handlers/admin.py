"""Admin panel handlers: /admin command, inline menu, settings, tickets, stats."""

import logging
import math
import os
import sys

from telebot import types

from app.config import ADMIN_IDS, FREE_DOWNLOAD_LIMIT, FREE_DOWNLOAD_WINDOW_SECONDS
from app.constants import (
    CB_ADMIN,
    CB_ADMIN_STATS,
    CB_ADMIN_STATS_PLATFORM,
    CB_ADMIN_STATS_DAILY,
    CB_ADMIN_STATS_USERS,
    CB_ADMIN_USERS,
    CB_ADMIN_USER_BLOCK,
    CB_ADMIN_USER_UNBLOCK,
    CB_ADMIN_USERS_PAGE,
    CB_ADMIN_SETTINGS,
    CB_ADMIN_TICKETS,
    CB_ADMIN_RESTART,
    CB_ADMIN_RESTART_CONFIRM,
    CB_ADMIN_BACK,
    CB_ADMIN_SET_LIMIT,
    CB_ADMIN_SET_WINDOW,
    CB_ADMIN_CHANNELS,
    CB_ADMIN_CHANNEL_DEL,
    CB_TICKET_VIEW,
    CB_TICKET_REPLY,
    CB_TICKET_CLOSE,
    CB_TICKET_LIST,
    EMOJI_STATS,
    EMOJI_BACK,
    EMOJI_DONE,
    STATE_AWAITING_LIMIT,
    STATE_AWAITING_WINDOW,
    STATE_AWAITING_CHANNEL_ID,
    STATE_REPLYING_TICKET,
)
from app.keyboards import (
    build_admin_menu,
    build_admin_back,
    build_admin_stats_submenu,
    build_admin_users_page,
    build_admin_settings,
    build_admin_channels,
    build_admin_tickets,
    build_ticket_actions,
    build_restart_confirm,
)
from app.utils import is_admin, format_bytes

logger = logging.getLogger(__name__)

USERS_PER_PAGE = 10


def register_admin_handlers(ctx) -> None:
    """Register all admin-related handlers."""
    bot = ctx.bot
    storage = ctx.storage

    # ------------------------------------------------------------------
    # Helper: safe edit or fallback to send
    # ------------------------------------------------------------------

    def _safe_edit(chat_id: int, message_id: int, text: str,
                   reply_markup=None, parse_mode=None):
        """Try to edit a message; fall back to sending a new one."""
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

    # ------------------------------------------------------------------
    # Helper: render paginated user list
    # ------------------------------------------------------------------

    def _show_users_page(chat_id: int, message_id: int, page: int):
        all_users = storage.list_users()
        total_pages = max(1, math.ceil(len(all_users) / USERS_PER_PAGE))
        page = max(0, min(page, total_pages - 1))
        start = page * USERS_PER_PAGE
        page_users = all_users[start:start + USERS_PER_PAGE]
        user_stats = storage.get_user_stats()
        download_counts = {uid: count for uid, count in user_stats}
        markup = build_admin_users_page(page_users, page, total_pages, download_counts)
        _safe_edit(
            chat_id, message_id,
            f"👥 Пользователи (стр. {page + 1}/{total_pages}):",
            reply_markup=markup,
        )

    # ------------------------------------------------------------------
    # Helper: render ticket list
    # ------------------------------------------------------------------

    def _show_tickets(chat_id: int, message_id: int):
        tickets = storage.list_open_tickets()
        users_map: dict[int, str] = {}
        for ticket_id, user_id, status, created_at in tickets:
            if user_id not in users_map:
                user_row = storage.get_user(user_id)
                if user_row:
                    users_map[user_id] = user_row[1] or user_row[2] or str(user_id)
                else:
                    users_map[user_id] = str(user_id)
        markup = build_admin_tickets(tickets, users_map)
        count = len(tickets)
        _safe_edit(
            chat_id, message_id,
            f"📬 Открытые обращения: {count}",
            reply_markup=markup,
        )

    # ==================================================================
    # 1. /admin command
    # ==================================================================

    @bot.message_handler(commands=["admin"])
    def cmd_admin(message: types.Message):
        ctx.ensure_user(message.from_user)
        user_id = message.from_user.id
        if not is_admin(user_id):
            return
        ctx.clear_last_inline(user_id, message.chat.id)
        open_tickets = storage.count_open_tickets()
        markup = build_admin_menu(open_tickets=open_tickets)
        bot.send_message(
            message.chat.id,
            "⚙️ Панель администратора",
            reply_markup=markup,
        )

    # ==================================================================
    # 2. CB_ADMIN_BACK -> return to admin menu
    # ==================================================================

    @bot.callback_query_handler(func=lambda c: c.data == CB_ADMIN_BACK)
    def cb_admin_back(call: types.CallbackQuery):
        user_id = call.from_user.id
        if not is_admin(user_id):
            bot.answer_callback_query(call.id, "Доступ запрещён.")
            return
        bot.answer_callback_query(call.id)
        open_tickets = storage.count_open_tickets()
        markup = build_admin_menu(open_tickets=open_tickets)
        _safe_edit(
            call.message.chat.id, call.message.message_id,
            "⚙️ Панель администратора",
            reply_markup=markup,
        )

    # ==================================================================
    # 3. CB_ADMIN_STATS -> general stats
    # ==================================================================

    @bot.callback_query_handler(func=lambda c: c.data == CB_ADMIN_STATS)
    def cb_admin_stats(call: types.CallbackQuery):
        user_id = call.from_user.id
        if not is_admin(user_id):
            bot.answer_callback_query(call.id, "Доступ запрещён.")
            return
        bot.answer_callback_query(call.id)
        total_users, total_downloads = storage.get_usage_stats()
        today = storage.get_downloads_today()
        week = storage.get_downloads_week()
        text = (
            f"📊 Статистика\n\n"
            f"Пользователей: {total_users}\n"
            f"Загрузок всего: {total_downloads}\n"
            f"Загрузок сегодня: {today}\n"
            f"Загрузок за неделю: {week}"
        )
        markup = build_admin_stats_submenu()
        _safe_edit(call.message.chat.id, call.message.message_id, text, reply_markup=markup)

    # ==================================================================
    # 4. CB_ADMIN_STATS_PLATFORM -> stats by platform
    # ==================================================================

    @bot.callback_query_handler(func=lambda c: c.data == CB_ADMIN_STATS_PLATFORM)
    def cb_stats_platform(call: types.CallbackQuery):
        user_id = call.from_user.id
        if not is_admin(user_id):
            bot.answer_callback_query(call.id, "Доступ запрещён.")
            return
        bot.answer_callback_query(call.id)
        rows = storage.get_stats_by_platform()
        if not rows:
            text = "📱 Статистика по платформам\n\nНет данных."
        else:
            lines = ["📱 Статистика по платформам\n"]
            for platform, count in rows:
                lines.append(f"• {platform}: {count}")
            text = "\n".join(lines)
        markup = build_admin_back()
        _safe_edit(call.message.chat.id, call.message.message_id, text, reply_markup=markup)

    # ==================================================================
    # 5. CB_ADMIN_STATS_DAILY -> stats by day (7 days)
    # ==================================================================

    @bot.callback_query_handler(func=lambda c: c.data == CB_ADMIN_STATS_DAILY)
    def cb_stats_daily(call: types.CallbackQuery):
        user_id = call.from_user.id
        if not is_admin(user_id):
            bot.answer_callback_query(call.id, "Доступ запрещён.")
            return
        bot.answer_callback_query(call.id)
        rows = storage.get_stats_by_day(days=7)
        if not rows:
            text = "📅 Загрузки по дням\n\nНет данных."
        else:
            lines = ["📅 Загрузки по дням (последние 7)\n"]
            for day, count in rows:
                lines.append(f"• {day}: {count}")
            text = "\n".join(lines)
        markup = build_admin_back()
        _safe_edit(call.message.chat.id, call.message.message_id, text, reply_markup=markup)

    # ==================================================================
    # 6. CB_ADMIN_STATS_USERS -> top 10 users
    # ==================================================================

    @bot.callback_query_handler(func=lambda c: c.data == CB_ADMIN_STATS_USERS)
    def cb_stats_users(call: types.CallbackQuery):
        user_id = call.from_user.id
        if not is_admin(user_id):
            bot.answer_callback_query(call.id, "Доступ запрещён.")
            return
        bot.answer_callback_query(call.id)
        rows = storage.get_user_stats()[:10]
        if not rows:
            text = "👥 Топ пользователей\n\nНет данных."
        else:
            lines = ["👥 Топ пользователей по загрузкам\n"]
            for i, (uid, count) in enumerate(rows, 1):
                user_row = storage.get_user(uid)
                if user_row:
                    display = user_row[1] or user_row[2] or str(uid)
                else:
                    display = str(uid)
                lines.append(f"{i}. @{display} — {count}")
            text = "\n".join(lines)
        markup = build_admin_back()
        _safe_edit(call.message.chat.id, call.message.message_id, text, reply_markup=markup)

    # ==================================================================
    # 7. CB_ADMIN_USERS -> paginated user list (page 0)
    # ==================================================================

    @bot.callback_query_handler(func=lambda c: c.data == CB_ADMIN_USERS)
    def cb_admin_users(call: types.CallbackQuery):
        user_id = call.from_user.id
        if not is_admin(user_id):
            bot.answer_callback_query(call.id, "Доступ запрещён.")
            return
        bot.answer_callback_query(call.id)
        _show_users_page(call.message.chat.id, call.message.message_id, 0)

    # ==================================================================
    # 8. CB_ADMIN_USERS_PAGE|{page} -> navigate pages
    # ==================================================================

    @bot.callback_query_handler(
        func=lambda c: c.data and c.data.startswith(f"{CB_ADMIN_USERS_PAGE}|")
    )
    def cb_users_page(call: types.CallbackQuery):
        user_id = call.from_user.id
        if not is_admin(user_id):
            bot.answer_callback_query(call.id, "Доступ запрещён.")
            return
        bot.answer_callback_query(call.id)
        try:
            page = int(call.data.split("|", 1)[1])
        except (ValueError, IndexError):
            page = 0
        _show_users_page(call.message.chat.id, call.message.message_id, page)

    # ==================================================================
    # 9. CB_ADMIN_USER_BLOCK|{user_id} -> block user
    # ==================================================================

    @bot.callback_query_handler(
        func=lambda c: c.data and c.data.startswith(f"{CB_ADMIN_USER_BLOCK}|")
    )
    def cb_user_block(call: types.CallbackQuery):
        user_id = call.from_user.id
        if not is_admin(user_id):
            bot.answer_callback_query(call.id, "Доступ запрещён.")
            return
        try:
            target_id = int(call.data.split("|", 1)[1])
        except (ValueError, IndexError):
            bot.answer_callback_query(call.id, "Ошибка.")
            return
        storage.set_blocked(target_id, True)
        bot.answer_callback_query(call.id, f"Пользователь {target_id} заблокирован.")
        _show_users_page(call.message.chat.id, call.message.message_id, 0)

    # ==================================================================
    # 10. CB_ADMIN_USER_UNBLOCK|{user_id} -> unblock user
    # ==================================================================

    @bot.callback_query_handler(
        func=lambda c: c.data and c.data.startswith(f"{CB_ADMIN_USER_UNBLOCK}|")
    )
    def cb_user_unblock(call: types.CallbackQuery):
        user_id = call.from_user.id
        if not is_admin(user_id):
            bot.answer_callback_query(call.id, "Доступ запрещён.")
            return
        try:
            target_id = int(call.data.split("|", 1)[1])
        except (ValueError, IndexError):
            bot.answer_callback_query(call.id, "Ошибка.")
            return
        storage.set_blocked(target_id, False)
        bot.answer_callback_query(call.id, f"Пользователь {target_id} разблокирован.")
        _show_users_page(call.message.chat.id, call.message.message_id, 0)

    # ==================================================================
    # 11. CB_ADMIN_SETTINGS -> show settings
    # ==================================================================

    @bot.callback_query_handler(func=lambda c: c.data == CB_ADMIN_SETTINGS)
    def cb_admin_settings(call: types.CallbackQuery):
        user_id = call.from_user.id
        if not is_admin(user_id):
            bot.answer_callback_query(call.id, "Доступ запрещён.")
            return
        bot.answer_callback_query(call.id)
        free_limit = ctx.get_free_limit()
        window = ctx.get_free_window() // 3600
        channels = storage.get_required_channels()
        markup = build_admin_settings(free_limit, window, len(channels))
        _safe_edit(
            call.message.chat.id, call.message.message_id,
            "⚙️ Настройки бота",
            reply_markup=markup,
        )

    # ==================================================================
    # 12. CB_ADMIN_SET_LIMIT -> ask admin to type number
    # ==================================================================

    @bot.callback_query_handler(func=lambda c: c.data == CB_ADMIN_SET_LIMIT)
    def cb_set_limit(call: types.CallbackQuery):
        user_id = call.from_user.id
        if not is_admin(user_id):
            bot.answer_callback_query(call.id, "Доступ запрещён.")
            return
        bot.answer_callback_query(call.id)
        ctx.set_user_state(user_id, STATE_AWAITING_LIMIT)
        bot.send_message(call.message.chat.id, "Введите новый лимит (число):")

    # ==================================================================
    # 13. CB_ADMIN_SET_WINDOW -> ask admin to type hours
    # ==================================================================

    @bot.callback_query_handler(func=lambda c: c.data == CB_ADMIN_SET_WINDOW)
    def cb_set_window(call: types.CallbackQuery):
        user_id = call.from_user.id
        if not is_admin(user_id):
            bot.answer_callback_query(call.id, "Доступ запрещён.")
            return
        bot.answer_callback_query(call.id)
        ctx.set_user_state(user_id, STATE_AWAITING_WINDOW)
        bot.send_message(call.message.chat.id, "Введите период в часах (число):")

    # ==================================================================
    # 14. Text handler: STATE_AWAITING_LIMIT
    # ==================================================================

    @bot.message_handler(func=lambda m: (
        m.text is not None
        and is_admin(m.from_user.id)
        and ctx.get_user_state(m.from_user.id) == STATE_AWAITING_LIMIT
    ))
    def handle_set_limit(message: types.Message):
        user_id = message.from_user.id
        text = message.text.strip()
        try:
            value = int(text)
            if value <= 0:
                raise ValueError
        except ValueError:
            bot.send_message(message.chat.id, "Введите положительное целое число.")
            return
        storage.set_setting("free_download_limit", str(value))
        ctx.set_user_state(user_id, None)
        bot.send_message(message.chat.id, f"✅ Лимит обновлён: {value}")

    # ==================================================================
    # 15. Text handler: STATE_AWAITING_WINDOW
    # ==================================================================

    @bot.message_handler(func=lambda m: (
        m.text is not None
        and is_admin(m.from_user.id)
        and ctx.get_user_state(m.from_user.id) == STATE_AWAITING_WINDOW
    ))
    def handle_set_window(message: types.Message):
        user_id = message.from_user.id
        text = message.text.strip()
        try:
            hours = int(text)
            if hours <= 0:
                raise ValueError
        except ValueError:
            bot.send_message(message.chat.id, "Введите положительное целое число.")
            return
        seconds = hours * 3600
        storage.set_setting("free_download_window", str(seconds))
        ctx.set_user_state(user_id, None)
        bot.send_message(message.chat.id, f"✅ Период обновлён: {hours} ч.")

    # ==================================================================
    # 16. CB_ADMIN_CHANNELS -> show channel list
    # ==================================================================

    @bot.callback_query_handler(func=lambda c: c.data == CB_ADMIN_CHANNELS)
    def cb_admin_channels(call: types.CallbackQuery):
        user_id = call.from_user.id
        if not is_admin(user_id):
            bot.answer_callback_query(call.id, "Доступ запрещён.")
            return
        bot.answer_callback_query(call.id)
        channels = storage.get_required_channels()
        markup = build_admin_channels(channels)
        _safe_edit(
            call.message.chat.id, call.message.message_id,
            "📢 Обязательные каналы:",
            reply_markup=markup,
        )

    # ==================================================================
    # 17. CB_ADMIN_CHANNELS|add -> ask admin to type channel ID
    # ==================================================================

    @bot.callback_query_handler(
        func=lambda c: c.data == f"{CB_ADMIN_CHANNELS}|add"
    )
    def cb_channel_add(call: types.CallbackQuery):
        user_id = call.from_user.id
        if not is_admin(user_id):
            bot.answer_callback_query(call.id, "Доступ запрещён.")
            return
        bot.answer_callback_query(call.id)
        ctx.set_user_state(user_id, STATE_AWAITING_CHANNEL_ID)
        bot.send_message(
            call.message.chat.id,
            "Введите ID канала (число, например -1001234567890):",
        )

    # ==================================================================
    # 18. Text handler: STATE_AWAITING_CHANNEL_ID
    # ==================================================================

    @bot.message_handler(func=lambda m: (
        m.text is not None
        and is_admin(m.from_user.id)
        and ctx.get_user_state(m.from_user.id) == STATE_AWAITING_CHANNEL_ID
    ))
    def handle_add_channel(message: types.Message):
        user_id = message.from_user.id
        text = message.text.strip()
        try:
            chat_id = int(text)
        except ValueError:
            bot.send_message(message.chat.id, "Введите корректный числовой ID канала.")
            return
        try:
            chat_info = bot.get_chat(chat_id)
            title = chat_info.title or str(chat_id)
            invite_link = chat_info.invite_link or None
        except Exception as exc:
            logger.warning("Failed to get chat info for %s: %s", chat_id, exc)
            title = str(chat_id)
            invite_link = None
        storage.add_required_channel(chat_id, title, invite_link)
        ctx.set_user_state(user_id, None)
        bot.send_message(
            message.chat.id,
            f"✅ Канал добавлен: {title} ({chat_id})",
        )

    # ==================================================================
    # 19. CB_ADMIN_CHANNEL_DEL|{chat_id} -> remove channel
    # ==================================================================

    @bot.callback_query_handler(
        func=lambda c: c.data and c.data.startswith(f"{CB_ADMIN_CHANNEL_DEL}|")
    )
    def cb_channel_del(call: types.CallbackQuery):
        user_id = call.from_user.id
        if not is_admin(user_id):
            bot.answer_callback_query(call.id, "Доступ запрещён.")
            return
        try:
            target_chat_id = int(call.data.split("|", 1)[1])
        except (ValueError, IndexError):
            bot.answer_callback_query(call.id, "Ошибка.")
            return
        storage.remove_required_channel(target_chat_id)
        bot.answer_callback_query(call.id, "Канал удалён.")
        channels = storage.get_required_channels()
        markup = build_admin_channels(channels)
        _safe_edit(
            call.message.chat.id, call.message.message_id,
            "📢 Обязательные каналы:",
            reply_markup=markup,
        )

    # ==================================================================
    # 20. CB_ADMIN_TICKETS -> list open tickets
    # ==================================================================

    @bot.callback_query_handler(func=lambda c: c.data == CB_ADMIN_TICKETS)
    def cb_admin_tickets(call: types.CallbackQuery):
        user_id = call.from_user.id
        if not is_admin(user_id):
            bot.answer_callback_query(call.id, "Доступ запрещён.")
            return
        bot.answer_callback_query(call.id)
        _show_tickets(call.message.chat.id, call.message.message_id)

    # ==================================================================
    # 21. CB_TICKET_VIEW|{ticket_id} -> show ticket conversation
    # ==================================================================

    @bot.callback_query_handler(
        func=lambda c: c.data and c.data.startswith(f"{CB_TICKET_VIEW}|")
    )
    def cb_ticket_view(call: types.CallbackQuery):
        user_id = call.from_user.id
        if not is_admin(user_id):
            bot.answer_callback_query(call.id, "Доступ запрещён.")
            return
        bot.answer_callback_query(call.id)
        try:
            ticket_id = int(call.data.split("|", 1)[1])
        except (ValueError, IndexError):
            return
        ticket = storage.get_ticket(ticket_id)
        if not ticket:
            _safe_edit(
                call.message.chat.id, call.message.message_id,
                "Обращение не найдено.",
                reply_markup=build_admin_back(),
            )
            return
        _tid, ticket_user_id, status, created_at = ticket
        messages = storage.get_ticket_messages(ticket_id)
        lines = [f"📬 Обращение #{ticket_id} (от {created_at})\n"]
        user_row = storage.get_user(ticket_user_id)
        if user_row:
            display = user_row[1] or user_row[2] or str(ticket_user_id)
            lines.append(f"Пользователь: @{display}\n")
        else:
            lines.append(f"Пользователь: {ticket_user_id}\n")
        for msg_id, from_uid, msg_is_admin, text, file_id, file_type, msg_time in messages:
            sender = "👤 Админ" if msg_is_admin else "👤 Пользователь"
            content = text or f"[{file_type or 'файл'}]"
            lines.append(f"{sender} ({msg_time}):\n{content}\n")
        text = "\n".join(lines)
        # Telegram message limit
        if len(text) > 4000:
            text = text[:4000] + "\n..."
        markup = build_ticket_actions(ticket_id)
        _safe_edit(call.message.chat.id, call.message.message_id, text, reply_markup=markup)

    # ==================================================================
    # 22. CB_TICKET_REPLY|{ticket_id} -> set state to reply
    # ==================================================================

    @bot.callback_query_handler(
        func=lambda c: c.data and c.data.startswith(f"{CB_TICKET_REPLY}|")
    )
    def cb_ticket_reply(call: types.CallbackQuery):
        user_id = call.from_user.id
        if not is_admin(user_id):
            bot.answer_callback_query(call.id, "Доступ запрещён.")
            return
        bot.answer_callback_query(call.id)
        try:
            ticket_id = int(call.data.split("|", 1)[1])
        except (ValueError, IndexError):
            return
        ctx.set_user_state(user_id, (STATE_REPLYING_TICKET, ticket_id))
        bot.send_message(
            call.message.chat.id,
            "Отправьте ответ (текст, фото или видео):",
        )

    # ==================================================================
    # 23. CB_TICKET_CLOSE|{ticket_id} -> close ticket
    # ==================================================================

    @bot.callback_query_handler(
        func=lambda c: c.data and c.data.startswith(f"{CB_TICKET_CLOSE}|")
    )
    def cb_ticket_close(call: types.CallbackQuery):
        user_id = call.from_user.id
        if not is_admin(user_id):
            bot.answer_callback_query(call.id, "Доступ запрещён.")
            return
        try:
            ticket_id = int(call.data.split("|", 1)[1])
        except (ValueError, IndexError):
            bot.answer_callback_query(call.id, "Ошибка.")
            return
        storage.close_ticket(ticket_id)
        bot.answer_callback_query(call.id, "Обращение закрыто.")
        _safe_edit(
            call.message.chat.id, call.message.message_id,
            f"Обращение #{ticket_id} закрыто.",
            reply_markup=build_admin_back(),
        )

    # ==================================================================
    # 24. CB_TICKET_LIST -> same as CB_ADMIN_TICKETS
    # ==================================================================

    @bot.callback_query_handler(func=lambda c: c.data == CB_TICKET_LIST)
    def cb_ticket_list(call: types.CallbackQuery):
        user_id = call.from_user.id
        if not is_admin(user_id):
            bot.answer_callback_query(call.id, "Доступ запрещён.")
            return
        bot.answer_callback_query(call.id)
        _show_tickets(call.message.chat.id, call.message.message_id)

    # ==================================================================
    # 25. CB_ADMIN_RESTART -> show confirmation
    # ==================================================================

    @bot.callback_query_handler(func=lambda c: c.data == CB_ADMIN_RESTART)
    def cb_admin_restart(call: types.CallbackQuery):
        user_id = call.from_user.id
        if not is_admin(user_id):
            bot.answer_callback_query(call.id, "Доступ запрещён.")
            return
        bot.answer_callback_query(call.id)
        markup = build_restart_confirm()
        _safe_edit(
            call.message.chat.id, call.message.message_id,
            "⚠️ Вы уверены, что хотите перезапустить бота?",
            reply_markup=markup,
        )

    # ==================================================================
    # 26. CB_ADMIN_RESTART_CONFIRM -> restart bot
    # ==================================================================

    @bot.callback_query_handler(func=lambda c: c.data == CB_ADMIN_RESTART_CONFIRM)
    def cb_admin_restart_confirm(call: types.CallbackQuery):
        user_id = call.from_user.id
        if not is_admin(user_id):
            bot.answer_callback_query(call.id, "Доступ запрещён.")
            return
        bot.answer_callback_query(call.id)
        bot.send_message(call.message.chat.id, "🔄 Перезапуск бота...")
        logger.info("Bot restart requested by admin %s", user_id)
        os.execv(sys.executable, [sys.executable] + sys.argv)

    # ==================================================================
    # 27. Callback "noop" -> answer empty
    # ==================================================================

    @bot.callback_query_handler(func=lambda c: c.data == "noop")
    def cb_noop(call: types.CallbackQuery):
        bot.answer_callback_query(call.id)

    # ==================================================================
    # Text handler: STATE_REPLYING_TICKET (text messages)
    # ==================================================================

    @bot.message_handler(func=lambda m: (
        m.text is not None
        and is_admin(m.from_user.id)
        and isinstance(ctx.get_user_state(m.from_user.id), tuple)
        and len(ctx.get_user_state(m.from_user.id)) == 2
        and ctx.get_user_state(m.from_user.id)[0] == STATE_REPLYING_TICKET
    ))
    def handle_ticket_reply_text(message: types.Message):
        user_id = message.from_user.id
        state = ctx.get_user_state(user_id)
        ticket_id = state[1]
        ticket = storage.get_ticket(ticket_id)
        if not ticket:
            bot.send_message(message.chat.id, "Обращение не найдено.")
            ctx.set_user_state(user_id, None)
            return
        storage.add_ticket_message(
            ticket_id, user_id, is_admin=True, text=message.text,
        )
        # Notify the user who created the ticket
        ticket_user_id = ticket[1]
        try:
            bot.send_message(
                ticket_user_id,
                f"💬 Ответ по обращению #{ticket_id}:\n\n{message.text}",
            )
        except Exception as exc:
            logger.warning("Failed to notify user %s about ticket reply: %s", ticket_user_id, exc)
        ctx.set_user_state(user_id, None)
        bot.send_message(message.chat.id, f"✅ Ответ отправлен по обращению #{ticket_id}.")

    # ==================================================================
    # Content handler: STATE_REPLYING_TICKET (photo)
    # ==================================================================

    @bot.message_handler(
        content_types=["photo"],
        func=lambda m: (
            is_admin(m.from_user.id)
            and isinstance(ctx.get_user_state(m.from_user.id), tuple)
            and len(ctx.get_user_state(m.from_user.id)) == 2
            and ctx.get_user_state(m.from_user.id)[0] == STATE_REPLYING_TICKET
        ),
    )
    def handle_ticket_reply_photo(message: types.Message):
        user_id = message.from_user.id
        state = ctx.get_user_state(user_id)
        ticket_id = state[1]
        ticket = storage.get_ticket(ticket_id)
        if not ticket:
            bot.send_message(message.chat.id, "Обращение не найдено.")
            ctx.set_user_state(user_id, None)
            return
        file_id = message.photo[-1].file_id
        caption = message.caption or ""
        storage.add_ticket_message(
            ticket_id, user_id, is_admin=True,
            text=caption, file_id=file_id, file_type="photo",
        )
        ticket_user_id = ticket[1]
        try:
            bot.send_photo(
                ticket_user_id, file_id,
                caption=f"💬 Ответ по обращению #{ticket_id}:\n\n{caption}" if caption else f"💬 Ответ по обращению #{ticket_id}",
            )
        except Exception as exc:
            logger.warning("Failed to notify user %s about ticket reply: %s", ticket_user_id, exc)
        ctx.set_user_state(user_id, None)
        bot.send_message(message.chat.id, f"✅ Ответ отправлен по обращению #{ticket_id}.")

    # ==================================================================
    # Content handler: STATE_REPLYING_TICKET (video)
    # ==================================================================

    @bot.message_handler(
        content_types=["video"],
        func=lambda m: (
            is_admin(m.from_user.id)
            and isinstance(ctx.get_user_state(m.from_user.id), tuple)
            and len(ctx.get_user_state(m.from_user.id)) == 2
            and ctx.get_user_state(m.from_user.id)[0] == STATE_REPLYING_TICKET
        ),
    )
    def handle_ticket_reply_video(message: types.Message):
        user_id = message.from_user.id
        state = ctx.get_user_state(user_id)
        ticket_id = state[1]
        ticket = storage.get_ticket(ticket_id)
        if not ticket:
            bot.send_message(message.chat.id, "Обращение не найдено.")
            ctx.set_user_state(user_id, None)
            return
        file_id = message.video.file_id
        caption = message.caption or ""
        storage.add_ticket_message(
            ticket_id, user_id, is_admin=True,
            text=caption, file_id=file_id, file_type="video",
        )
        ticket_user_id = ticket[1]
        try:
            bot.send_video(
                ticket_user_id, file_id,
                caption=f"💬 Ответ по обращению #{ticket_id}:\n\n{caption}" if caption else f"💬 Ответ по обращению #{ticket_id}",
            )
        except Exception as exc:
            logger.warning("Failed to notify user %s about ticket reply: %s", ticket_user_id, exc)
        ctx.set_user_state(user_id, None)
        bot.send_message(message.chat.id, f"✅ Ответ отправлен по обращению #{ticket_id}.")
