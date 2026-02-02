import logging
import os
import threading
import time

from app.config import CLEANUP_INTERVAL_SECONDS, CLEANUP_MAX_AGE_SECONDS, DATA_DIR, DB_FILENAME


class DataCleanupMonitor:
    def __init__(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._stop_event = threading.Event()

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()

    def _run(self) -> None:
        while not self._stop_event.is_set():
            self._cleanup_data_dir()
            self._stop_event.wait(CLEANUP_INTERVAL_SECONDS)

    def _cleanup_data_dir(self) -> None:
        now = time.time()
        cutoff = now - CLEANUP_MAX_AGE_SECONDS
        for root, _dirs, files in os.walk(DATA_DIR):
            for filename in files:
                if filename == DB_FILENAME:
                    continue
                path = os.path.join(root, filename)
                try:
                    if os.path.getmtime(path) < cutoff:
                        os.remove(path)
                except FileNotFoundError:
                    continue
                except OSError:
                    logging.exception("Failed to cleanup file %s", path)
