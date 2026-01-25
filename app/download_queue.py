from concurrent.futures import Future, ThreadPoolExecutor
from typing import Callable, TypeVar

T = TypeVar("T")


class DownloadManager:
    def __init__(self, max_workers: int) -> None:
        self.executor = ThreadPoolExecutor(max_workers=max_workers)

    def submit(self, func: Callable[..., T], *args, **kwargs) -> Future[T]:
        return self.executor.submit(func, *args, **kwargs)

    def shutdown(self) -> None:
        self.executor.shutdown(wait=False)
