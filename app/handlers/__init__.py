"""Bot handler registration."""

from app.handlers.download import register_download_handlers
from app.handlers.subscription import register_subscription_handlers
from app.handlers.admin import register_admin_handlers


def register_all_handlers(ctx) -> None:
    """Register all bot handlers with shared context."""
    register_download_handlers(ctx)
    register_subscription_handlers(ctx)
    register_admin_handlers(ctx)
