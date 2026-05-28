"""Обработчики админ-панели: команда /admin, инлайн-меню, настройки, обращения, статистика."""

import logging
import math
import os
import sys

from telebot import types

from app.config import ADMIN_IDS, COOKIES_FILE, FREE_DOWNLOAD_LIMIT, FREE_DOWNLOAD_WINDOW_SECONDS, TELEGRAM_API_SERVER_URL, YOUTUBE_TEST_URL
from app.constants import (
    MENU_ADMIN,
    MENU_CHANNEL,
    MENU_HISTORY,
    MENU_REPORT,
    CB_ADMIN,
    CB_ADMIN_BROADCAST,
    CB_ADMIN_INCIDENTS,
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
    CB_ADMIN_LOGS,
    CB_ADMIN_RESTART,
    CB_ADMIN_RESTART_CONFIRM,
    CB_ADMIN_BACK,
    CB_ADMIN_SET_LIMIT,
    CB_ADMIN_SET_WINDOW,
    CB_ADMIN_CHANNELS,
    CB_ADMIN_CHANNEL_DEL,
    CB_BROADCAST_AFFECTED,
    CB_BROADCAST_ALL,
    CB_INCIDENT_LIST,
    CB_INCIDENT_STATUS,
    CB_INCIDENT_VIEW,
    CB_TICKET_VIEW,
    CB_TICKET_REPLY,
    CB_TICKET_CLOSE,
    CB_TICKET_LIST,
    EMOJI_STATS,
    EMOJI_BACK,
    EMOJI_DONE,
    INCIDENT_FIXED,
    INCIDENT_WONT_FIX,
    STATE_AWAITING_BROADCAST_AFFECTED,
    STATE_AWAITING_BROADCAST_ALL,
    STATE_AWAITING_LIMIT,
    STATE_AWAITING_WINDOW,
    STATE_AWAITING_CHANNEL_ID,
    STATE_AWAITING_LOG_LINES,
    STATE_REPLYING_TICKET,
)
from app.keyboards import (
    build_admin_menu,
    build_admin_back,
    build_admin_incidents_list,
    build_admin_stats_submenu,
    build_admin_users_page,
    build_admin_settings,
    build_admin_channels,
    build_admin_tickets,
    build_broadcast_menu,
    build_incident_actions,
    build_ticket_actions,
    build_restart_confirm,
    incident_status_label,
)
from app.logger import get_log_file_path
from app.utils import is_admin, format_bytes
from app.version import BOT_VERSION, BOT_VERSION_DATE, BOT_VERSION_CHANGELOG

logger = logging.getLogger(__name__)

USERS_PER_PAGE = 10


def register_admin_handlers(ctx) -> None:
    """Регистрирует все обработчики админ-панели."""
    bot = ctx.bot
    storage = ctx.storage

    # ------------------------------------------------------------------
    # Набор кнопок reply-меню: если пользователь нажимает кнопку или
    # отправляет URL / команду — сбрасываем состояние ожидания ввода.
    # ------------------------------------------------------------------
    _MENU_BUTTONS = {MENU_ADMIN, MENU_CHANNEL, MENU_HISTORY, MENU_REPORT}

    def _is_escape_text(text: str) -> bool:
        """Текст не является вводом для текущего состояния ожидания."""
        t = text.strip()
        return t.startswith("/") or t.startswith("http") or t in _MENU_BUTTONS

    def _check_state_input(m: types.Message, expected_state) -> bool:
        """Проверяет, что сообщение предназначено для обработчика состояния.

        Если текст является URL, командой или кнопкой меню —
        сбрасывает состояние и возвращает False, чтобы сообщение
        попало в нужный обработчик.
        """
        if m.text is None or not is_admin(m.from_user.id):
            return False
        state = ctx.get_user_state(m.from_user.id)
        if state != expected_state:
            return False
        if _is_escape_text(m.text):
            ctx.set_user_state(m.from_user.id, None)
            return False
        return True

    def _check_state_input_tuple(m: types.Message, expected_first) -> bool:
        """Аналог _check_state_input для состояний-кортежей (STATE_REPLYING_TICKET)."""
        if m.text is None or not is_admin(m.from_user.id):
            return False
        state = ctx.get_user_state(m.from_user.id)
        if not (isinstance(state, tuple) and len(state) == 2 and state[0] == expected_first):
            return False
        if _is_escape_text(m.text):
            ctx.set_user_state(m.from_user.id, None)
            return False
        return True

    def _check_state_input_broadcast(m: types.Message) -> bool:
        """Проверка для рассылки (два возможных состояния)."""
        if m.text is None or not is_admin(m.from_user.id):
            return False
        state = ctx.get_user_state(m.from_user.id)
        if state not in (STATE_AWAITING_BROADCAST_ALL, STATE_AWAITING_BROADCAST_AFFECTED):
            return False
        if _is_escape_text(m.text):
            ctx.set_user_state(m.from_user.id, None)
            return False
        return True

    # ------------------------------------------------------------------
    # Вспомогательная функция: безопасное редактирование или отправка нового сообщения
    # ------------------------------------------------------------------

    def _safe_edit(chat_id: int, message_id: int, text: str,
                   reply_markup=None, parse_mode=None):
        """Пытается отредактировать сообщение; при неудаче отправляет новое."""
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
    # Вспомогательная функция: отображение постраничного списка пользователей
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
    # Вспомогательная функция: отображение списка обращений
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
    # 1. Команда /admin
    # ==================================================================

    def _admin_menu_markup():
        """Строит меню админ-панели с актуальными счётчиками."""
        return build_admin_menu(
            open_tickets=storage.count_open_tickets(),
            open_incidents=storage.count_open_incidents(),
        )

    @bot.message_handler(commands=["admin"])
    def cmd_admin(message: types.Message):
        ctx.ensure_user(message.from_user)
        user_id = message.from_user.id
        if not is_admin(user_id):
            return
        ctx.clear_last_inline(user_id, message.chat.id)
        bot.send_message(
            message.chat.id,
            f"⚙️ Панель администратора\n\n"
            f"📦 Версия: <b>{BOT_VERSION}</b>\n"
            f"📅 Обновление: {BOT_VERSION_DATE}\n"
            f"📝 {BOT_VERSION_CHANGELOG}",
            reply_markup=_admin_menu_markup(),
            parse_mode="HTML",
        )

    # ==================================================================
    # 2. CB_ADMIN_BACK -> возврат в главное меню админки
    # ==================================================================

    @bot.callback_query_handler(func=lambda c: c.data == CB_ADMIN_BACK)
    def cb_admin_back(call: types.CallbackQuery):
        user_id = call.from_user.id
        if not is_admin(user_id):
            bot.answer_callback_query(call.id, "Доступ запрещён.")
            return
        bot.answer_callback_query(call.id)
        _safe_edit(
            call.message.chat.id, call.message.message_id,
            f"⚙️ Панель администратора\n\n"
            f"📦 Версия: <b>{BOT_VERSION}</b>\n"
            f"📅 Обновление: {BOT_VERSION_DATE}\n"
            f"📝 {BOT_VERSION_CHANGELOG}",
            reply_markup=_admin_menu_markup(),
            parse_mode="HTML",
        )

    # ==================================================================
    # 3. CB_ADMIN_STATS -> общая статистика
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
    # 4. CB_ADMIN_STATS_PLATFORM -> статистика по платформам
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
    # 5. CB_ADMIN_STATS_DAILY -> статистика по дням (7 дней)
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
    # 6. CB_ADMIN_STATS_USERS -> топ-10 пользователей
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
    # 7. CB_ADMIN_USERS -> постраничный список пользователей (страница 0)
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
    # 8. CB_ADMIN_USERS_PAGE|{page} -> навигация по страницам
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
    # 9. CB_ADMIN_USER_BLOCK|{user_id} -> блокировка пользователя
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
    # 10. CB_ADMIN_USER_UNBLOCK|{user_id} -> разблокировка пользователя
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
    # 11. CB_ADMIN_SETTINGS -> отображение настроек
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
    # 12. CB_ADMIN_SET_LIMIT -> запрос у админа нового числового лимита
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
    # 13. CB_ADMIN_SET_WINDOW -> запрос у админа периода в часах
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
    # 14. Обработчик текста: STATE_AWAITING_LIMIT
    # ==================================================================

    @bot.message_handler(func=lambda m: _check_state_input(m, STATE_AWAITING_LIMIT))
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
    # 15. Обработчик текста: STATE_AWAITING_WINDOW
    # ==================================================================

    @bot.message_handler(func=lambda m: _check_state_input(m, STATE_AWAITING_WINDOW))
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
    # 16. CB_ADMIN_CHANNELS -> отображение списка каналов
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
    # 17. CB_ADMIN_CHANNELS|add -> запрос у админа ID канала
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
    # 18. Обработчик текста: STATE_AWAITING_CHANNEL_ID
    # ==================================================================

    @bot.message_handler(func=lambda m: _check_state_input(m, STATE_AWAITING_CHANNEL_ID))
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
            logger.warning("Не удалось получить информацию о чате %s: %s", chat_id, exc)
            title = str(chat_id)
            invite_link = None
        storage.add_required_channel(chat_id, title, invite_link)
        ctx.set_user_state(user_id, None)
        bot.send_message(
            message.chat.id,
            f"✅ Канал добавлен: {title} ({chat_id})",
        )

    # ==================================================================
    # 19. CB_ADMIN_CHANNEL_DEL|{chat_id} -> удаление канала
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
    # 20. CB_ADMIN_TICKETS -> список открытых обращений
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
    # 21. CB_TICKET_VIEW|{ticket_id} -> просмотр переписки по обращению
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
        # Ограничение длины сообщения Telegram
        if len(text) > 4000:
            text = text[:4000] + "\n..."
        markup = build_ticket_actions(ticket_id)
        _safe_edit(call.message.chat.id, call.message.message_id, text, reply_markup=markup)

    # ==================================================================
    # 22. CB_TICKET_REPLY|{ticket_id} -> установка состояния для ответа
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
    # 23. CB_TICKET_CLOSE|{ticket_id} -> закрытие обращения
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
    # 24. CB_TICKET_LIST -> аналог CB_ADMIN_TICKETS
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
    # CB_ADMIN_LOGS -> запрос количества строк логов
    # ==================================================================

    @bot.callback_query_handler(func=lambda c: c.data == CB_ADMIN_LOGS)
    def cb_admin_logs(call: types.CallbackQuery):
        user_id = call.from_user.id
        if not is_admin(user_id):
            bot.answer_callback_query(call.id, "Доступ запрещён.")
            return
        bot.answer_callback_query(call.id)
        ctx.set_user_state(user_id, STATE_AWAITING_LOG_LINES)
        log_path = get_log_file_path()
        file_size = 0
        try:
            file_size = os.path.getsize(log_path)
        except OSError:
            pass
        size_text = format_bytes(file_size) if file_size else "файл отсутствует"
        bot.send_message(
            call.message.chat.id,
            f"📋 Файл логов: {size_text}\n\n"
            "Сколько последних строк прислать? Введите число (например, 100):",
        )

    # ==================================================================
    # Обработчик текста: STATE_AWAITING_LOG_LINES
    # ==================================================================

    @bot.message_handler(func=lambda m: _check_state_input(m, STATE_AWAITING_LOG_LINES))
    def handle_log_lines(message: types.Message):
        user_id = message.from_user.id
        text = message.text.strip()
        try:
            num_lines = int(text)
            if num_lines <= 0:
                raise ValueError
        except ValueError:
            bot.send_message(message.chat.id, "Введите положительное целое число.")
            return
        ctx.set_user_state(user_id, None)
        log_path = get_log_file_path()
        if not os.path.exists(log_path):
            bot.send_message(message.chat.id, "Файл логов не найден.")
            return
        try:
            with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                all_lines = f.readlines()
            tail = all_lines[-num_lines:]
            content = "".join(tail)
            if not content.strip():
                bot.send_message(message.chat.id, "Файл логов пуст.")
                return
            # Всегда отправляем как документ — удобнее для чтения
            import io
            doc = io.BytesIO(content.encode("utf-8"))
            doc.name = f"logs_last_{len(tail)}.txt"
            bot.send_document(
                message.chat.id, doc,
                caption=f"📋 Последние {len(tail)} строк логов",
            )
        except Exception:
            logger.exception("Ошибка при чтении логов")
            bot.send_message(message.chat.id, "Ошибка при чтении файла логов.")

    # ==================================================================
    # 25. CB_ADMIN_RESTART -> отображение подтверждения перезапуска
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
    # 26. CB_ADMIN_RESTART_CONFIRM -> перезапуск бота
    # ==================================================================

    @bot.callback_query_handler(func=lambda c: c.data == CB_ADMIN_RESTART_CONFIRM)
    def cb_admin_restart_confirm(call: types.CallbackQuery):
        user_id = call.from_user.id
        if not is_admin(user_id):
            bot.answer_callback_query(call.id, "Доступ запрещён.")
            return
        bot.answer_callback_query(call.id)
        bot.send_message(call.message.chat.id, "🔄 Перезапуск бота...")
        logger.info("Перезапуск бота запрошен администратором %s", user_id)
        # Бот запускается как пакет (`python -m app.main`) с абсолютными импортами,
        # поэтому повторный запуск sys.argv (путь к main.py как скрипту) ломает
        # `import app`. Перезапускаемся через -m с корнем проекта в PYTHONPATH.
        project_root = os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        )
        env = os.environ.copy()
        existing = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = (
            project_root + os.pathsep + existing if existing else project_root
        )
        os.chdir(project_root)
        os.execve(
            sys.executable,
            [sys.executable, "-m", "app.main"] + sys.argv[1:],
            env,
        )

    # ==================================================================
    # ИНЦИДЕНТЫ ВОСПРОИЗВЕДЕНИЯ ВИДЕО
    # ==================================================================

    def _show_incidents(chat_id: int, message_id: int):
        """Отображает список открытых инцидентов."""
        incidents = storage.list_video_incidents()
        users_map: dict[int, str] = {}
        for inc in incidents:
            uid = inc[1]
            if uid not in users_map:
                user_row = storage.get_user(uid)
                if user_row:
                    users_map[uid] = user_row[1] or user_row[2] or str(uid)
                else:
                    users_map[uid] = str(uid)
        markup = build_admin_incidents_list(incidents, users_map)
        count = len(incidents)
        _safe_edit(
            chat_id, message_id,
            f"🚧 Инциденты воспроизведения: {count}",
            reply_markup=markup,
        )

    @bot.callback_query_handler(func=lambda c: c.data == CB_ADMIN_INCIDENTS)
    def cb_admin_incidents(call: types.CallbackQuery):
        user_id = call.from_user.id
        if not is_admin(user_id):
            bot.answer_callback_query(call.id, "Доступ запрещён.")
            return
        bot.answer_callback_query(call.id)
        _show_incidents(call.message.chat.id, call.message.message_id)

    @bot.callback_query_handler(func=lambda c: c.data == CB_INCIDENT_LIST)
    def cb_incident_list(call: types.CallbackQuery):
        user_id = call.from_user.id
        if not is_admin(user_id):
            bot.answer_callback_query(call.id, "Доступ запрещён.")
            return
        bot.answer_callback_query(call.id)
        _show_incidents(call.message.chat.id, call.message.message_id)

    @bot.callback_query_handler(
        func=lambda c: c.data and c.data.startswith(f"{CB_INCIDENT_VIEW}|")
    )
    def cb_incident_view(call: types.CallbackQuery):
        user_id = call.from_user.id
        if not is_admin(user_id):
            bot.answer_callback_query(call.id, "Доступ запрещён.")
            return
        bot.answer_callback_query(call.id)
        try:
            incident_id = int(call.data.split("|", 1)[1])
        except (ValueError, IndexError):
            return
        inc = storage.get_video_incident(incident_id)
        if not inc:
            _safe_edit(
                call.message.chat.id, call.message.message_id,
                "Инцидент не найден.",
                reply_markup=build_admin_back(),
            )
            return
        _id, inc_uid, url, platform, fmt_id, codec, resolution, fsize, status, created, resolved = inc
        user_row = storage.get_user(inc_uid)
        display = f"@{user_row[1] or user_row[2]}" if user_row else str(inc_uid)
        status_lbl = incident_status_label(status)
        lines = [
            f"🚧 Инцидент #{incident_id}\n",
            f"Пользователь: {display}",
            f"Платформа: {platform or '?'}",
            f"Кодек: {codec or '?'}",
            f"Разрешение: {resolution or '?'}",
            f"Формат: {fmt_id or '?'}",
            f"Размер: {format_bytes(fsize) if fsize else '?'}",
            f"URL: {url or '?'}",
            f"\nСтатус: {status_lbl}",
            f"Создан: {created or '?'}",
        ]
        if resolved:
            lines.append(f"Решён: {resolved}")
        text = "\n".join(lines)
        if len(text) > 4000:
            text = text[:4000] + "\n..."
        markup = build_incident_actions(incident_id, status)
        _safe_edit(call.message.chat.id, call.message.message_id, text, reply_markup=markup)

    @bot.callback_query_handler(
        func=lambda c: c.data and c.data.startswith(f"{CB_INCIDENT_STATUS}|")
    )
    def cb_incident_status(call: types.CallbackQuery):
        user_id = call.from_user.id
        if not is_admin(user_id):
            bot.answer_callback_query(call.id, "Доступ запрещён.")
            return
        try:
            parts = call.data.split("|")
            incident_id = int(parts[1])
            new_status = parts[2]
        except (ValueError, IndexError):
            bot.answer_callback_query(call.id, "Ошибка.")
            return
        inc = storage.get_video_incident(incident_id)
        if not inc:
            bot.answer_callback_query(call.id, "Инцидент не найден.")
            return
        storage.set_incident_status(incident_id, new_status)
        status_lbl = incident_status_label(new_status)
        bot.answer_callback_query(call.id, f"Статус: {status_lbl}")

        # Уведомляем пользователя при смене на «исправлено» или «не будет исправлено»
        inc_uid = inc[1]
        if new_status == INCIDENT_FIXED:
            try:
                bot.send_message(
                    inc_uid,
                    f"✅ Мы исправили проблему с воспроизведением видео "
                    f"(обращение #{incident_id}).\n\n"
                    "Попробуйте скачать видео заново — теперь должно работать!",
                )
            except Exception as exc:
                logger.warning("Не удалось уведомить пользователя %s: %s", inc_uid, exc)
        elif new_status == INCIDENT_WONT_FIX:
            try:
                bot.send_message(
                    inc_uid,
                    f"ℹ️ По вашему обращению #{incident_id}:\n\n"
                    "К сожалению, данная проблема вызвана ограничениями платформы "
                    "и не может быть исправлена на нашей стороне.\n"
                    "Спасибо за обратную связь!",
                )
            except Exception as exc:
                logger.warning("Не удалось уведомить пользователя %s: %s", inc_uid, exc)

        # Обновляем отображение инцидента
        inc = storage.get_video_incident(incident_id)
        if inc:
            _id, inc_uid, url, platform, fmt_id, codec, resolution, fsize, status, created, resolved = inc
            user_row = storage.get_user(inc_uid)
            display = f"@{user_row[1] or user_row[2]}" if user_row else str(inc_uid)
            status_lbl = incident_status_label(status)
            lines = [
                f"🚧 Инцидент #{incident_id}\n",
                f"Пользователь: {display}",
                f"Платформа: {platform or '?'}",
                f"Кодек: {codec or '?'}",
                f"Разрешение: {resolution or '?'}",
                f"Формат: {fmt_id or '?'}",
                f"Размер: {format_bytes(fsize) if fsize else '?'}",
                f"URL: {url or '?'}",
                f"\nСтатус: {status_lbl}",
                f"Создан: {created or '?'}",
            ]
            if resolved:
                lines.append(f"Решён: {resolved}")
            text = "\n".join(lines)
            markup = build_incident_actions(incident_id, status)
            _safe_edit(call.message.chat.id, call.message.message_id, text, reply_markup=markup)

    # ==================================================================
    # МАССОВАЯ РАССЫЛКА
    # ==================================================================

    @bot.callback_query_handler(func=lambda c: c.data == CB_ADMIN_BROADCAST)
    def cb_admin_broadcast(call: types.CallbackQuery):
        user_id = call.from_user.id
        if not is_admin(user_id):
            bot.answer_callback_query(call.id, "Доступ запрещён.")
            return
        bot.answer_callback_query(call.id)
        total = len(storage.list_all_user_ids())
        affected = len(storage.list_affected_user_ids())
        markup = build_broadcast_menu(total, affected)
        _safe_edit(
            call.message.chat.id, call.message.message_id,
            "📢 Рассылка сообщений\n\nВыберите аудиторию:",
            reply_markup=markup,
        )

    @bot.callback_query_handler(func=lambda c: c.data == CB_BROADCAST_ALL)
    def cb_broadcast_all(call: types.CallbackQuery):
        user_id = call.from_user.id
        if not is_admin(user_id):
            bot.answer_callback_query(call.id, "Доступ запрещён.")
            return
        bot.answer_callback_query(call.id)
        ctx.set_user_state(user_id, STATE_AWAITING_BROADCAST_ALL)
        bot.send_message(
            call.message.chat.id,
            "📢 Введите текст рассылки для ВСЕХ пользователей.\n\n"
            "Отправьте /cancel для отмены.",
        )

    @bot.callback_query_handler(func=lambda c: c.data == CB_BROADCAST_AFFECTED)
    def cb_broadcast_affected(call: types.CallbackQuery):
        user_id = call.from_user.id
        if not is_admin(user_id):
            bot.answer_callback_query(call.id, "Доступ запрещён.")
            return
        bot.answer_callback_query(call.id)
        affected = len(storage.list_affected_user_ids())
        if affected == 0:
            bot.send_message(
                call.message.chat.id,
                "Нет пользователей с открытыми обращениями или инцидентами.",
            )
            return
        ctx.set_user_state(user_id, STATE_AWAITING_BROADCAST_AFFECTED)
        bot.send_message(
            call.message.chat.id,
            f"🎯 Введите текст рассылки для затронутых пользователей ({affected} чел.).\n\n"
            "Отправьте /cancel для отмены.",
        )

    @bot.message_handler(func=_check_state_input_broadcast)
    def handle_broadcast_text(message: types.Message):
        user_id = message.from_user.id
        state = ctx.get_user_state(user_id)
        text = message.text.strip()

        if text == "/cancel":
            ctx.set_user_state(user_id, None)
            bot.send_message(message.chat.id, "Рассылка отменена.")
            return

        if state == STATE_AWAITING_BROADCAST_ALL:
            user_ids = storage.list_all_user_ids()
            audience = "всем пользователям"
        else:
            user_ids = storage.list_affected_user_ids()
            audience = "затронутым пользователям"

        ctx.set_user_state(user_id, None)

        if not user_ids:
            bot.send_message(message.chat.id, "Список получателей пуст.")
            return

        sent = 0
        failed = 0
        for uid in user_ids:
            try:
                bot.send_message(uid, f"📢 {text}")
                sent += 1
            except Exception as exc:
                logger.warning("Не удалось отправить рассылку пользователю %s: %s", uid, exc)
                failed += 1

        bot.send_message(
            message.chat.id,
            f"✅ Рассылка {audience} завершена.\n"
            f"Доставлено: {sent}\nОшибок: {failed}",
        )

    # ==================================================================
    # 27. Обратный вызов "noop" -> пустой ответ
    # ==================================================================

    @bot.callback_query_handler(func=lambda c: c.data == "noop")
    def cb_noop(call: types.CallbackQuery):
        bot.answer_callback_query(call.id)

    # ==================================================================
    # Обработчик текста: STATE_REPLYING_TICKET (текстовые сообщения)
    # ==================================================================

    @bot.message_handler(func=lambda m: _check_state_input_tuple(m, STATE_REPLYING_TICKET))
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
        # Уведомление пользователя, создавшего обращение
        ticket_user_id = ticket[1]
        try:
            bot.send_message(
                ticket_user_id,
                f"💬 Ответ по обращению #{ticket_id}:\n\n{message.text}",
            )
        except Exception as exc:
            logger.warning("Не удалось уведомить пользователя %s об ответе на обращение: %s", ticket_user_id, exc)
        ctx.set_user_state(user_id, None)
        bot.send_message(message.chat.id, f"✅ Ответ отправлен по обращению #{ticket_id}.")

    # ==================================================================
    # Обработчик контента: STATE_REPLYING_TICKET (фото)
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
            logger.warning("Не удалось уведомить пользователя %s об ответе на обращение: %s", ticket_user_id, exc)
        ctx.set_user_state(user_id, None)
        bot.send_message(message.chat.id, f"✅ Ответ отправлен по обращению #{ticket_id}.")

    # ==================================================================
    # Обработчик контента: STATE_REPLYING_TICKET (видео)
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
            logger.warning("Не удалось уведомить пользователя %s об ответе на обращение: %s", ticket_user_id, exc)
        ctx.set_user_state(user_id, None)
        bot.send_message(message.chat.id, f"✅ Ответ отправлен по обращению #{ticket_id}.")

    # ==================================================================
    # Приём файла cookies от админа (документ .txt)
    # ==================================================================

    def _is_cookies_document(m: types.Message) -> bool:
        """Проверяет, является ли документ файлом cookies от админа."""
        if not is_admin(m.from_user.id):
            return False
        if m.document is None:
            return False
        if ctx.get_user_state(m.from_user.id) is not None:
            return False
        name = (m.document.file_name or "").lower()
        caption = (m.caption or "").strip().lower()
        if not name.endswith(".txt"):
            return False
        return "cookie" in name or caption == "cookies"

    @bot.message_handler(
        content_types=["document"],
        func=_is_cookies_document,
    )
    def handle_cookies_upload(message: types.Message):
        user_id = message.from_user.id
        try:
            file_info = bot.get_file(message.document.file_id)
            file_path = file_info.file_path
            # В режиме локального Bot API Server get_file возвращает абсолютный
            # путь к файлу на диске сервера — скачивание по HTTP даёт 404,
            # поэтому читаем файл напрямую с диска.
            if TELEGRAM_API_SERVER_URL and os.path.isabs(file_path) and os.path.exists(file_path):
                with open(file_path, "rb") as f:
                    downloaded = f.read()
            else:
                downloaded = bot.download_file(file_path)
        except Exception as exc:
            logger.exception("Не удалось скачать файл cookies от админа %s", user_id)
            bot.send_message(message.chat.id, f"Не удалось скачать файл: {exc}")
            return
        # Сохраняем файл
        cookies_path = COOKIES_FILE or "cookies.txt"
        try:
            with open(cookies_path, "wb") as f:
                f.write(downloaded)
        except Exception as exc:
            logger.exception("Не удалось сохранить cookies в %s", cookies_path)
            bot.send_message(message.chat.id, f"Не удалось сохранить файл: {exc}")
            return
        # Перезагружаем cookies в downloader
        try:
            result = ctx.downloader.reload_cookies()
            if not result:
                bot.send_message(
                    message.chat.id,
                    f"Файл сохранён ({cookies_path}), но cookies не удалось загрузить. "
                    "Проверьте формат файла (Netscape HTTP Cookie File).",
                )
                return
        except Exception as exc:
            logger.exception("Ошибка при перезагрузке cookies")
            bot.send_message(message.chat.id, f"Файл сохранён, но ошибка при загрузке: {exc}")
            return

        # Проверяем работоспособность cookies
        bot.send_message(message.chat.id, "🔍 Проверяю cookies...")
        cookies_ok = False
        try:
            ctx.downloader.get_info(YOUTUBE_TEST_URL)
            cookies_ok = True
        except Exception as check_exc:
            if "sign in to confirm" in str(check_exc).lower():
                bot.send_message(
                    message.chat.id,
                    f"❌ Cookies НЕ рабочие — YouTube по-прежнему требует авторизацию.\n\n"
                    f"Файл: {cookies_path}\n"
                    f"Размер: {len(downloaded)} байт\n"
                    "Попробуйте загрузить другой файл cookies.",
                )
                return
            # Другая ошибка (сеть и т.д.) — cookies могут быть ОК
            logger.warning("Проверка cookies: ошибка (не auth): %s", check_exc)
            cookies_ok = True  # не блокируем, ошибка не связана с cookies

        # Cookies рабочие — обрабатываем отложенные загрузки всех платформ
        # (cookies-файл общий, может содержать cookies для YouTube, Instagram и др.)
        pending = storage.list_pending_cookie_downloads()
        pending_count = len(pending)
        status_text = (
            f"✅ Cookies рабочие!\n\n"
            f"Файл: {cookies_path}\n"
            f"Размер: {len(downloaded)} байт\n"
            f"Cookiefile: {result}"
        )
        if pending_count > 0:
            status_text += f"\n\n📋 Отложенных загрузок: {pending_count} — начинаю обработку..."
        bot.send_message(message.chat.id, status_text)

        # Обрабатываем отложенные загрузки
        if pending_count > 0:
            _process_pending_cookie_downloads(message.chat.id, pending)

    def _process_pending_cookie_downloads(
        admin_chat_id: int,
        pending: list[tuple],
    ) -> None:
        """Обрабатывает отложенные загрузки после обновления cookies."""
        import threading

        def _process() -> None:
            sent = 0
            failed = 0
            for row in pending:
                pid, uid, cid, url, platform, created_at = row
                try:
                    bot.send_message(
                        cid,
                        f"✅ Проблема с {platform} устранена! Начинаю загрузку вашего ролика...",
                    )
                    if hasattr(ctx, "queue_download"):
                        ctx.queue_download(
                            uid, cid, url,
                            selected_format=None,
                            title="Отложенная загрузка",
                            audio_only=False,
                        )
                    sent += 1
                except Exception as exc:
                    logger.warning(
                        "Не удалось обработать отложенную загрузку %d для user=%d: %s",
                        pid, uid, exc,
                    )
                    failed += 1
                storage.delete_pending_cookie_download(pid)

            bot.send_message(
                admin_chat_id,
                f"📋 Отложенные загрузки обработаны.\n"
                f"Запущено: {sent}\nОшибок: {failed}",
            )

        threading.Thread(target=_process, daemon=True).start()
