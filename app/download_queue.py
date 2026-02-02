from __future__ import annotations

import queue
import threading

from concurrent.futures import Future
from typing import Callable, TypeVar

T = TypeVar("T")


class DownloadManager:
    def __init__(
        self,
        max_workers: int,
        max_queue_size: int,
        max_active_per_user: int | None = None,
    ) -> None:
        self._queue: queue.Queue[
            tuple[Callable[..., T], tuple, dict, Future[T], int | None]
        ] = queue.Queue(maxsize=max_queue_size)
        self._workers = [
            threading.Thread(target=self._worker, daemon=True)
            for _ in range(max_workers)
        ]
        self._stop_event = threading.Event()
        self._max_queue_size = max_queue_size
        self._max_active_per_user = (
            max_active_per_user if max_active_per_user and max_active_per_user > 0 else None
        )
        self._active_counts: dict[int, int] = {}
        self._active_lock = threading.Lock()
        self._active_condition = threading.Condition(self._active_lock)
        for worker in self._workers:
            worker.start()

    def submit(self, func: Callable[..., T], *args, **kwargs) -> Future[T]:
        future: Future[T] = Future()
        self._queue.put_nowait((func, args, kwargs, future, None))
        return future

    def submit_user(self, user_id: int, func: Callable[..., T], *args, **kwargs) -> Future[T]:
        future: Future[T] = Future()
        self._queue.put_nowait((func, args, kwargs, future, user_id))
        return future

    def queued_count(self) -> int:
        return self._queue.qsize()

    def max_queue_size(self) -> int:
        return self._max_queue_size

    def active_count(self, user_id: int) -> int:
        with self._active_lock:
            return self._active_counts.get(user_id, 0)

    def _worker(self) -> None:
        while True:
            try:
                item = self._queue.get(timeout=0.5)
            except queue.Empty:
                if self._stop_event.is_set():
                    break
                continue
            func, args, kwargs, future, user_id = item
            if user_id is not None and self._max_active_per_user is not None:
                with self._active_condition:
                    while self._active_counts.get(user_id, 0) >= self._max_active_per_user:
                        self._active_condition.wait(timeout=0.5)
                        if self._stop_event.is_set():
                            break
                    self._active_counts[user_id] = self._active_counts.get(user_id, 0) + 1
            if future.set_running_or_notify_cancel():
                try:
                    result = func(*args, **kwargs)
                except Exception as exc:
                    future.set_exception(exc)
                else:
                    future.set_result(result)
            if user_id is not None and self._max_active_per_user is not None:
                with self._active_condition:
                    self._active_counts[user_id] = max(
                        0, self._active_counts.get(user_id, 0) - 1
                    )
                    self._active_condition.notify_all()
            self._queue.task_done()

    def shutdown(self) -> None:
        self._stop_event.set()
        for worker in self._workers:
            worker.join(timeout=1)
