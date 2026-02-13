"""Telegram keyboard builders with deduplicated quality button logic."""

import logging

from telebot import types

from app.constants import (
    CB_BACK,
    CB_DOWNLOAD,
    CB_SUBSCRIBE,
    CB_SUBMENU,
    CB_UNSUB,
    EMOJI_AUDIO,
    EMOJI_BACK,
    EMOJI_BEST,
    EMOJI_STAR,
    EMOJI_UNSUB,
    EMOJI_VIDEO,
    FORMAT_AUDIO,
    FORMAT_BEST,
    MENU_DOWNLOAD,
    MENU_HELP,
    MENU_SUBSCRIPTIONS,
    TELEGRAM_CALLBACK_DATA_MAX_BYTES,
    TELEGRAM_MAX_BUTTONS_PER_KEYBOARD,
)
from app.downloader import FormatOption


def _safe_callback_data(data: str) -> str:
    """Ensure callback_data fits within Telegram's 64-byte limit."""
    encoded = data.encode("utf-8")
    if len(encoded) <= TELEGRAM_CALLBACK_DATA_MAX_BYTES:
        return data
    truncated = encoded[:TELEGRAM_CALLBACK_DATA_MAX_BYTES].decode("utf-8", errors="ignore")
    logging.warning("Callback data truncated: %r -> %r", data, truncated)
    return truncated


def _build_quality_buttons(
    prefix: str,
    token: str,
    options: list[FormatOption],
    option_emoji: str,
    value_getter=None,
) -> list[types.InlineKeyboardButton]:
    """Build quality option buttons shared between download and subscription menus.

    Args:
        prefix: Callback data prefix (e.g. CB_DOWNLOAD or CB_SUBSCRIBE).
        token: Request token.
        options: List of format options.
        option_emoji: Emoji prefix for each option button.
        value_getter: Function to get callback value from option. Defaults to format_id.
    """
    buttons = []
    for option in options[:TELEGRAM_MAX_BUTTONS_PER_KEYBOARD - 3]:
        value = value_getter(option) if value_getter else option.format_id
        cb = _safe_callback_data(f"{prefix}|{token}|{value}")
        buttons.append(
            types.InlineKeyboardButton(
                text=f"{option_emoji} {option.label}",
                callback_data=cb,
            )
        )
    # Best quality
    buttons.append(
        types.InlineKeyboardButton(
            text=f"{EMOJI_BEST} Максимальное качество",
            callback_data=_safe_callback_data(f"{prefix}|{token}|{FORMAT_BEST}"),
        )
    )
    # Audio only
    buttons.append(
        types.InlineKeyboardButton(
            text=f"{EMOJI_AUDIO} Только звук",
            callback_data=_safe_callback_data(f"{prefix}|{token}|{FORMAT_AUDIO}"),
        )
    )
    return buttons


def build_format_keyboard(token: str, options: list[FormatOption]) -> types.InlineKeyboardMarkup:
    markup = types.InlineKeyboardMarkup()
    buttons = _build_quality_buttons(CB_DOWNLOAD, token, options, EMOJI_VIDEO)
    for btn in buttons:
        markup.add(btn)
    markup.add(
        types.InlineKeyboardButton(
            text=f"{EMOJI_STAR} Подписка на канал (уведомления)",
            callback_data=_safe_callback_data(f"{CB_SUBMENU}|{token}"),
        )
    )
    return markup


def build_subscription_menu(
    token: str, options: list[FormatOption]
) -> types.InlineKeyboardMarkup:
    markup = types.InlineKeyboardMarkup()
    buttons = _build_quality_buttons(
        CB_SUBSCRIBE, token, options, EMOJI_STAR,
        value_getter=lambda opt: opt.label,
    )
    for btn in buttons:
        markup.add(btn)
    markup.add(
        types.InlineKeyboardButton(
            text=f"{EMOJI_BACK} Назад к скачиванию",
            callback_data=_safe_callback_data(f"{CB_BACK}|{token}"),
        )
    )
    return markup


def build_subscription_keyboard(token: str) -> types.InlineKeyboardMarkup:
    markup = types.InlineKeyboardMarkup()
    markup.add(
        types.InlineKeyboardButton(
            text=f"{EMOJI_UNSUB} Отписаться",
            callback_data=_safe_callback_data(f"{CB_UNSUB}|{token}"),
        )
    )
    return markup


def build_main_menu() -> types.ReplyKeyboardMarkup:
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.row(MENU_DOWNLOAD, MENU_SUBSCRIPTIONS)
    markup.row(MENU_HELP)
    return markup
