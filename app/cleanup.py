"""Фоновая очистка временных файлов в каталоге данных."""

import logging
import os
import threading
import time

from app.config import CLEANUP_INTERVAL_SECONDS, CLEANUP_MAX_AGE_SECONDS, DATA_DIR, DB_FILENAME


class DataCleanupMonitor:
    """Периодически удаляет устаревшие файлы из DATA_DIR."""

    def __init__(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._stop_event = threading.Event()

    def start(self) -> None:
        """Запускает фоновый поток очистки."""
        self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        """Останавливает мониторинг очистки данных."""
        self._stop_event.set()
        self._thread.join(timeout=timeout)

    def _run(self) -> None:
        """Основной цикл: очистка → ожидание интервала."""
        while not self._stop_event.is_set():
            self._cleanup_data_dir()
            self._stop_event.wait(CLEANUP_INTERVAL_SECONDS)

    def _cleanup_data_dir(self) -> None:
        """Удаляет файлы старше CLEANUP_MAX_AGE_SECONDS (кроме БД и логов)."""
        now = time.time()
        cutoff = now - CLEANUP_MAX_AGE_SECONDS
        for root, _dirs, files in os.walk(DATA_DIR):
            for filename in files:
                if filename == DB_FILENAME:
                    continue
                # Не удаляем файлы логов
                if filename.endswith(".log") or filename.endswith(".log.1"):
                    continue
                path = os.path.join(root, filename)
                try:
                    if os.path.getmtime(path) < cutoff:
                        os.remove(path)
                except FileNotFoundError:
                    continue
                except OSError:
                    logging.exception("Не удалось удалить файл %s", path)
