"""Настройка логирования с ротацией файлов."""

import logging
import os
from logging.handlers import RotatingFileHandler

from app.config import DATA_DIR, LOG_LEVEL, TELEBOT_LOG_LEVEL

# Максимальный размер файла логов — 50 МБ
LOG_MAX_BYTES = 50 * 1024 * 1024
# Количество резервных файлов (итого до 100 МБ)
LOG_BACKUP_COUNT = 1
LOG_FILENAME = "bot.log"


def get_log_file_path() -> str:
    """Возвращает путь к файлу логов."""
    return os.path.join(DATA_DIR, LOG_FILENAME)


def setup_logging() -> None:
    """Настраивает логирование: консоль + файл с ротацией."""
    os.makedirs(DATA_DIR, exist_ok=True)

    log_level = getattr(logging, LOG_LEVEL, logging.INFO)
    log_format = "%(asctime)s | %(levelname)s | %(name)s | %(message)s"

    # Корневой логгер
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)

    # Консольный обработчик
    console_handler = logging.StreamHandler()
    console_handler.setLevel(log_level)
    console_handler.setFormatter(logging.Formatter(log_format))
    root_logger.addHandler(console_handler)

    # Файловый обработчик с ротацией (50 МБ, 1 резервная копия)
    log_path = get_log_file_path()
    file_handler = RotatingFileHandler(
        log_path,
        maxBytes=LOG_MAX_BYTES,
        backupCount=LOG_BACKUP_COUNT,
        encoding="utf-8",
    )
    file_handler.setLevel(log_level)
    file_handler.setFormatter(logging.Formatter(log_format))
    root_logger.addHandler(file_handler)

    # Подавляем логи TeleBot
    for logger_name in ("TeleBot", "telebot"):
        logging.getLogger(logger_name).setLevel(TELEBOT_LOG_LEVEL)
