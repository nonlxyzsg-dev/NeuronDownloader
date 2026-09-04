"""Standalone-проверки классификатора ошибок и персистенции ретраев."""

import os
import sys
import tempfile


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)


with tempfile.TemporaryDirectory() as temp_dir:
    os.environ["DATA_DIR"] = temp_dir
    os.environ["DB_FILENAME"] = "retry-test.db"

    from app.download_errors import (
        CLASS_403,
        CLASS_429,
        CLASS_FORMAT,
        CLASS_SITE,
        CLASS_TIMEOUT,
        classify_download_error,
        is_auto_retryable,
    )
    from app.constants import STATUS_DONE, STATUS_FAILED, STATUS_RETRYING
    from app.downloader import compute_download_deadline
    import app.storage as storage_module
    from app.storage import Storage

    checks = 0

    def check(condition: bool) -> None:
        global checks
        assert condition
        checks += 1

    # Позитивные и негативные пробы классификатора.
    probes = (
        (TimeoutError("Загрузка превысила таймаут 600с (скачано 1.2 GB из 2.0 GB)"), "", CLASS_TIMEOUT),
        (Exception("HTTP Error 403: Forbidden"), "", CLASS_403),
        (Exception("HTTP Error 429: Too Many Requests"), "", CLASS_429),
        (Exception("ERROR: [youtube] dQw4w9WgXcQ: Requested format is not available. Use -F for a list"), "", CLASS_FORMAT),
        (Exception("login required to view this video"), "https://www.instagram.com/p/abc123/", CLASS_SITE),
        (Exception("Private video. Sign in if you've been granted access to this video"), "", CLASS_SITE),
        (Exception("Video unavailable. This video is no longer available because the uploader closed their account"), "", CLASS_SITE),
        (Exception("HTTP Error 404: Not Found"), "", CLASS_SITE),
        (Exception("Requested format ab403cd is not valid"), "", CLASS_SITE),
        (Exception("socket timeout: connection timed out"), "", CLASS_SITE),
    )
    for exc, url, expected in probes:
        check(classify_download_error(exc, url) == expected)
    check(is_auto_retryable(CLASS_TIMEOUT))
    check(not is_auto_retryable(CLASS_SITE))

    # Адаптивный дедлайн: пол, расчёт по размеру и потолок.
    started = 100.0
    deadline_args = (600, 7200, 256_000)
    check(compute_download_deadline(started, None, *deadline_args) == 700.0)
    check(
        compute_download_deadline(started, 256_000_000, *deadline_args)
        == started + 1000.0
    )
    check(
        compute_download_deadline(started, 10**12, *deadline_args)
        == started + 7200
    )
    check(
        compute_download_deadline(started, 1, 99999, 7200, 256_000)
        == started + 7200
    )
    check(
        compute_download_deadline(started, 10_000_000, *deadline_args)
        == started + 600
    )
    check(
        compute_download_deadline(started, 2_000_000_000, *deadline_args)
        > compute_download_deadline(started, 1_000_000_000, *deadline_args)
    )

    storage = Storage()

    # Статусная машина: failed -> retrying -> done.
    done_id = storage.record_failed_download(1, 101, "https://example.com/done")
    check(done_id is not None)
    check(storage.mark_failed_download_retrying(done_id))
    done_row = storage.get_failed_download(done_id)
    check(done_row is not None and len(done_row) == 16)
    check(
        done_row is not None
        and done_row[12] == STATUS_RETRYING
        and done_row[13] == 1
    )
    storage.mark_failed_download_done(done_id)
    check(storage.get_failed_download(done_id)[12] == STATUS_DONE)
    check(done_id not in {row[0] for row in storage.list_failed_downloads()})
    with storage._connect() as conn:
        conn.execute(
            "UPDATE failed_downloads "
            "SET updated_at = datetime('now', ?) WHERE id = ?",
            ("-31 days", done_id),
        )
    check(storage.cleanup_old_failed_downloads() == 1)
    check(storage.get_failed_download(done_id) is None)

    # Повторный провал сохраняет attempts=1.
    failed_id = storage.record_failed_download(2, 102, "https://example.com/failed")
    check(storage.mark_failed_download_retrying(failed_id))
    storage.mark_failed_download_failed(failed_id, CLASS_TIMEOUT, "timeout again")
    failed_row = storage.get_failed_download(failed_id)
    check(
        failed_row is not None
        and failed_row[10:14]
        == (CLASS_TIMEOUT, "timeout again", STATUS_FAILED, 1)
    )
    storage.mark_failed_download_failed(failed_id, error_text="timeout preserved")
    failed_row = storage.get_failed_download(failed_id)
    check(
        failed_row is not None
        and failed_row[10:12] == (CLASS_TIMEOUT, "timeout preserved")
    )
    check(failed_id in {row[0] for row in storage.list_failed_downloads()})

    # Дедупликация по user_id + url + format_id.
    dedup_id = storage.record_failed_download(
        3, 103, "https://example.com/dedup", "720", error_class=CLASS_403,
        error_text="first", is_carousel=True,
    )
    same_id = storage.record_failed_download(
        3, 103, "https://example.com/dedup", "720", error_class=CLASS_429,
        error_text="second",
    )
    check(dedup_id == same_id)
    dedup_row = storage.get_failed_download(dedup_id)
    check(
        dedup_row is not None
        and dedup_row[7] == 1
        and dedup_row[10:12] == (CLASS_429, "second")
    )
    with storage._connect() as conn:
        dedup_count = conn.execute(
            "SELECT COUNT(*) FROM failed_downloads WHERE user_id = ? AND url = ?",
            (3, "https://example.com/dedup"),
        ).fetchone()[0]
    check(dedup_count == 1)

    # Восстановление зависшего retrying.
    stale_id = storage.record_failed_download(4, 104, "https://example.com/stale")
    with storage._connect() as conn:
        conn.execute(
            "UPDATE failed_downloads SET status = ? WHERE id = ?",
            ("retrying", stale_id),
        )
    check(storage.reset_stale_retrying() == 1)
    check(storage.get_failed_download(stale_id)[12] == STATUS_FAILED)

    # Успех по URL закрывает все открытые форматы.
    shared_url = "https://example.com/shared"
    storage.record_failed_download(5, 105, shared_url, "360")
    storage.record_failed_download(5, 105, shared_url, "720")
    count_before = storage.count_failed_downloads()
    check(storage.mark_failed_downloads_done_for_url(5, shared_url) == 2)
    check(storage.count_failed_downloads() == count_before - 2)

    # Пагинация: отдельная БД без записей предыдущих сценариев.
    storage_module.DB_FILENAME = "pagination-test.db"
    pagination_storage = Storage()
    for index in range(10):
        pagination_storage.record_failed_download(
            10 + index, 200 + index, f"https://example.com/page/{index}"
        )
    check(len(pagination_storage.list_failed_downloads(page=0, per_page=8)) == 8)
    check(len(pagination_storage.list_failed_downloads(page=1, per_page=8)) == 2)
    check(pagination_storage.count_failed_downloads() == 10)

    # Окно 7 дней исключает запись старше 8 дней.
    old_id = pagination_storage.list_failed_downloads(page=1, per_page=8)[0][0]
    with pagination_storage._connect() as conn:
        conn.execute(
            "UPDATE failed_downloads "
            "SET created_at = datetime('now', ?) WHERE id = ?",
            ("-8 days", old_id),
        )
    check(old_id not in {row[0] for row in pagination_storage.list_failed_downloads()})
    check(pagination_storage.count_failed_downloads() == 9)

print(f"TESTS OK: {checks} проверок")
