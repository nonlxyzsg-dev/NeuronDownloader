"""Обработчики поддержки: отправка обращений пользователями."""

import logging

from app.config import ADMIN_IDS
from app.constants import (
    EMOJI_ALERT,
    EMOJI_DONE,
    EMOJI_REPORT,
    MENU_REPORT,
    STATE_AWAITING_REPORT,
)
from app.keyboards import build_main_menu

logger = logging.getLogger(__name__)


def register_support_handlers(ctx) -> None:
    """Регистрирует все обработчики системы поддержки для пользователей.

    Обработчики ответов админа находятся в admin.py во избежание дублирования.
    """
    bot = ctx.bot
    storage = ctx.storage

    # ------------------------------------------------------------------
    # Вспомогательные функции
    # ------------------------------------------------------------------

    def _notify_admins(ticket_id: int, user_id: int, username: str | None,
                       text: str, file_id: str | None = None,
                       file_type: str | None = None) -> None:
        """Отправляет уведомление о новом обращении всем админам."""
        handle = f"@{username}" if username else "\u043d\u0435\u0442"
        header = (
            f"\U0001f4ec \u041d\u043e\u0432\u043e\u0435 \u043e\u0431\u0440\u0430\u0449\u0435\u043d\u0438\u0435 #{ticket_id}\n"
            f"\U0001f464 \u041f\u043e\u043b\u044c\u0437\u043e\u0432\u0430\u0442\u0435\u043b\u044c: {user_id} ({handle})\n"
        )
        body = f"\n{text}" if text else ""
        for admin_id in ADMIN_IDS:
            try:
                if file_id and file_type == "photo":
                    bot.send_photo(admin_id, file_id, caption=header + body)
                elif file_id and file_type == "video":
                    bot.send_video(admin_id, file_id, caption=header + body)
                elif file_id and file_type == "document":
                    bot.send_document(admin_id, file_id, caption=header + body)
                else:
                    bot.send_message(admin_id, header + body)
            except Exception:
                logger.exception("\u041d\u0435 \u0443\u0434\u0430\u043b\u043e\u0441\u044c \u0443\u0432\u0435\u0434\u043e\u043c\u0438\u0442\u044c \u0430\u0434\u043c\u0438\u043d\u0438\u0441\u0442\u0440\u0430\u0442\u043e\u0440\u0430 %s \u043e \u0442\u0438\u043a\u0435\u0442\u0435 #%s",
                                 admin_id, ticket_id)

    def _confirm_ticket(chat_id: int, ticket_id: int) -> None:
        """Отправляет пользователю подтверждение создания обращения."""
        bot.send_message(
            chat_id,
            f"{EMOJI_DONE} \u041e\u0431\u0440\u0430\u0449\u0435\u043d\u0438\u0435 #{ticket_id} \u0441\u043e\u0437\u0434\u0430\u043d\u043e. "
            "\u041c\u044b \u043e\u0442\u0432\u0435\u0442\u0438\u043c \u0432\u0430\u043c \u0432 \u0431\u043b\u0438\u0436\u0430\u0439\u0448\u0435\u0435 \u0432\u0440\u0435\u043c\u044f.",
            reply_markup=build_main_menu(),
        )

    # ------------------------------------------------------------------
    # Команда /report
    # ------------------------------------------------------------------

    @bot.message_handler(commands=["report"])
    def report_cmd(message):
        try:
            ctx.ensure_user(message.from_user)
            if not ctx.check_access(message.from_user.id, message.chat.id):
                return
            ctx.clear_last_inline(message.from_user.id, message.chat.id)

            ctx.set_user_state(message.from_user.id, STATE_AWAITING_REPORT)
            bot.send_message(
                message.chat.id,
                f"{EMOJI_REPORT} \u041e\u043f\u0438\u0448\u0438\u0442\u0435 \u0432\u0430\u0448\u0443 \u043f\u0440\u043e\u0431\u043b\u0435\u043c\u0443. "
                "\u0412\u044b \u043c\u043e\u0436\u0435\u0442\u0435 \u043e\u0442\u043f\u0440\u0430\u0432\u0438\u0442\u044c \u0442\u0435\u043a\u0441\u0442, \u0444\u043e\u0442\u043e, \u0432\u0438\u0434\u0435\u043e \u0438\u043b\u0438 \u0434\u043e\u043a\u0443\u043c\u0435\u043d\u0442.",
            )
        except Exception:
            logger.exception("\u041e\u0448\u0438\u0431\u043a\u0430 \u0432 /report (user=%s)", message.from_user.id)

    # ------------------------------------------------------------------
    # Нажатие кнопки MENU_REPORT (текстовое совпадение)
    # ------------------------------------------------------------------

    @bot.message_handler(func=lambda msg: msg.text == MENU_REPORT)
    def report_menu(message):
        try:
            ctx.ensure_user(message.from_user)
            if not ctx.check_access(message.from_user.id, message.chat.id):
                return
            ctx.clear_last_inline(message.from_user.id, message.chat.id)

            ctx.set_user_state(message.from_user.id, STATE_AWAITING_REPORT)
            bot.send_message(
                message.chat.id,
                f"{EMOJI_REPORT} \u041e\u043f\u0438\u0448\u0438\u0442\u0435 \u0432\u0430\u0448\u0443 \u043f\u0440\u043e\u0431\u043b\u0435\u043c\u0443. "
                "\u0412\u044b \u043c\u043e\u0436\u0435\u0442\u0435 \u043e\u0442\u043f\u0440\u0430\u0432\u0438\u0442\u044c \u0442\u0435\u043a\u0441\u0442, \u0444\u043e\u0442\u043e, \u0432\u0438\u0434\u0435\u043e \u0438\u043b\u0438 \u0434\u043e\u043a\u0443\u043c\u0435\u043d\u0442.",
            )
        except Exception:
            logger.exception("\u041e\u0448\u0438\u0431\u043a\u0430 \u043f\u0440\u0438 \u043d\u0430\u0436\u0430\u0442\u0438\u0438 \u043a\u043d\u043e\u043f\u043a\u0438 \u0440\u0435\u043f\u043e\u0440\u0442\u0430 (user=%s)",
                             message.from_user.id)

    # ------------------------------------------------------------------
    # Обращение пользователя: текст
    # ------------------------------------------------------------------

    @bot.message_handler(
        func=lambda msg: ctx.get_user_state(msg.from_user.id) == STATE_AWAITING_REPORT
        and msg.text is not None,
    )
    def handle_report_text(message):
        user_id = message.from_user.id
        try:
            ticket_id = storage.create_ticket(user_id)
            storage.add_ticket_message(
                ticket_id,
                from_user_id=user_id,
                is_admin=False,
                text=message.text,
                file_id=None,
                file_type=None,
            )
            ctx.set_user_state(user_id, None)

            _confirm_ticket(message.chat.id, ticket_id)
            _notify_admins(
                ticket_id,
                user_id,
                message.from_user.username,
                message.text,
            )
        except Exception:
            logger.exception("\u041e\u0448\u0438\u0431\u043a\u0430 \u043f\u0440\u0438 \u0441\u043e\u0437\u0434\u0430\u043d\u0438\u0438 \u0442\u0435\u043a\u0441\u0442\u043e\u0432\u043e\u0433\u043e \u0442\u0438\u043a\u0435\u0442\u0430 (user=%s)", user_id)
            bot.send_message(
                message.chat.id,
                f"{EMOJI_ALERT} \u041f\u0440\u043e\u0438\u0437\u043e\u0448\u043b\u0430 \u043e\u0448\u0438\u0431\u043a\u0430 \u043f\u0440\u0438 \u0441\u043e\u0437\u0434\u0430\u043d\u0438\u0438 \u043e\u0431\u0440\u0430\u0449\u0435\u043d\u0438\u044f. \u041f\u043e\u043f\u0440\u043e\u0431\u0443\u0439\u0442\u0435 \u043f\u043e\u0437\u0436\u0435.",
                reply_markup=build_main_menu(),
            )
            ctx.set_user_state(user_id, None)

    # ------------------------------------------------------------------
    # Обращение пользователя: фото
    # ------------------------------------------------------------------

    @bot.message_handler(
        func=lambda msg: ctx.get_user_state(msg.from_user.id) == STATE_AWAITING_REPORT,
        content_types=["photo"],
    )
    def handle_report_photo(message):
        user_id = message.from_user.id
        try:
            file_id = message.photo[-1].file_id
            caption = message.caption or ""

            ticket_id = storage.create_ticket(user_id)
            storage.add_ticket_message(
                ticket_id,
                from_user_id=user_id,
                is_admin=False,
                text=caption,
                file_id=file_id,
                file_type="photo",
            )
            ctx.set_user_state(user_id, None)

            _confirm_ticket(message.chat.id, ticket_id)
            _notify_admins(
                ticket_id,
                user_id,
                message.from_user.username,
                caption,
                file_id=file_id,
                file_type="photo",
            )
        except Exception:
            logger.exception("\u041e\u0448\u0438\u0431\u043a\u0430 \u043f\u0440\u0438 \u0441\u043e\u0437\u0434\u0430\u043d\u0438\u0438 \u0444\u043e\u0442\u043e-\u0442\u0438\u043a\u0435\u0442\u0430 (user=%s)", user_id)
            bot.send_message(
                message.chat.id,
                f"{EMOJI_ALERT} \u041f\u0440\u043e\u0438\u0437\u043e\u0448\u043b\u0430 \u043e\u0448\u0438\u0431\u043a\u0430 \u043f\u0440\u0438 \u0441\u043e\u0437\u0434\u0430\u043d\u0438\u0438 \u043e\u0431\u0440\u0430\u0449\u0435\u043d\u0438\u044f. \u041f\u043e\u043f\u0440\u043e\u0431\u0443\u0439\u0442\u0435 \u043f\u043e\u0437\u0436\u0435.",
                reply_markup=build_main_menu(),
            )
            ctx.set_user_state(user_id, None)

    # ------------------------------------------------------------------
    # Обращение пользователя: видео / документ
    # ------------------------------------------------------------------

    @bot.message_handler(
        func=lambda msg: ctx.get_user_state(msg.from_user.id) == STATE_AWAITING_REPORT,
        content_types=["video", "document"],
    )
    def handle_report_media(message):
        user_id = message.from_user.id
        try:
            if message.video:
                file_id = message.video.file_id
                file_type = "video"
            else:
                file_id = message.document.file_id
                file_type = "document"

            caption = message.caption or ""

            ticket_id = storage.create_ticket(user_id)
            storage.add_ticket_message(
                ticket_id,
                from_user_id=user_id,
                is_admin=False,
                text=caption,
                file_id=file_id,
                file_type=file_type,
            )
            ctx.set_user_state(user_id, None)

            _confirm_ticket(message.chat.id, ticket_id)
            _notify_admins(
                ticket_id,
                user_id,
                message.from_user.username,
                caption,
                file_id=file_id,
                file_type=file_type,
            )
        except Exception:
            logger.exception("\u041e\u0448\u0438\u0431\u043a\u0430 \u043f\u0440\u0438 \u0441\u043e\u0437\u0434\u0430\u043d\u0438\u0438 \u043c\u0435\u0434\u0438\u0430-\u0442\u0438\u043a\u0435\u0442\u0430 (user=%s)", user_id)
            bot.send_message(
                message.chat.id,
                f"{EMOJI_ALERT} \u041f\u0440\u043e\u0438\u0437\u043e\u0448\u043b\u0430 \u043e\u0448\u0438\u0431\u043a\u0430 \u043f\u0440\u0438 \u0441\u043e\u0437\u0434\u0430\u043d\u0438\u0438 \u043e\u0431\u0440\u0430\u0449\u0435\u043d\u0438\u044f. \u041f\u043e\u043f\u0440\u043e\u0431\u0443\u0439\u0442\u0435 \u043f\u043e\u0437\u0436\u0435.",
                reply_markup=build_main_menu(),
            )
            ctx.set_user_state(user_id, None)
