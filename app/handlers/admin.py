"""Admin command handlers: /stats, /users, /block, /unblock."""

from telebot import types

from app.utils import is_admin


def register_admin_handlers(ctx) -> None:
    """Register all admin-related handlers."""
    bot = ctx.bot
    storage = ctx.storage

    @bot.message_handler(commands=["stats"])
    def show_stats(message: types.Message) -> None:
        ctx.ensure_user(message.from_user)
        if not is_admin(message.from_user.id):
            bot.send_message(
                message.chat.id, "Команда доступна только администратору.",
            )
            return
        ctx.clear_last_inline(message.from_user.id, message.chat.id)
        total_users, total_downloads = storage.get_usage_stats()
        per_user = storage.get_user_stats()
        lines = [
            f"Всего пользователей: {total_users}",
            f"Всего загрузок: {total_downloads}",
            "Статистика по пользователям:",
        ]
        for uid, count in per_user:
            lines.append(f"- {uid}: {count}")
        bot.send_message(message.chat.id, "\n".join(lines))

    @bot.message_handler(commands=["users"])
    def show_users(message: types.Message) -> None:
        ctx.ensure_user(message.from_user)
        if not is_admin(message.from_user.id):
            bot.send_message(
                message.chat.id, "Команда доступна только администратору.",
            )
            return
        ctx.clear_last_inline(message.from_user.id, message.chat.id)
        lines = ["Пользователи:"]
        for uid, username, first_name, last_name, blocked in storage.list_users():
            display = " ".join(part for part in [first_name, last_name] if part)
            blocked_label = "заблокирован" if blocked else "активен"
            lines.append(f"- {uid} @{username} {display} ({blocked_label})")
        bot.send_message(message.chat.id, "\n".join(lines))

    @bot.message_handler(commands=["block"])
    def block_user(message: types.Message) -> None:
        ctx.ensure_user(message.from_user)
        if not is_admin(message.from_user.id):
            bot.send_message(
                message.chat.id, "Команда доступна только администратору.",
            )
            return
        ctx.clear_last_inline(message.from_user.id, message.chat.id)
        parts = message.text.split()
        if len(parts) < 2:
            bot.send_message(message.chat.id, "Использование: /block <user_id>")
            return
        try:
            target_id = int(parts[1])
        except ValueError:
            bot.send_message(message.chat.id, "Некорректный user_id.")
            return
        storage.set_blocked(target_id, True)
        bot.send_message(
            message.chat.id, f"Пользователь {target_id} заблокирован.",
        )

    @bot.message_handler(commands=["unblock"])
    def unblock_user(message: types.Message) -> None:
        ctx.ensure_user(message.from_user)
        if not is_admin(message.from_user.id):
            bot.send_message(
                message.chat.id, "Команда доступна только администратору.",
            )
            return
        ctx.clear_last_inline(message.from_user.id, message.chat.id)
        parts = message.text.split()
        if len(parts) < 2:
            bot.send_message(
                message.chat.id, "Использование: /unblock <user_id>",
            )
            return
        try:
            target_id = int(parts[1])
        except ValueError:
            bot.send_message(message.chat.id, "Некорректный user_id.")
            return
        storage.set_blocked(target_id, False)
        bot.send_message(
            message.chat.id, f"Пользователь {target_id} разблокирован.",
        )
