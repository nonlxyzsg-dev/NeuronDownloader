import logging

from app.config import LOG_LEVEL, TELEBOT_LOG_LEVEL


def setup_logging() -> None:
    logging.basicConfig(
        level=getattr(logging, LOG_LEVEL, logging.INFO),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )
    for logger_name in ("TeleBot", "telebot"):
        logging.getLogger(logger_name).setLevel(TELEBOT_LOG_LEVEL)
