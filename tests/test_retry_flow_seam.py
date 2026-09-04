"""Standalone-проверка интеграционного шва повторной загрузки."""

import os
import sys
import tempfile
import threading
import time


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)


with tempfile.TemporaryDirectory() as temp_dir:
    os.environ["DATA_DIR"] = temp_dir
    os.environ["DB_FILENAME"] = "retry-flow-seam.db"

    from telebot import TeleBot

    from app.constants import STATUS_FAILED, STATUS_RETRYING
    from app.download_queue import DownloadManager
    from app.downloader import VideoDownloader
    from app.handlers.download import register_download_handlers
    from app.main import BotContext
    from app.storage import Storage
    from app.utils import ActiveDownloads, MembershipCache

    checks = 0

    def check(condition: bool) -> None:
        global checks
        assert condition
        checks += 1

    download_manager = DownloadManager(
        max_workers=1,
        max_queue_size=5,
        max_active_per_user=1,
    )
    prepare_entered = threading.Event()
    allow_prepare = threading.Event()

    try:
        bot = TeleBot("0000000000:TEST-TOKEN-NO-NETWORK")

        def fail_send(*args, **kwargs):
            raise RuntimeError("Telegram API отключён в seam-тесте")

        bot.send_message = fail_send

        storage = Storage()
        original_is_blocked = storage.is_blocked

        def synchronized_is_blocked(user_id: int) -> bool:
            prepare_entered.set()
            if not allow_prepare.wait(timeout=10):
                raise TimeoutError("Не разрешено продолжение подготовки")
            return original_is_blocked(user_id)

        storage.is_blocked = synchronized_is_blocked

        ctx = BotContext(
            bot=bot,
            storage=storage,
            downloader=object.__new__(VideoDownloader),
            download_manager=download_manager,
            membership_cache=MembershipCache(),
            active_downloads=ActiveDownloads(),
        )
        ctx.shutdown_requested = True
        register_download_handlers(ctx)

        record_id = storage.record_failed_download(
            user_id=77,
            chat_id=77,
            url="https://example.com/seam",
            format_id=None,
            error_class="timeout",
            error_text="initial timeout",
        )
        check(record_id is not None)
        check(callable(getattr(ctx, "retry_failed_download", None)))

        ok = ctx.retry_failed_download(record_id)
        check(ok is True)
        check(prepare_entered.wait(timeout=10))

        retrying_row = storage.get_failed_download(record_id)
        check(retrying_row is not None and retrying_row[12] == STATUS_RETRYING)
        check(retrying_row is not None and retrying_row[13] == 1)

        allow_prepare.set()
        deadline = time.monotonic() + 10
        failed_row = None
        while time.monotonic() < deadline:
            failed_row = storage.get_failed_download(record_id)
            if (
                failed_row is not None
                and failed_row[12] == STATUS_FAILED
                and "завершение работы" in (failed_row[11] or "")
            ):
                break
            time.sleep(0.2)

        check(failed_row is not None and failed_row[12] == STATUS_FAILED)
        check(
            failed_row is not None
            and "завершение работы" in (failed_row[11] or "")
        )

        storage.is_blocked = original_is_blocked

        def fail_carousel(url: str):
            raise RuntimeError("carousel-path-marker")

        ctx.downloader.download_carousel = fail_carousel
        carousel_id = storage.record_failed_download(
            user_id=88,
            chat_id=88,
            url="https://www.instagram.com/p/carousel-seam/",
            is_carousel=True,
            platform="Instagram",
            title="Карусель",
            error_class="site",
            error_text="initial carousel failure",
        )
        check(carousel_id is not None)
        check(ctx.retry_failed_download(carousel_id) is True)

        deadline = time.monotonic() + 10
        carousel_row = None
        while time.monotonic() < deadline:
            carousel_row = storage.get_failed_download(carousel_id)
            if (
                carousel_row is not None
                and carousel_row[12] == STATUS_FAILED
                and "carousel-path-marker" in (carousel_row[11] or "")
            ):
                break
            time.sleep(0.2)

        check(carousel_row is not None and carousel_row[12] == STATUS_FAILED)
        check(
            carousel_row is not None
            and "carousel-path-marker" in (carousel_row[11] or "")
        )

        def empty_carousel(url: str):
            return [], {}

        bot.send_message = lambda *args, **kwargs: None
        ctx.downloader.download_carousel = empty_carousel
        empty_carousel_id = storage.record_failed_download(
            user_id=99,
            chat_id=99,
            url="https://www.instagram.com/p/empty-carousel-seam/",
            is_carousel=True,
            platform="Instagram",
            title="Пустая карусель",
            error_class="site",
            error_text="initial empty carousel failure",
        )
        check(empty_carousel_id is not None)
        check(ctx.retry_failed_download(empty_carousel_id) is True)

        deadline = time.monotonic() + 10
        empty_carousel_row = None
        while time.monotonic() < deadline:
            empty_carousel_row = storage.get_failed_download(empty_carousel_id)
            if (
                empty_carousel_row is not None
                and empty_carousel_row[12] == STATUS_FAILED
            ):
                break
            time.sleep(0.2)

        check(
            empty_carousel_row is not None
            and empty_carousel_row[12] == STATUS_FAILED
        )
    finally:
        allow_prepare.set()
        download_manager.shutdown(timeout=3)

print(f"SEAM TESTS OK: {checks} проверок")
