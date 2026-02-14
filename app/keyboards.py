"""Построение Telegram-клавиатур (инлайн и reply)."""

import logging

from telebot import types

from app.constants import (
    CB_ADMIN_BACK,
    CB_ADMIN_CHANNELS,
    CB_ADMIN_CHANNEL_DEL,
    CB_ADMIN_LOGS,
    CB_ADMIN_RESTART,
    CB_ADMIN_RESTART_CONFIRM,
    CB_ADMIN_SETTINGS,
    CB_ADMIN_SET_LIMIT,
    CB_ADMIN_SET_WINDOW,
    CB_ADMIN_STATS,
    CB_ADMIN_STATS_DAILY,
    CB_ADMIN_STATS_PLATFORM,
    CB_ADMIN_STATS_USERS,
    CB_ADMIN_TICKETS,
    CB_ADMIN_USERS,
    CB_ADMIN_USERS_PAGE,
    CB_ADMIN_USER_BLOCK,
    CB_ADMIN_USER_UNBLOCK,
    CB_DOWNLOAD,
    CB_SPLIT_NO,
    CB_SPLIT_YES,
    CB_TICKET_CLOSE,
    CB_TICKET_LIST,
    CB_TICKET_REPLY,
    CB_TICKET_VIEW,
    EMOJI_AUDIO,
    EMOJI_BACK,
    EMOJI_BEST,
    EMOJI_CHANNEL,
    EMOJI_LOGS,
    EMOJI_RESTART,
    EMOJI_SETTINGS,
    EMOJI_STATS,
    EMOJI_TICKETS,
    EMOJI_USERS,
    EMOJI_VIDEO,
    FORMAT_AUDIO,
    FORMAT_BEST,
    MENU_HELP,
    MENU_REPORT,
    TELEGRAM_CALLBACK_DATA_MAX_BYTES,
    TELEGRAM_MAX_BUTTONS_PER_KEYBOARD,
)
from app.downloader import FormatOption


def _safe_callback_data(data: str) -> str:
    """Обрезает callback_data до лимита Telegram в 64 байта."""
    encoded = data.encode("utf-8")
    if len(encoded) <= TELEGRAM_CALLBACK_DATA_MAX_BYTES:
        return data
    truncated = encoded[:TELEGRAM_CALLBACK_DATA_MAX_BYTES].decode("utf-8", errors="ignore")
    logging.warning("Callback data обрезано: %r -> %r", data, truncated)
    return truncated


def build_format_keyboard(token: str, options: list[FormatOption]) -> types.InlineKeyboardMarkup:
    """Строит клавиатуру выбора качества в несколько столбцов."""
    markup = types.InlineKeyboardMarkup(row_width=3)

    # Кнопки качества по 3 в ряд
    quality_buttons = []
    for option in options[:TELEGRAM_MAX_BUTTONS_PER_KEYBOARD - 2]:
        cb = _safe_callback_data(f"{CB_DOWNLOAD}|{token}|{option.format_id}")
        quality_buttons.append(
            types.InlineKeyboardButton(
                text=f"{EMOJI_VIDEO} {option.label}",
                callback_data=cb,
            )
        )

    for i in range(0, len(quality_buttons), 3):
        row = quality_buttons[i:i + 3]
        markup.row(*row)

    # Максимальное качество + Только звук в одном ряду
    markup.row(
        types.InlineKeyboardButton(
            text=f"{EMOJI_BEST} \u041c\u0430\u043a\u0441\u0438\u043c\u0430\u043b\u044c\u043d\u043e\u0435",
            callback_data=_safe_callback_data(f"{CB_DOWNLOAD}|{token}|{FORMAT_BEST}"),
        ),
        types.InlineKeyboardButton(
            text=f"{EMOJI_AUDIO} \u0422\u043e\u043b\u044c\u043a\u043e \u0437\u0432\u0443\u043a",
            callback_data=_safe_callback_data(f"{CB_DOWNLOAD}|{token}|{FORMAT_AUDIO}"),
        ),
    )

    return markup


def build_main_menu() -> types.ReplyKeyboardMarkup:
    """Строит главное reply-меню бота."""
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.row(MENU_HELP, MENU_REPORT)
    return markup


def build_channel_buttons(channels: list[tuple[int, str | None, str | None]]) -> types.InlineKeyboardMarkup | None:
    """Строит инлайн-кнопки со ссылками на обязательные каналы."""
    buttons = []
    for _chat_id, title, invite_link in channels:
        if not invite_link:
            continue
        label = title or "\u041f\u043e\u0434\u043f\u0438\u0441\u0430\u0442\u044c\u0441\u044f"
        buttons.append(
            types.InlineKeyboardButton(
                text=f"{EMOJI_CHANNEL} {label}",
                url=invite_link,
            )
        )
    if not buttons:
        return None
    markup = types.InlineKeyboardMarkup()
    for btn in buttons:
        markup.add(btn)
    return markup


def build_split_confirm_keyboard(token: str) -> types.InlineKeyboardMarkup:
    """Строит клавиатуру подтверждения разделения большого видео."""
    markup = types.InlineKeyboardMarkup()
    markup.row(
        types.InlineKeyboardButton(
            text="\u2705 \u0414\u0430, \u0440\u0430\u0437\u0434\u0435\u043b\u0438\u0442\u044c",
            callback_data=_safe_callback_data(f"{CB_SPLIT_YES}|{token}"),
        ),
        types.InlineKeyboardButton(
            text="\u274c \u041d\u0435\u0442",
            callback_data=_safe_callback_data(f"{CB_SPLIT_NO}|{token}"),
        ),
    )
    return markup


# --- Клавиатуры админ-панели ---


def build_admin_menu(open_tickets: int = 0) -> types.InlineKeyboardMarkup:
    """Строит главное меню админ-панели."""
    markup = types.InlineKeyboardMarkup(row_width=2)
    tickets_label = f"{EMOJI_TICKETS} \u041e\u0431\u0440\u0430\u0449\u0435\u043d\u0438\u044f"
    if open_tickets > 0:
        tickets_label += f" ({open_tickets})"
    markup.row(
        types.InlineKeyboardButton(text=f"{EMOJI_STATS} \u0421\u0442\u0430\u0442\u0438\u0441\u0442\u0438\u043a\u0430", callback_data=CB_ADMIN_STATS),
        types.InlineKeyboardButton(text=f"{EMOJI_USERS} \u041f\u043e\u043b\u044c\u0437\u043e\u0432\u0430\u0442\u0435\u043b\u0438", callback_data=CB_ADMIN_USERS),
    )
    markup.row(
        types.InlineKeyboardButton(text=f"{EMOJI_SETTINGS} \u041d\u0430\u0441\u0442\u0440\u043e\u0439\u043a\u0438", callback_data=CB_ADMIN_SETTINGS),
        types.InlineKeyboardButton(text=tickets_label, callback_data=CB_ADMIN_TICKETS),
    )
    markup.row(
        types.InlineKeyboardButton(text=f"{EMOJI_LOGS} \u041b\u043e\u0433\u0438", callback_data=CB_ADMIN_LOGS),
        types.InlineKeyboardButton(text=f"{EMOJI_RESTART} \u041f\u0435\u0440\u0435\u0437\u0430\u043f\u0443\u0441\u043a \u0431\u043e\u0442\u0430", callback_data=CB_ADMIN_RESTART),
    )
    return markup


def build_admin_back() -> types.InlineKeyboardMarkup:
    """Строит кнопку «Назад» для админ-панели."""
    markup = types.InlineKeyboardMarkup()
    markup.row(types.InlineKeyboardButton(text=f"{EMOJI_BACK} \u041d\u0430\u0437\u0430\u0434", callback_data=CB_ADMIN_BACK))
    return markup


def build_admin_stats_submenu() -> types.InlineKeyboardMarkup:
    """Строит подменю статистики."""
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.row(
        types.InlineKeyboardButton(text="\U0001f4f1 \u041f\u043e \u043f\u043b\u0430\u0442\u0444\u043e\u0440\u043c\u0430\u043c", callback_data=CB_ADMIN_STATS_PLATFORM),
        types.InlineKeyboardButton(text="\U0001f4c5 \u041f\u043e \u0434\u043d\u044f\u043c", callback_data=CB_ADMIN_STATS_DAILY),
    )
    markup.row(
        types.InlineKeyboardButton(text=f"{EMOJI_USERS} \u0422\u043e\u043f \u043f\u043e\u043b\u044c\u0437\u043e\u0432\u0430\u0442\u0435\u043b\u0435\u0439", callback_data=CB_ADMIN_STATS_USERS),
    )
    markup.row(types.InlineKeyboardButton(text=f"{EMOJI_BACK} \u041d\u0430\u0437\u0430\u0434", callback_data=CB_ADMIN_BACK))
    return markup


def build_admin_users_page(
    users: list[tuple[int, str, str, str, int]],
    page: int,
    total_pages: int,
    download_counts: dict[int, int],
) -> types.InlineKeyboardMarkup:
    """Строит страницу списка пользователей с пагинацией."""
    markup = types.InlineKeyboardMarkup()
    for uid, username, first_name, _last_name, blocked in users:
        display = username or first_name or str(uid)
        count = download_counts.get(uid, 0)
        status = "\U0001f534" if blocked else "\U0001f7e2"
        label = f"{status} @{display} \u2014 {count}"
        action = CB_ADMIN_USER_UNBLOCK if blocked else CB_ADMIN_USER_BLOCK
        markup.add(types.InlineKeyboardButton(
            text=label,
            callback_data=_safe_callback_data(f"{action}|{uid}"),
        ))
    nav = []
    if page > 0:
        nav.append(types.InlineKeyboardButton(text="\u2b05\ufe0f", callback_data=f"{CB_ADMIN_USERS_PAGE}|{page - 1}"))
    nav.append(types.InlineKeyboardButton(text=f"{page + 1}/{total_pages}", callback_data="noop"))
    if page < total_pages - 1:
        nav.append(types.InlineKeyboardButton(text="\u27a1\ufe0f", callback_data=f"{CB_ADMIN_USERS_PAGE}|{page + 1}"))
    if nav:
        markup.row(*nav)
    markup.row(types.InlineKeyboardButton(text=f"{EMOJI_BACK} \u041d\u0430\u0437\u0430\u0434", callback_data=CB_ADMIN_BACK))
    return markup


def build_admin_settings(
    free_limit: int,
    window_hours: int,
    channels_count: int,
) -> types.InlineKeyboardMarkup:
    """Строит клавиатуру настроек бота."""
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton(
        text=f"\U0001f522 \u041b\u0438\u043c\u0438\u0442: {free_limit} \u0432\u0438\u0434\u0435\u043e/\u043f\u0435\u0440\u0438\u043e\u0434",
        callback_data=CB_ADMIN_SET_LIMIT,
    ))
    markup.add(types.InlineKeyboardButton(
        text=f"\u23f0 \u041f\u0435\u0440\u0438\u043e\u0434: {window_hours} \u0447\u0430\u0441.",
        callback_data=CB_ADMIN_SET_WINDOW,
    ))
    markup.add(types.InlineKeyboardButton(
        text=f"{EMOJI_CHANNEL} \u041a\u0430\u043d\u0430\u043b\u044b ({channels_count})",
        callback_data=CB_ADMIN_CHANNELS,
    ))
    markup.row(types.InlineKeyboardButton(text=f"{EMOJI_BACK} \u041d\u0430\u0437\u0430\u0434", callback_data=CB_ADMIN_BACK))
    return markup


def build_admin_channels(channels: list[tuple[int, str | None, str | None]]) -> types.InlineKeyboardMarkup:
    """Строит список обязательных каналов с кнопками удаления."""
    markup = types.InlineKeyboardMarkup()
    for chat_id, title, invite_link in channels:
        label = title or str(chat_id)
        if invite_link:
            label += " \u2705"
        markup.add(types.InlineKeyboardButton(
            text=f"\u274c {label}",
            callback_data=_safe_callback_data(f"{CB_ADMIN_CHANNEL_DEL}|{chat_id}"),
        ))
    markup.add(types.InlineKeyboardButton(
        text="\u2795 \u0414\u043e\u0431\u0430\u0432\u0438\u0442\u044c \u043a\u0430\u043d\u0430\u043b",
        callback_data=f"{CB_ADMIN_CHANNELS}|add",
    ))
    markup.row(types.InlineKeyboardButton(text=f"{EMOJI_BACK} \u041d\u0430\u0437\u0430\u0434", callback_data=CB_ADMIN_SETTINGS))
    return markup


def build_admin_tickets(
    tickets: list[tuple[int, int, str, str]],
    users_map: dict[int, str],
) -> types.InlineKeyboardMarkup:
    """Строит список открытых обращений."""
    markup = types.InlineKeyboardMarkup()
    for ticket_id, user_id, _status, created_at in tickets[:20]:
        username = users_map.get(user_id, str(user_id))
        date_part = created_at[:10] if created_at else ""
        markup.add(types.InlineKeyboardButton(
            text=f"#{ticket_id} @{username} ({date_part})",
            callback_data=_safe_callback_data(f"{CB_TICKET_VIEW}|{ticket_id}"),
        ))
    markup.row(types.InlineKeyboardButton(text=f"{EMOJI_BACK} \u041d\u0430\u0437\u0430\u0434", callback_data=CB_ADMIN_BACK))
    return markup


def build_ticket_actions(ticket_id: int) -> types.InlineKeyboardMarkup:
    """Строит кнопки действий для обращения (ответить/закрыть)."""
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.row(
        types.InlineKeyboardButton(text="\u2709\ufe0f \u041e\u0442\u0432\u0435\u0442\u0438\u0442\u044c", callback_data=_safe_callback_data(f"{CB_TICKET_REPLY}|{ticket_id}")),
        types.InlineKeyboardButton(text="\u2705 \u0417\u0430\u043a\u0440\u044b\u0442\u044c", callback_data=_safe_callback_data(f"{CB_TICKET_CLOSE}|{ticket_id}")),
    )
    markup.row(types.InlineKeyboardButton(text=f"{EMOJI_BACK} \u041a \u043e\u0431\u0440\u0430\u0449\u0435\u043d\u0438\u044f\u043c", callback_data=CB_TICKET_LIST))
    return markup


def build_restart_confirm() -> types.InlineKeyboardMarkup:
    """Строит клавиатуру подтверждения перезапуска бота."""
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.row(
        types.InlineKeyboardButton(text="\u2705 \u0414\u0430, \u043f\u0435\u0440\u0435\u0437\u0430\u043f\u0443\u0441\u0442\u0438\u0442\u044c", callback_data=CB_ADMIN_RESTART_CONFIRM),
        types.InlineKeyboardButton(text=f"{EMOJI_BACK} \u041e\u0442\u043c\u0435\u043d\u0430", callback_data=CB_ADMIN_BACK),
    )
    return markup
