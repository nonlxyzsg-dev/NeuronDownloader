"""Bot handler registration."""

from app.handlers.admin import register_admin_handlers
from app.handlers.support import register_support_handlers
from app.handlers.download import register_download_handlers


def register_all_handlers(ctx) -> None:
    """Register all bot handlers with shared context.

    Order matters: admin and support handlers must be registered before
    the download handler which has the catch-all text message handler.
    """
    register_admin_handlers(ctx)
    register_support_handlers(ctx)
    register_download_handlers(ctx)
