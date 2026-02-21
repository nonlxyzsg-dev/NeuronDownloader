"""Построение Telegram-клавиатур (инлайн и reply)."""

import logging

from telebot import types

import math

from app.constants import (
    CB_ADMIN_BACK,
    CB_ADMIN_BROADCAST,
    CB_ADMIN_CHANNELS,
    CB_ADMIN_CHANNEL_DEL,
    CB_ADMIN_HIST_ALL,
    CB_ADMIN_HIST_PLAT_VIEW,
    CB_ADMIN_HIST_PLATFORMS,
    CB_ADMIN_HIST_SEND,
    CB_ADMIN_HIST_USER_VIEW,
    CB_ADMIN_HIST_USERS,
    CB_ADMIN_HISTORY,
    CB_ADMIN_INCIDENTS,
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
    CB_BROADCAST_AFFECTED,
    CB_BROADCAST_ALL,
    CB_CACHED_SEND,
    CB_DEVICE_ANDROID,
    CB_DEVICE_INLINE,
    CB_DEVICE_IPHONE,
    CB_DOWNLOAD,
    CB_INCIDENT_LIST,
    CB_INCIDENT_STATUS,
    CB_INCIDENT_VIEW,
    CB_MY_HIST_ALL,
    CB_MY_HIST_DATES,
    CB_MY_HIST_PLAT_VIEW,
    CB_MY_HIST_PLATFORMS,
    CB_MY_HIST_SEND,
    CB_MY_HISTORY,
    CB_REENCODE,
    CB_SPLIT_NO,
    CB_SPLIT_YES,
    CB_TICKET_CLOSE,
    CB_TICKET_LIST,
    CB_TICKET_REPLY,
    CB_TICKET_VIEW,
    CB_TOGGLE_REENCODE,
    CB_VIDEO_REPORT,
    EMOJI_AUDIO,
    EMOJI_BACK,
    EMOJI_BEST,
    EMOJI_CHANNEL,
    EMOJI_DONE,
    EMOJI_HISTORY,
    EMOJI_INCIDENT,
    EMOJI_LOGS,
    EMOJI_RESTART,
    EMOJI_SETTINGS,
    EMOJI_STATS,
    EMOJI_TICKETS,
    EMOJI_USERS,
    EMOJI_VIDEO,
    EMOJI_WARNING,
    EMOJI_ZAP,
    FORMAT_AUDIO,
    FORMAT_BEST,
    INCIDENT_FIXED,
    INCIDENT_IN_PROGRESS,
    INCIDENT_REPORTED,
    INCIDENT_WONT_FIX,
    MENU_ADMIN,
    MENU_CHANNEL,
    MENU_HISTORY,
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


def build_format_keyboard(
    token: str,
    options: list[FormatOption],
    cached_format_ids: set[str] | None = None,
    has_cached_best: bool = False,
    has_cached_audio: bool = False,
    reencode_on: bool = False,
    device_label: str = "",
) -> types.InlineKeyboardMarkup:
    """Строит клавиатуру выбора качества в несколько столбцов.

    cached_format_ids — множество format_id, для которых есть кэш (мгновенная отправка).
    """
    cached = cached_format_ids or set()
    markup = types.InlineKeyboardMarkup(row_width=3)

    # Кнопки качества по 3 в ряд
    quality_buttons = []
    for option in options[:TELEGRAM_MAX_BUTTONS_PER_KEYBOARD - 4]:
        cb = _safe_callback_data(f"{CB_DOWNLOAD}|{token}|{option.format_id}")
        is_cached = option.format_id in cached
        icon = f"{EMOJI_ZAP}" if is_cached else f"{EMOJI_VIDEO}"
        quality_buttons.append(
            types.InlineKeyboardButton(
                text=f"{icon} {option.label}",
                callback_data=cb,
            )
        )

    for i in range(0, len(quality_buttons), 3):
        row = quality_buttons[i:i + 3]
        markup.row(*row)

    # Максимальное качество + Только звук в одном ряду
    best_icon = f"{EMOJI_ZAP}" if has_cached_best else f"{EMOJI_BEST}"
    audio_icon = f"{EMOJI_ZAP}" if has_cached_audio else f"{EMOJI_AUDIO}"
    markup.row(
        types.InlineKeyboardButton(
            text=f"{best_icon} \u041c\u0430\u043a\u0441\u0438\u043c\u0430\u043b\u044c\u043d\u043e\u0435",
            callback_data=_safe_callback_data(f"{CB_DOWNLOAD}|{token}|{FORMAT_BEST}"),
        ),
        types.InlineKeyboardButton(
            text=f"{audio_icon} \u0422\u043e\u043b\u044c\u043a\u043e \u0437\u0432\u0443\u043a",
            callback_data=_safe_callback_data(f"{CB_DOWNLOAD}|{token}|{FORMAT_AUDIO}"),
        ),
    )

    return markup


def build_main_menu(is_admin: bool = False) -> types.ReplyKeyboardMarkup:
    """Строит главное reply-меню бота."""
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.row(MENU_HISTORY, MENU_CHANNEL)
    if is_admin:
        markup.row(MENU_REPORT, MENU_ADMIN)
    else:
        markup.row(MENU_REPORT)
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


def build_admin_menu(open_tickets: int = 0, open_incidents: int = 0) -> types.InlineKeyboardMarkup:
    """Строит главное меню админ-панели."""
    markup = types.InlineKeyboardMarkup(row_width=2)
    tickets_label = f"{EMOJI_TICKETS} Обращения"
    if open_tickets > 0:
        tickets_label += f" ({open_tickets})"
    incidents_label = f"{EMOJI_INCIDENT} Инциденты"
    if open_incidents > 0:
        incidents_label += f" ({open_incidents})"
    markup.row(
        types.InlineKeyboardButton(text=f"{EMOJI_STATS} Статистика", callback_data=CB_ADMIN_STATS),
        types.InlineKeyboardButton(text=f"{EMOJI_USERS} Пользователи", callback_data=CB_ADMIN_USERS),
    )
    markup.row(
        types.InlineKeyboardButton(text=f"{EMOJI_SETTINGS} Настройки", callback_data=CB_ADMIN_SETTINGS),
        types.InlineKeyboardButton(text=tickets_label, callback_data=CB_ADMIN_TICKETS),
    )
    markup.row(
        types.InlineKeyboardButton(text=incidents_label, callback_data=CB_ADMIN_INCIDENTS),
        types.InlineKeyboardButton(text=f"{EMOJI_HISTORY} История загрузок", callback_data=CB_ADMIN_HISTORY),
    )
    markup.row(
        types.InlineKeyboardButton(text=f"{EMOJI_LOGS} Логи", callback_data=CB_ADMIN_LOGS),
        types.InlineKeyboardButton(text="\U0001f4e2 Рассылка", callback_data=CB_ADMIN_BROADCAST),
    )
    markup.row(
        types.InlineKeyboardButton(text=f"{EMOJI_RESTART} Перезапуск бота", callback_data=CB_ADMIN_RESTART),
    )
    return markup


def build_broadcast_menu(total_users: int, affected_users: int) -> types.InlineKeyboardMarkup:
    """Строит меню выбора типа рассылки."""
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton(
        text=f"\U0001f4e2 Всем пользователям ({total_users})",
        callback_data=CB_BROADCAST_ALL,
    ))
    markup.add(types.InlineKeyboardButton(
        text=f"\U0001f3af Затронутым (тикеты/инциденты) ({affected_users})",
        callback_data=CB_BROADCAST_AFFECTED,
    ))
    markup.row(types.InlineKeyboardButton(
        text=f"{EMOJI_BACK} \u041d\u0430\u0437\u0430\u0434",
        callback_data=CB_ADMIN_BACK,
    ))
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
        types.InlineKeyboardButton(text="\u2705 Да, перезапустить", callback_data=CB_ADMIN_RESTART_CONFIRM),
        types.InlineKeyboardButton(text=f"{EMOJI_BACK} Отмена", callback_data=CB_ADMIN_BACK),
    )
    return markup


# --- Клавиатуры инцидентов воспроизведения видео ---


def build_device_selection() -> types.InlineKeyboardMarkup:
    """Строит клавиатуру выбора типа устройства."""
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.row(
        types.InlineKeyboardButton(
            text="\U0001f4f1 Android",
            callback_data=CB_DEVICE_ANDROID,
        ),
        types.InlineKeyboardButton(
            text="\U0001f34f iPhone / iPad",
            callback_data=CB_DEVICE_IPHONE,
        ),
    )
    return markup


def build_video_report_button(token: str) -> types.InlineKeyboardMarkup:
    """Строит кнопку «Не воспроизводится» под отправленным видео."""
    markup = types.InlineKeyboardMarkup()
    markup.row(
        types.InlineKeyboardButton(
            text=f"{EMOJI_WARNING} Не воспроизводится",
            callback_data=_safe_callback_data(f"{CB_VIDEO_REPORT}|{token}"),
        )
    )
    return markup


def build_video_buttons(
    report_token: str,
    reencode_token: str | None = None,
) -> types.InlineKeyboardMarkup:
    """Строит кнопки под видео: отчёт + опционально перекодирование."""
    markup = types.InlineKeyboardMarkup()
    if reencode_token:
        markup.row(
            types.InlineKeyboardButton(
                text="\U0001f504 Перекодировать (iPhone)",
                callback_data=_safe_callback_data(f"{CB_REENCODE}|{reencode_token}"),
            ),
        )
    markup.row(
        types.InlineKeyboardButton(
            text=f"{EMOJI_WARNING} Не воспроизводится",
            callback_data=_safe_callback_data(f"{CB_VIDEO_REPORT}|{report_token}"),
        ),
    )
    return markup


_INCIDENT_STATUS_LABELS = {
    INCIDENT_REPORTED: "Сообщено",
    INCIDENT_IN_PROGRESS: "В работе",
    INCIDENT_FIXED: "Исправлено",
    INCIDENT_WONT_FIX: "Не будет исправлено",
}


def incident_status_label(status: str) -> str:
    """Возвращает человекочитаемую метку статуса инцидента."""
    return _INCIDENT_STATUS_LABELS.get(status, status)


def build_admin_incidents_list(
    incidents: list[tuple],
    users_map: dict[int, str],
) -> types.InlineKeyboardMarkup:
    """Строит список инцидентов для админ-панели."""
    markup = types.InlineKeyboardMarkup()
    for inc in incidents[:20]:
        inc_id, user_id, _url, platform, _fmt, codec, _res, _size, status, created_at, _resolved = inc
        username = users_map.get(user_id, str(user_id))
        date_part = created_at[:10] if created_at else ""
        status_lbl = incident_status_label(status)
        plat = platform or "?"
        cod = codec or "?"
        label = f"#{inc_id} {plat}/{cod} @{username} [{status_lbl}] ({date_part})"
        # Обрезаем для отображения
        if len(label) > 60:
            label = label[:57] + "..."
        markup.add(types.InlineKeyboardButton(
            text=label,
            callback_data=_safe_callback_data(f"{CB_INCIDENT_VIEW}|{inc_id}"),
        ))
    markup.row(types.InlineKeyboardButton(text=f"{EMOJI_BACK} Назад", callback_data=CB_ADMIN_BACK))
    return markup


def build_incident_actions(incident_id: int, current_status: str) -> types.InlineKeyboardMarkup:
    """Строит кнопки смены статуса для инцидента."""
    markup = types.InlineKeyboardMarkup(row_width=2)
    transitions = []
    if current_status == INCIDENT_REPORTED:
        transitions = [
            ("В работу", INCIDENT_IN_PROGRESS),
            ("Исправлено", INCIDENT_FIXED),
            ("Не исправлять", INCIDENT_WONT_FIX),
        ]
    elif current_status == INCIDENT_IN_PROGRESS:
        transitions = [
            ("Исправлено", INCIDENT_FIXED),
            ("Не исправлять", INCIDENT_WONT_FIX),
        ]
    buttons = []
    for label, new_status in transitions:
        buttons.append(types.InlineKeyboardButton(
            text=label,
            callback_data=_safe_callback_data(f"{CB_INCIDENT_STATUS}|{incident_id}|{new_status}"),
        ))
    if buttons:
        markup.row(*buttons)
    markup.row(types.InlineKeyboardButton(text=f"{EMOJI_BACK} К инцидентам", callback_data=CB_INCIDENT_LIST))
    return markup


# --- Клавиатуры истории загрузок ---

HISTORY_PAGE_SIZE = 8


def _truncate_title(title: str, max_len: int = 35) -> str:
    """Обрезает заголовок для кнопки."""
    if not title:
        return "Без названия"
    if len(title) <= max_len:
        return title
    return title[:max_len - 1] + "\u2026"


def _history_nav_row(
    page: int,
    total: int,
    cb_prefix: str,
    cb_suffix: str = "",
) -> list[types.InlineKeyboardButton]:
    """Строит ряд навигации: ← Стр. X/Y →.

    Формат callback_data:
    - без суффикса: {prefix}|{page}
    - с суффиксом: {prefix}|{suffix}|{page}
    """
    total_pages = max(1, math.ceil(total / HISTORY_PAGE_SIZE))
    base = f"{cb_prefix}|{cb_suffix}|" if cb_suffix else f"{cb_prefix}|"
    nav = []
    if page > 0:
        nav.append(types.InlineKeyboardButton(
            text="\u2b05\ufe0f",
            callback_data=_safe_callback_data(f"{base}{page - 1}"),
        ))
    nav.append(types.InlineKeyboardButton(
        text=f"{page + 1}/{total_pages}", callback_data="noop",
    ))
    if page < total_pages - 1:
        nav.append(types.InlineKeyboardButton(
            text="\u27a1\ufe0f",
            callback_data=_safe_callback_data(f"{base}{page + 1}"),
        ))
    return nav


# --- Пользовательская история ---


def build_my_history_menu(total_downloads: int) -> types.InlineKeyboardMarkup:
    """Главное меню истории пользователя."""
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton(
        text=f"\U0001f4cb Все загрузки ({total_downloads})",
        callback_data=f"{CB_MY_HIST_ALL}|0",
    ))
    markup.add(types.InlineKeyboardButton(
        text="\U0001f4f1 По площадкам",
        callback_data=CB_MY_HIST_PLATFORMS,
    ))
    return markup


def build_my_history_list(
    downloads: list[tuple],
    page: int,
    total: int,
    back_cb: str,
    send_prefix: str = CB_MY_HIST_SEND,
    page_prefix: str = CB_MY_HIST_ALL,
    page_suffix: str = "",
    show_dates: bool = True,
) -> types.InlineKeyboardMarkup:
    """Строит список загрузок с пагинацией.

    downloads: [(id, user_id, platform, status, created_at, url, title, telegram_file_id, audio_only), ...]
    """
    markup = types.InlineKeyboardMarkup()
    for dl in downloads:
        dl_id, _uid, platform, _status, created_at, _url, title, file_id, audio_only = dl
        icon = EMOJI_AUDIO if audio_only else EMOJI_VIDEO
        plat = (platform or "?")[:10]
        date_str = ""
        if show_dates and created_at:
            date_str = f" {created_at[5:10]}"
        display = _truncate_title(title, 28)
        label = f"{icon} {plat}{date_str} | {display}"
        if len(label) > 55:
            label = label[:52] + "\u2026"
        # Если нет file_id — кнопка неактивна
        if file_id:
            markup.add(types.InlineKeyboardButton(
                text=label,
                callback_data=_safe_callback_data(f"{send_prefix}|{dl_id}"),
            ))
        else:
            markup.add(types.InlineKeyboardButton(
                text=f"\u274c {label}",
                callback_data="noop",
            ))
    if total > HISTORY_PAGE_SIZE:
        nav = _history_nav_row(page, total, page_prefix, page_suffix)
        markup.row(*nav)
    markup.row(types.InlineKeyboardButton(
        text=f"{EMOJI_BACK} Назад", callback_data=back_cb,
    ))
    return markup


def build_my_history_platforms(
    platforms: list[tuple[str, int]],
) -> types.InlineKeyboardMarkup:
    """Список площадок с количеством загрузок."""
    markup = types.InlineKeyboardMarkup()
    for platform, count in platforms:
        markup.add(types.InlineKeyboardButton(
            text=f"\U0001f4f1 {platform} ({count})",
            callback_data=_safe_callback_data(f"{CB_MY_HIST_PLAT_VIEW}|{platform}|0"),
        ))
    markup.row(types.InlineKeyboardButton(
        text=f"{EMOJI_BACK} Назад", callback_data=CB_MY_HISTORY,
    ))
    return markup


# --- Админская история ---


def build_admin_history_menu(total_downloads: int) -> types.InlineKeyboardMarkup:
    """Главное меню истории загрузок в админке."""
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton(
        text=f"\U0001f4cb Все загрузки ({total_downloads})",
        callback_data=f"{CB_ADMIN_HIST_ALL}|0",
    ))
    markup.add(types.InlineKeyboardButton(
        text="\U0001f4f1 По площадкам",
        callback_data=CB_ADMIN_HIST_PLATFORMS,
    ))
    markup.add(types.InlineKeyboardButton(
        text=f"{EMOJI_USERS} По пользователям",
        callback_data=f"{CB_ADMIN_HIST_USERS}|0",
    ))
    markup.row(types.InlineKeyboardButton(
        text=f"{EMOJI_BACK} Назад", callback_data=CB_ADMIN_BACK,
    ))
    return markup


def build_admin_history_platforms(
    platforms: list[tuple[str, int]],
) -> types.InlineKeyboardMarkup:
    """Список площадок для админа."""
    markup = types.InlineKeyboardMarkup()
    for platform, count in platforms:
        markup.add(types.InlineKeyboardButton(
            text=f"\U0001f4f1 {platform} ({count})",
            callback_data=_safe_callback_data(f"{CB_ADMIN_HIST_PLAT_VIEW}|{platform}|0"),
        ))
    markup.row(types.InlineKeyboardButton(
        text=f"{EMOJI_BACK} Назад", callback_data=CB_ADMIN_HISTORY,
    ))
    return markup


def build_admin_history_users(
    users: list[tuple[int, str, str, int]],
    page: int,
    total_users: int,
) -> types.InlineKeyboardMarkup:
    """Список пользователей с количеством загрузок (для админа)."""
    markup = types.InlineKeyboardMarkup()
    for uid, username, first_name, count in users:
        display = username or first_name or str(uid)
        markup.add(types.InlineKeyboardButton(
            text=f"@{display} \u2014 {count} загрузок",
            callback_data=_safe_callback_data(f"{CB_ADMIN_HIST_USER_VIEW}|{uid}|0"),
        ))
    if total_users > HISTORY_PAGE_SIZE:
        total_pages = max(1, math.ceil(total_users / HISTORY_PAGE_SIZE))
        nav = []
        if page > 0:
            nav.append(types.InlineKeyboardButton(
                text="\u2b05\ufe0f",
                callback_data=f"{CB_ADMIN_HIST_USERS}|{page - 1}",
            ))
        nav.append(types.InlineKeyboardButton(
            text=f"{page + 1}/{total_pages}", callback_data="noop",
        ))
        if page < total_pages - 1:
            nav.append(types.InlineKeyboardButton(
                text="\u27a1\ufe0f",
                callback_data=f"{CB_ADMIN_HIST_USERS}|{page + 1}",
            ))
        markup.row(*nav)
    markup.row(types.InlineKeyboardButton(
        text=f"{EMOJI_BACK} Назад", callback_data=CB_ADMIN_HISTORY,
    ))
    return markup
