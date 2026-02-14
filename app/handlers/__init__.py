"""Регистрация обработчиков бота."""

from app.handlers.admin import register_admin_handlers
from app.handlers.support import register_support_handlers
from app.handlers.download import register_download_handlers


def register_all_handlers(ctx) -> None:
    """Регистрирует все обработчики бота с общим контекстом.

    Порядок важен: обработчики админ-панели и поддержки должны быть
    зарегистрированы до обработчика скачивания, который содержит
    catch-all обработчик текстовых сообщений.
    """
    register_admin_handlers(ctx)
    register_support_handlers(ctx)
    register_download_handlers(ctx)
