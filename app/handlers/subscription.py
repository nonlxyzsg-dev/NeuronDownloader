"""Subscription flow handlers: subscribe, unsubscribe, list, delete."""

from telebot import types

from app.constants import (
    CB_BACK,
    CB_SUBDEL,
    CB_SUBDEL_ALL,
    CB_SUBSCRIBE,
    CB_SUBMENU,
    CB_UNSUB,
    EMOJI_DELETE,
    EMOJI_UNSUB,
)
from app.keyboards import (
    build_format_keyboard,
    build_subscription_keyboard,
    build_subscription_menu,
)


def _list_subscriptions(ctx, message: types.Message) -> None:
    """Shared logic for listing subscriptions (used from command and menu button)."""
    bot = ctx.bot
    storage = ctx.storage
    ctx.ensure_user(message.from_user)
    if not ctx.check_access(message.from_user.id, message.chat.id):
        return
    ctx.clear_last_inline(message.from_user.id, message.chat.id)
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
                text=f"{EMOJI_DELETE} Удалить {resolution or 'max'}",
                callback_data=f"{CB_SUBDEL}|{token}",
            )
        )
    markup.add(
        types.InlineKeyboardButton(
            text=f"{EMOJI_UNSUB} Отключить все",
            callback_data=CB_SUBDEL_ALL,
        )
    )
    sent = bot.send_message(
        message.chat.id,
        "Ваши подписки:\n" + "\n".join(lines),
        reply_markup=markup,
    )
    storage.set_last_inline_message_id(message.from_user.id, sent.message_id)


def register_subscription_handlers(ctx) -> None:
    """Register all subscription-related handlers."""
    bot = ctx.bot
    storage = ctx.storage
    downloader = ctx.downloader

    @bot.message_handler(commands=["subscriptions"])
    def list_subscriptions_cmd(message: types.Message) -> None:
        _list_subscriptions(ctx, message)

    @bot.callback_query_handler(
        func=lambda call: call.data and call.data.startswith(f"{CB_SUBSCRIBE}|")
    )
    def handle_subscribe(call: types.CallbackQuery) -> None:
        ctx.ensure_user(call.from_user)
        if not ctx.check_access(call.from_user.id, call.message.chat.id):
            return
        _, token, resolution = call.data.split("|", 2)
        request = storage.get_request(token)
        if request is None:
            bot.answer_callback_query(call.id, "Запрос устарел")
            return
        _, title, _, channel_url = request
        if not channel_url:
            bot.send_message(
                call.message.chat.id,
                "Не удалось определить канал для подписки.",
            )
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
        storage.set_last_inline_message_id(
            call.from_user.id, call.message.message_id,
        )

    @bot.callback_query_handler(
        func=lambda call: call.data and call.data.startswith(f"{CB_SUBMENU}|")
    )
    def handle_subscription_menu(call: types.CallbackQuery) -> None:
        ctx.ensure_user(call.from_user)
        if not ctx.check_access(call.from_user.id, call.message.chat.id):
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
                f"{title}\nВы выбираете качество для подписки.\n"
                "Выберите качество подписки:",
                call.message.chat.id,
                call.message.message_id,
                reply_markup=build_subscription_menu(token, options),
            )
        except Exception:
            bot.send_message(
                call.message.chat.id,
                f"{title}\nВы выбираете качество для подписки.\n"
                "Выберите качество подписки:",
                reply_markup=build_subscription_menu(token, options),
            )
        storage.set_last_inline_message_id(
            call.from_user.id, call.message.message_id,
        )

    @bot.callback_query_handler(
        func=lambda call: call.data and call.data.startswith(f"{CB_BACK}|")
    )
    def handle_back_to_download(call: types.CallbackQuery) -> None:
        ctx.ensure_user(call.from_user)
        if not ctx.check_access(call.from_user.id, call.message.chat.id):
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
                f"{title}\n"
                "Возвращаемся к выбору качества скачивания.\n"
                "Выберите качество или формат:",
                call.message.chat.id,
                call.message.message_id,
                reply_markup=build_format_keyboard(token, options),
            )
        except Exception:
            bot.send_message(
                call.message.chat.id,
                f"{title}\n"
                "Возвращаемся к выбору качества скачивания.\n"
                "Выберите качество или формат:",
                reply_markup=build_format_keyboard(token, options),
            )
        storage.set_last_inline_message_id(
            call.from_user.id, call.message.message_id,
        )

    @bot.callback_query_handler(
        func=lambda call: call.data and call.data.startswith(f"{CB_UNSUB}|")
    )
    def handle_unsubscribe(call: types.CallbackQuery) -> None:
        ctx.ensure_user(call.from_user)
        if not ctx.check_access(call.from_user.id, call.message.chat.id):
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
            bot.send_message(
                call.message.chat.id, f"Подписка на {title} отменена.",
            )
        storage.set_last_inline_message_id(call.from_user.id, None)

    @bot.callback_query_handler(func=lambda call: call.data == CB_SUBDEL_ALL)
    def handle_delete_all(call: types.CallbackQuery) -> None:
        ctx.ensure_user(call.from_user)
        if not ctx.check_access(call.from_user.id, call.message.chat.id):
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

    @bot.callback_query_handler(
        func=lambda call: call.data and call.data.startswith(f"{CB_SUBDEL}|")
    )
    def handle_delete_subscription(call: types.CallbackQuery) -> None:
        ctx.ensure_user(call.from_user)
        if not ctx.check_access(call.from_user.id, call.message.chat.id):
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
