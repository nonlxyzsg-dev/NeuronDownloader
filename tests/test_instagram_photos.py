"""Standalone-проверки фото-фолбэка Instagram (полностью офлайн, сеть не трогаем)."""

import http.client
import http.cookiejar
import io
import logging
import os
import shutil
import sys
import tempfile
import urllib.error
import urllib.request

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from app import downloader as downloader_module
from app import instagram_api
from app.downloader import merge_carousel_media

checks = 0


def check(condition: bool) -> None:
    global checks
    assert condition
    checks += 1


# 1. decode: значения сверены живьём на проде (base64-url алфавит Instagram).
check(instagram_api.shortcode_to_pk("DdvelkoDCQP") == 3994545917843481615)
check(instagram_api.shortcode_to_pk("BsOGulcndj-") == 1949525278281554174)

# 2. decode негатив: невалидный символ / пустая строка / не-строка.
for bad in ("DdvelkoDCQ@", "", None):
    try:
        instagram_api.shortcode_to_pk(bad)
        raised = False
    except (ValueError, TypeError):
        raised = True
    check(raised)

# 3. extract_shortcode: p/reel/tv с query, чужой домен, пустой код.
check(instagram_api.extract_shortcode("https://www.instagram.com/p/DdvelkoDCQP/") == "DdvelkoDCQP")
check(
    instagram_api.extract_shortcode("https://www.instagram.com/reel/AbCdEf01234/?igsh=x")
    == "AbCdEf01234"
)
check(
    instagram_api.extract_shortcode("https://www.instagram.com/tv/CdEfGh12345/?hl=ru")
    == "CdEfGh12345"
)
check(instagram_api.extract_shortcode("https://youtube.com/watch?v=1") is None)
check(instagram_api.extract_shortcode("https://www.instagram.com/p/") is None)
check(instagram_api.extract_shortcode("") is None)

# 4. load_sessionid: Netscape-строка, float-expires в 5-м поле, без instagram, без файла.
with tempfile.TemporaryDirectory() as temp_dir:
    session_file = os.path.join(temp_dir, "cookies_sid.txt")
    with open(session_file, "w", encoding="utf-8") as handle:
        handle.write("# Netscape HTTP Cookie File\n")
        handle.write(
            ".instagram.com\tTRUE\t/\tTRUE\t1811526512.091545\tsessionid\tsynthetic-sid\n"
        )
        handle.write(".youtube.com\tTRUE\t/\tTRUE\t1893456000\tOTHER\tsynthetic-other\n")
    check(instagram_api.load_sessionid(session_file) == "synthetic-sid")

    float_field_file = os.path.join(temp_dir, "cookies_float.txt")
    with open(float_field_file, "w", encoding="utf-8") as handle:
        handle.write("# Netscape HTTP Cookie File\n")
        handle.write(
            ".www.instagram.com\tTRUE\t/\tTRUE\t1811526512.091545\tsessionid\tsynthetic-float\n"
        )
    check(instagram_api.load_sessionid(float_field_file) == "synthetic-float")

    no_instagram_file = os.path.join(temp_dir, "cookies_no_ig.txt")
    with open(no_instagram_file, "w", encoding="utf-8") as handle:
        handle.write("# Netscape HTTP Cookie File\n")
        handle.write(".youtube.com\tTRUE\t/\tTRUE\t1893456000\tsessionid\tsynthetic-yt\n")
    check(instagram_api.load_sessionid(no_instagram_file) is None)
    check(instagram_api.load_sessionid(os.path.join(temp_dir, "missing.txt")) is None)
    check(instagram_api.load_sessionid(None) is None)

    # 4а. Политика «свежайшая строка побеждает» (max expires_at): несколько
    #     instagram-sessionid строк → победитель по максимальному expires,
    #     порядок строк не важен; мусорный expires у строки — пропуск, не
    #     падение.
    def _sid_line(expires_field: str, sid_value: str) -> str:
        return f".instagram.com\tTRUE\t/\tTRUE\t{expires_field}\tsessionid\t{sid_value}\n"

    for order_index, content in enumerate((
        _sid_line("1000000000", "synthetic-old") + _sid_line("9999999999", "synthetic-new"),
        _sid_line("9999999999", "synthetic-new") + _sid_line("1000000000", "synthetic-old"),
    )):
        order_file = os.path.join(temp_dir, f"cookies_max_order{order_index}.txt")
        with open(order_file, "w", encoding="utf-8") as handle:
            handle.write("# Netscape HTTP Cookie File\n")
            handle.write(content)
        check(instagram_api.load_sessionid(order_file) == "synthetic-new")

    # Мусор («мусор» → ValueError, «1e400» → float inf → OverflowError) не валит
    # чтение и не выигрывает; валидная строка возвращается.
    garbage_file = os.path.join(temp_dir, "cookies_garbage_expires.txt")
    with open(garbage_file, "w", encoding="utf-8") as handle:
        handle.write("# Netscape HTTP Cookie File\n")
        handle.write(_sid_line("мусор", "synthetic-garbage"))
        handle.write(_sid_line("1e400", "synthetic-inf"))
        handle.write(_sid_line("1000000000", "synthetic-valid"))
    check(instagram_api.load_sessionid(garbage_file) == "synthetic-valid")

    only_garbage_file = os.path.join(temp_dir, "cookies_only_garbage.txt")
    with open(only_garbage_file, "w", encoding="utf-8") as handle:
        handle.write("# Netscape HTTP Cookie File\n")
        handle.write(_sid_line("мусор", "synthetic-garbage"))
        handle.write(_sid_line("1e400", "synthetic-inf"))
    check(instagram_api.load_sessionid(only_garbage_file) is None)

    # Instagram-строк нет вовсе (sessionid чужого домена не считается) → None.
    no_ig_sid_file = os.path.join(temp_dir, "cookies_no_ig_sid.txt")
    with open(no_ig_sid_file, "w", encoding="utf-8") as handle:
        handle.write("# Netscape HTTP Cookie File\n")
        handle.write(".youtube.com\tTRUE\t/\tTRUE\t1893456000\tsessionid\tsynthetic-yt\n")
    check(instagram_api.load_sessionid(no_ig_sid_file) is None)

# 5. iter_photo_children: урезанный реальный JSON ответа API.
CAROUSEL_FIXTURE = {
    "media_type": 8,
    "carousel_media": [
        {
            "media_type": 1,
            "image_versions2": {"candidates": [
                {
                    "url": "https://scontent-ams2-1.cdninstagram.com/v/t51/small.jpg",
                    "width": 640,
                    "height": 640,
                },
                {
                    "url": "https://scontent-ams2-1.cdninstagram.com/v/t51/big.jpg",
                    "width": 1080,
                    "height": 1080,
                },
            ]},
        },
        {
            "media_type": 2,
            "image_versions2": {"candidates": [
                {
                    "url": "https://scontent-ams2-1.cdninstagram.com/v/t51/cover.jpg",
                    "width": 480,
                    "height": 480,
                },
            ]},
            "video_versions": [{
                "url": "https://scontent-ams2-1.cdninstagram.com/v/t51/clip.mp4",
                "width": 720,
                "height": 720,
            }],
        },
    ],
}
children = instagram_api.iter_photo_children(CAROUSEL_FIXTURE)
check(len(children) == 2)
check(children[0]["is_video"] is False)
check(children[0]["image_url"] == "https://scontent-ams2-1.cdninstagram.com/v/t51/big.jpg")
check(children[0]["width"] == 1080 and children[0]["height"] == 1080)
check(children[1]["is_video"] is True)
check(children[1]["image_url"] == "https://scontent-ams2-1.cdninstagram.com/v/t51/cover.jpg")

single = instagram_api.iter_photo_children({
    "media_type": 1,
    "image_versions2": {"candidates": [{
        "url": "https://scontent-ams2-1.cdninstagram.com/v/t51/one.jpg",
        "width": 100,
        "height": 50,
    }]},
})
check(len(single) == 1 and single[0]["is_video"] is False)
check(single[0]["image_url"] == "https://scontent-ams2-1.cdninstagram.com/v/t51/one.jpg")

# Негатив: None, пустой items, кандидат без width/height.
check(instagram_api.iter_photo_children(None) == [])
check(instagram_api.iter_photo_children({"items": []}) == [])
no_dims = instagram_api.iter_photo_children({
    "media_type": 1,
    "image_versions2": {"candidates": [{"url": "https://scontent-ams2-1.cdninstagram.com/a.jpg"}]},
})
check(len(no_dims) == 1 and no_dims[0]["image_url"].endswith("a.jpg"))
check(no_dims[0]["width"] is None and no_dims[0]["height"] is None)

# 6. merge_carousel_media: порядок, rate-guard, сбои, нехватка видео-файлов.
def _make_fetch_ok(storage_dir, fetched):
    def fetch_ok(child, idx):
        fetched.append(idx)
        path = os.path.join(storage_dir, f"ig{idx:03d}.jpg")
        with open(path, "wb") as handle:
            handle.write(b"\xff\xd8\xff" + b"0" * 2048)
        return {
            "path": path,
            "is_video": False,
            "width": child.get("width"),
            "height": child.get("height"),
        }
    return fetch_ok


with tempfile.TemporaryDirectory() as merge_dir:
    photo_a = {"is_video": False, "image_url": "https://scontent-ams2-1.cdninstagram.com/1.jpg", "width": 10, "height": 10}
    video_b = {"is_video": True, "image_url": None, "width": None, "height": None}
    photo_c = {"is_video": False, "image_url": "https://scontent-ams2-1.cdninstagram.com/2.jpg", "width": 20, "height": 20}

    # (а) дети [фото, видео, фото], один скачанный видео-файл: фото-файлов нет,
    #     а фото-детей 2 → mismatch-политика (T2-2): видео как есть, фото с API
    #     в конец, порядок детей внутри фото-блока сохранён.
    fetched = []
    existing_video = [{"path": "/tmp/nd-existing-video.mp4", "is_video": True, "width": None, "height": None}]
    merged_a = merge_carousel_media([photo_a, video_b, photo_c], existing_video, _make_fetch_ok(merge_dir, fetched))
    check(len(merged_a) == 3)
    check([item["is_video"] for item in merged_a] == [True, False, False])
    check(fetched == [0, 1])
    check(merged_a[0]["path"] == "/tmp/nd-existing-video.mp4")
    check(merged_a[1]["width"] == 10 and merged_a[2]["width"] == 20)

    # (б) rate-guard: все дети видео и файлов хватает → fetch_photo не зовётся.
    fetched = []
    video_children = [dict(video_b) for _ in range(3)]
    video_files = [
        {"path": f"/tmp/nd-v{i}.mp4", "is_video": True, "width": None, "height": None}
        for i in range(3)
    ]
    merged_b = merge_carousel_media(video_children, video_files, _make_fetch_ok(merge_dir, fetched))
    check(fetched == [])
    check([item["path"] for item in merged_b] == [f"/tmp/nd-v{i}.mp4" for i in range(3)])

    # (в) сбой: fetch_photo возвращает None и кидает исключение → остальные на местах.
    def _make_flaky(fail_idx, mode):
        base = _make_fetch_ok(merge_dir, [])
        def fetch_flaky(child, idx):
            if idx == fail_idx:
                if mode == "raise":
                    raise RuntimeError("synthetic download failure")
                return None
            return base(child, idx)
        return fetch_flaky

    for mode in ("none", "raise"):
        fetched = []
        merged_c = merge_carousel_media([photo_a, photo_c, photo_a], [], _make_flaky(1, mode))
        check(len(merged_c) == 2)
        check([item["width"] for item in merged_c] == [10, 10])
        check(all(item["path"].startswith(merge_dir) for item in merged_c))

    # (г) видео-детей больше, чем скачанных файлов → лишние пропущены без исключения.
    fetched = []
    merged_d = merge_carousel_media(video_children, video_files[:1], _make_fetch_ok(merge_dir, fetched))
    check(len(merged_d) == 1 and merged_d[0]["path"] == "/tmp/nd-v0.mp4")
    check(merge_carousel_media([], [], _make_fetch_ok(merge_dir, fetched)) == [])
    check(merge_carousel_media(None, None, _make_fetch_ok(merge_dir, fetched)) == [])

# 7. is_allowed_media_host: https + только CDN-хосты Instagram.
check(
    instagram_api.is_allowed_media_host(
        "https://scontent-ams2-1.cdninstagram.com/v/t51.2885-15/x.jpg?efg=1"
    )
)
check(not instagram_api.is_allowed_media_host("http://scontent-ams2-1.cdninstagram.com/x.jpg"))
check(not instagram_api.is_allowed_media_host("https://evil.example.com/x.jpg"))
check(not instagram_api.is_allowed_media_host("https://cdninstagram.com.evil.io/x.jpg"))
check(not instagram_api.is_allowed_media_host(""))
check(not instagram_api.is_allowed_media_host(None))

# 8. Санитайзер: float-expires нормализуется, MozillaCookieJar грузит cleaned-файл.
with tempfile.TemporaryDirectory() as temp_dir:
    raw_file = os.path.join(temp_dir, "cookies.txt")
    with open(raw_file, "w", encoding="utf-8") as handle:
        handle.write("# Netscape HTTP Cookie File\n")
        handle.write(
            ".instagram.com\tTRUE\t/\tTRUE\t1811526512.091545\tsessionid\tsynthetic-sid\n"
        )
        handle.write(".youtube.com\tTRUE\t/\tTRUE\t1893456000\tVISITOR\tmjeste\n")
        handle.write(".example.com\tTRUE\t/\tFALSE\t1912345678.5\tSID\tsynthetic-sid3\n")

    data_dir = os.path.join(temp_dir, "data")
    os.makedirs(data_dir, exist_ok=True)
    original_cookies_file = downloader_module.COOKIES_FILE
    downloader_module.COOKIES_FILE = raw_file
    try:
        instance = downloader_module.VideoDownloader(data_dir=data_dir)
        cleaned = instance.cookiefile
    finally:
        downloader_module.COOKIES_FILE = original_cookies_file

    check(cleaned is not None and cleaned != raw_file)
    with open(cleaned, "r", encoding="utf-8") as handle:
        cookie_lines = [
            line for line in handle.read().splitlines() if line and not line.startswith("#")
        ]
    check(len(cookie_lines) == 3)
    float_expires = [line for line in cookie_lines if "\t1811526512.091545\t" in line or "\t1912345678.5\t" in line]
    check(float_expires == [])
    for line in cookie_lines:
        parts = line.split("\t")
        check(len(parts) == 7)
        check(parts[4].isdigit())

    jar = http.cookiejar.MozillaCookieJar(cleaned)
    jar.load()
    check(len(jar) == 3)

    # Повторный прогон: файл уже чистый — санитайзер не пересоздаёт cleaned.
    downloader_module.COOKIES_FILE = raw_file
    try:
        check(instance.reload_cookies() == cleaned)
    finally:
        downloader_module.COOKIES_FILE = original_cookies_file

# 9. ping_session: таблица статусов (транспорт подменён, сеть не трогаем).
#    200+JSON → True; 401/403/404 → False (мёртвый sid); 0/429/5xx → None
#    (rate-limit/сбой — НЕ смерть кукис, ложный алерт недопустим).
class _FakeTransport:
    def __init__(self, status, payload):
        self.status = status
        self.payload = payload
        self.calls = 0

    def __call__(self, path_query, sessionid, timeout=15.0):
        self.calls += 1
        check(isinstance(sessionid, str) and bool(sessionid))
        return self.status, self.payload


def _ping_with_fake(status, payload):
    fake = _FakeTransport(status, payload)
    original = instagram_api.api_get_status
    instagram_api.api_get_status = fake
    try:
        result = instagram_api.ping_session("synthetic-sid")
    finally:
        instagram_api.api_get_status = original
    check(fake.calls == 1)
    return result


check(_ping_with_fake(200, {"items": [1]}) is True)
check(_ping_with_fake(200, None) is None)
check(_ping_with_fake(404, None) is False)
check(_ping_with_fake(403, None) is False)
check(_ping_with_fake(401, None) is False)
check(_ping_with_fake(429, None) is None)
check(_ping_with_fake(503, None) is None)
check(_ping_with_fake(0, None) is None)

# Без sid — transport не вызывается вовсе.
fake_none = _FakeTransport(200, {"items": [1]})
original_transport = instagram_api.api_get_status
instagram_api.api_get_status = fake_none
try:
    check(instagram_api.ping_session(None) is None)
    check(instagram_api.ping_session("") is None)
finally:
    instagram_api.api_get_status = original_transport
check(fake_none.calls == 0)

# 10. extract_shortcode: хост строго Instagram (FIX-4).
check(instagram_api.extract_shortcode("https://vk.com/p/DdvelkoDCQP/") is None)
check(instagram_api.extract_shortcode("https://evil.com/p/DdvelkoDCQP/") is None)
check(
    instagram_api.extract_shortcode("https://instagr.am/p/DdvelkoDCQP/")
    == "DdvelkoDCQP"
)
check(
    instagram_api.extract_shortcode("https://www.instagram.com/p/DdvelkoDCQP/?igsh=abc")
    == "DdvelkoDCQP"
)

# 11. merge: частичное скачивание видео (FIX-2).
class _CaptureWarnings(logging.Handler):
    def __init__(self):
        super().__init__()
        self.messages = []

    def emit(self, record):
        self.messages.append(record.getMessage())


with tempfile.TemporaryDirectory() as merge_dir2:
    video_child = {"is_video": True, "image_url": None, "width": None, "height": None}
    photo_child = {
        "is_video": False,
        "image_url": "https://scontent-ams2-1.cdninstagram.com/p.jpg",
        "width": 5,
        "height": 5,
    }
    mixed_children = [video_child, photo_child, video_child, photo_child]

    # (а) точный кейс: видео- и фото-файлов ровно по числу детей → интерлив
    #     сохранён, каждый тип берёт свой файл по порядку, API не зовётся (T2-2).
    fetched = []
    two_videos = [
        {"path": "/tmp/nd-a.mp4", "is_video": True, "width": None, "height": None},
        {"path": "/tmp/nd-b.mp4", "is_video": True, "width": None, "height": None},
    ]
    two_photos = [
        {"path": "/tmp/nd-pa.jpg", "is_video": False, "width": None, "height": None},
        {"path": "/tmp/nd-pb.jpg", "is_video": False, "width": None, "height": None},
    ]
    merged_exact = merge_carousel_media(
        mixed_children, two_videos + two_photos, _make_fetch_ok(merge_dir2, fetched)
    )
    check([item["is_video"] for item in merged_exact] == [True, False, True, False])
    check(merged_exact[0]["path"] == "/tmp/nd-a.mp4")
    check(merged_exact[1]["path"] == "/tmp/nd-pa.jpg")
    check(merged_exact[2]["path"] == "/tmp/nd-b.mp4")
    check(merged_exact[3]["path"] == "/tmp/nd-pb.jpg")
    check(fetched == [])

    # (а2) фото-файлов нет, а фото-дети есть → mismatch: видео как есть
    #      (autonumber-порядок), фото с API в конец (T2-2).
    fetched = []
    merged_video_only = merge_carousel_media(
        mixed_children, two_videos, _make_fetch_ok(merge_dir2, fetched)
    )
    check([item["is_video"] for item in merged_video_only] == [True, True, False, False])
    check([item["path"] for item in merged_video_only[:2]] == ["/tmp/nd-a.mp4", "/tmp/nd-b.mp4"])
    check(all(item["path"].startswith(merge_dir2) for item in merged_video_only[2:]))
    check(fetched == [0, 1])

    # (б) mismatch: видео не скачалось → существующий порядок не тронут,
    #     фото дописаны в конец, предупреждение в логе.
    fetched = []
    one_video = [two_videos[0]]
    capture = _CaptureWarnings()
    root_logger = logging.getLogger()
    root_logger.addHandler(capture)
    old_level = root_logger.level
    root_logger.setLevel(logging.WARNING)
    try:
        merged_partial = merge_carousel_media(
            mixed_children, one_video, _make_fetch_ok(merge_dir2, fetched)
        )
    finally:
        root_logger.removeHandler(capture)
        root_logger.setLevel(old_level)
    check(len(merged_partial) == 3)
    check([item["path"] for item in merged_partial[:1]] == ["/tmp/nd-a.mp4"])
    check(merged_partial[0]["is_video"] is True)
    check([item["is_video"] for item in merged_partial[1:]] == [False, False])
    check(fetched == [0, 1])
    check(any("частичное скачивание видео" in m for m in capture.messages))

    # (б2) файлов больше, чем видео-детей → тоже без переупорядочивания.
    check(
        [item["path"] for item in merge_carousel_media(
            [video_child], two_videos, _make_fetch_ok(merge_dir2, [])
        )]
        == ["/tmp/nd-a.mp4", "/tmp/nd-b.mp4"]
    )
shutil.rmtree(merge_dir, ignore_errors=True)

# 12. Редирект-хендлер и кап тела (FIX-1).
class _FakeImageResponse:
    def __init__(self, content):
        self.status = 200
        self._content = content

    def read(self, amount=-1):
        if amount is None or amount < 0:
            return self._content
        return self._content[:amount]

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


class _FakeOpener:
    def __init__(self, content):
        self._content = content
        self.calls = 0

    def open(self, request, timeout=30.0):
        self.calls += 1
        return _FakeImageResponse(self._content)


original_opener = downloader_module._IG_IMAGE_OPENER
redirect_handler = downloader_module._SafeRedirectHandler()
ig_request = urllib.request.Request(
    "https://scontent-ams2-1.cdninstagram.com/v/t51/a.jpg"
)
followed = redirect_handler.redirect_request(
    ig_request, None, 302, "Found", {},
    "https://scontent-ams2-1.cdninstagram.com/v/t51/b.jpg",
)
check(followed is not None)
check(
    followed.full_url == "https://scontent-ams2-1.cdninstagram.com/v/t51/b.jpg"
)
check(
    redirect_handler.redirect_request(
        ig_request, None, 302, "Found", {}, "https://evil.example.com/b.jpg"
    )
    is None
)
check(
    redirect_handler.redirect_request(
        ig_request, None, 302, "Found", {},
        "http://scontent-ams2-1.cdninstagram.com/b.jpg",
    )
    is None
)
check(
    redirect_handler.redirect_request(
        ig_request, None, 302, "Found", {},
        "https://cdninstagram.com.evil.io/b.jpg",
    )
    is None
)

# Кап тела: больше лимита → None, в пределах лимита → содержимое как есть.
image_cap = downloader_module._MAX_INSTAGRAM_IMAGE_BYTES
downloader_module._IG_IMAGE_OPENER = _FakeOpener(b"\xff\xd8\xff" + b"0" * (image_cap + 1))
try:
    check(
        downloader_module._fetch_instagram_image(
            "https://scontent-ams2-1.cdninstagram.com/big.jpg"
        )
        is None
    )
finally:
    downloader_module._IG_IMAGE_OPENER = original_opener
small_opener = _FakeOpener(b"\xff\xd8\xff" + b"0" * 2048)
downloader_module._IG_IMAGE_OPENER = small_opener
try:
    small_body = downloader_module._fetch_instagram_image(
        "https://scontent-ams2-1.cdninstagram.com/small.jpg"
    )
finally:
    downloader_module._IG_IMAGE_OPENER = original_opener
check(small_body is not None and len(small_body) == 2051)
check(small_opener.calls == 1)

# 13. _fetch_instagram_image: InvalidURL/ValueError → None без повтора (FIX-3).
class _RaisingOpener:
    def __init__(self, exc):
        self._exc = exc
        self.calls = 0

    def open(self, request, timeout=30.0):
        self.calls += 1
        raise self._exc


original_sleep = downloader_module.time.sleep
downloader_module.time.sleep = lambda seconds: None
try:
    for raise_exc, expected_calls in (
        (http.client.InvalidURL("synthetic control char"), 1),
        (ValueError("synthetic port"), 1),
        (urllib.error.URLError("synthetic network failure"), 2),
    ):
        raiser = _RaisingOpener(raise_exc)
        downloader_module._IG_IMAGE_OPENER = raiser
        try:
            check(
                downloader_module._fetch_instagram_image(
                    "https://scontent-ams2-1.cdninstagram.com/x.jpg"
                )
                is None
            )
        finally:
            downloader_module._IG_IMAGE_OPENER = original_opener
        check(raiser.calls == expected_calls)
    # Реальный URL с control-символом — отклонён до сети, без исключения.
    check(
        downloader_module._fetch_instagram_image(
            "https://scontent-ams2-1.cdninstagram.com/a.jpg\x01?x=1"
        )
        is None
    )
finally:
    downloader_module.time.sleep = original_sleep

# 14. download_instagram_photos: плохой URL не валит пакет (FIX-3).
MIXED_POST_FIXTURE = {
    "media_type": 8,
    "carousel_media": [
        {
            "media_type": 1,
            "image_versions2": {"candidates": [{
                "url": "https://[::1/x.jpg",
                "width": 10,
                "height": 10,
            }]},
        },
        {
            "media_type": 2,
            "image_versions2": {"candidates": [{
                "url": "https://scontent-ams2-1.cdninstagram.com/cover.jpg",
            }]},
            "video_versions": [{"url": "https://scontent-ams2-1.cdninstagram.com/v.mp4"}],
        },
        {
            "media_type": 1,
            "image_versions2": {"candidates": [{
                "url": "https://scontent-ams2-1.cdninstagram.com/good.jpg",
                "width": 20,
                "height": 20,
            }]},
        },
    ],
}

with tempfile.TemporaryDirectory() as temp_dir2:
    data_dir2 = os.path.join(temp_dir2, "data")
    os.makedirs(data_dir2, exist_ok=True)
    original_cookies_file = downloader_module.COOKIES_FILE
    downloader_module.COOKIES_FILE = None
    try:
        instance = downloader_module.VideoDownloader(data_dir=data_dir2)
    finally:
        downloader_module.COOKIES_FILE = original_cookies_file

    original_load_sessionid = instagram_api.load_sessionid
    original_fetch_media_info = instagram_api.fetch_media_info
    original_fetch_image = downloader_module._fetch_instagram_image
    instagram_api.load_sessionid = lambda cookiefile: "synthetic-sid"
    instagram_api.fetch_media_info = lambda url, sessionid: MIXED_POST_FIXTURE

    def _fake_fetch_image(url, timeout=30.0):
        check(url.endswith("good.jpg"))
        return b"\xff\xd8\xff" + b"0" * 2048

    downloader_module._fetch_instagram_image = _fake_fetch_image
    try:
        photo_media = instance.download_instagram_photos(
            "https://www.instagram.com/p/DdvelkoDCQP/"
        )
    finally:
        instagram_api.load_sessionid = original_load_sessionid
        instagram_api.fetch_media_info = original_fetch_media_info
        downloader_module._fetch_instagram_image = original_fetch_image

    check(len(photo_media) == 1)
    check(photo_media[0]["path"].endswith("ig001.jpg"))
    check(os.path.isdir(os.path.dirname(photo_media[0]["path"])))
    check(os.path.getsize(photo_media[0]["path"]) > 1024)
    # Control-символ в URL: пропуск без исключения.
    check(
        instance._download_instagram_photo(
            {
                "is_video": False,
                "image_url": "https://scontent-ams2-1.cdninstagram.com/c.jpg\x01x",
                "width": None,
                "height": None,
            },
            data_dir2,
            0,
        )
        is None
    )

# 15. Санитайзер: мусорный expires → строка дропнута, cleaned грузится jar-ом (FIX-5).
with tempfile.TemporaryDirectory() as temp_dir3:
    raw_mixed = os.path.join(temp_dir3, "cookies_mixed.txt")
    with open(raw_mixed, "w", encoding="utf-8") as handle:
        handle.write("# Netscape HTTP Cookie File\n")
        handle.write(
            ".instagram.com\tTRUE\t/\tTRUE\t1811526512.091545\tsessionid\tsynthetic-a\n"
        )
        handle.write(".example.com\tTRUE\t/\tTRUE\tмусор\tSID\tsynthetic-b\n")
        handle.write(".youtube.com\tTRUE\t/\tTRUE\t1893456000\tVISITOR\tsynthetic-c\n")

    data_dir3 = os.path.join(temp_dir3, "data")
    os.makedirs(data_dir3, exist_ok=True)
    original_cookies_file = downloader_module.COOKIES_FILE
    downloader_module.COOKIES_FILE = raw_mixed
    try:
        instance_mixed = downloader_module.VideoDownloader(data_dir=data_dir3)
        cleaned_mixed = instance_mixed.cookiefile
    finally:
        downloader_module.COOKIES_FILE = original_cookies_file

    check(cleaned_mixed is not None and cleaned_mixed != raw_mixed)
    jar_mixed = http.cookiejar.MozillaCookieJar(cleaned_mixed)
    jar_mixed.load()
    check(len(jar_mixed) == 2)
    with open(cleaned_mixed, "r", encoding="utf-8") as handle:
        cleaned_text = handle.read()
    check("synthetic-b" not in cleaned_text)
    check("1811526512.091545" not in cleaned_text)
    check("\t1811526512\t" in cleaned_text)

# 16. _detect_image_ext: все ветки + мусор (FIX-8).
check(downloader_module._detect_image_ext(b"\xff\xd8\xff" + b"0" * 32) == "jpg")
check(downloader_module._detect_image_ext(b"\x89PNG\r\n\x1a\n" + b"0" * 32) == "png")
check(downloader_module._detect_image_ext(b"RIFF" + b"0" * 4 + b"WEBP" + b"0" * 8) == "webp")
check(downloader_module._detect_image_ext(b"0000ftypheic" + b"0" * 8) == "heic")
check(downloader_module._detect_image_ext(b"0000ftypheix" + b"0" * 8) == "heic")
check(downloader_module._detect_image_ext(b"0000ftypmif1" + b"0" * 8) == "heic")
check(downloader_module._detect_image_ext(b"GIF89a" + b"0" * 32) is None)
check(downloader_module._detect_image_ext(b"") is None)
check(downloader_module._detect_image_ext(b"\xff\xd8") is None)

# 17. api_get_status: HTTP-код без повтора, сетевая ошибка — ровно один повтор (FIX-8).
original_urlopen = urllib.request.urlopen
original_api_sleep = instagram_api.time.sleep
instagram_api.time.sleep = lambda seconds: None
urlopen_counter = {"n": 0}


def _urlopen_http_error(request, timeout=None):
    urlopen_counter["n"] += 1
    raise urllib.error.HTTPError(request.full_url, 404, "Not Found", {}, io.BytesIO(b""))


urllib.request.urlopen = _urlopen_http_error
try:
    status_value, payload_value = instagram_api.api_get_status(
        "/api/v1/media/1/info/", "synthetic-sid"
    )
finally:
    urllib.request.urlopen = original_urlopen
check(status_value == 404 and payload_value is None)
check(urlopen_counter["n"] == 1)


def _urlopen_url_error(request, timeout=None):
    urlopen_counter["n"] += 1
    raise urllib.error.URLError("synthetic network failure")


urlopen_counter["n"] = 0
urllib.request.urlopen = _urlopen_url_error
try:
    status_value, payload_value = instagram_api.api_get_status(
        "/api/v1/media/1/info/", "synthetic-sid"
    )
finally:
    urllib.request.urlopen = original_urlopen
    instagram_api.time.sleep = original_api_sleep
check(status_value == 0 and payload_value is None)
check(urlopen_counter["n"] == 2)

# 18. merge_carousel_media: единая политика существующих фото-файлов (T2-2).
with tempfile.TemporaryDirectory() as merge_dir3:
    photo_x = {
        "is_video": False,
        "image_url": "https://scontent-ams2-1.cdninstagram.com/x.jpg",
        "width": 7,
        "height": 7,
    }
    photo_y = {
        "is_video": False,
        "image_url": "https://scontent-ams2-1.cdninstagram.com/y.jpg",
        "width": 9,
        "height": 9,
    }
    video_v = {"is_video": True, "image_url": None, "width": None, "height": None}
    existing_video_file = {
        "path": "/tmp/nd-t2-video.mp4", "is_video": True, "width": None, "height": None,
    }
    existing_video_file2 = {
        "path": "/tmp/nd-t2-video2.mp4", "is_video": True, "width": None, "height": None,
    }
    existing_photo_files = [
        {"path": "/tmp/nd-t2-photo1.jpg", "is_video": False, "width": None, "height": None},
        {"path": "/tmp/nd-t2-photo2.jpg", "is_video": False, "width": None, "height": None},
    ]

    # (а) existing 1 фото + 1 видео, дети [фото, видео], количества сошлись →
    #     точная раскладка: existing-фото использован, fetch_photo НЕ зван.
    fetched = []
    merged_t2a = merge_carousel_media(
        [photo_x, video_v],
        [existing_photo_files[0], existing_video_file],
        _make_fetch_ok(merge_dir3, fetched),
    )
    check(
        [item["path"] for item in merged_t2a]
        == ["/tmp/nd-t2-photo1.jpg", "/tmp/nd-t2-video.mp4"]
    )
    check([item["is_video"] for item in merged_t2a] == [False, True])
    check(fetched == [])

    # (б) existing фото 2 шт, фото-детей 1 → mismatch: видео как есть, фото с
    #     API в конце, existing-фото в merge не попали (задвоения нет).
    fetched = []
    capture_t2 = _CaptureWarnings()
    root_logger_t2 = logging.getLogger()
    root_logger_t2.addHandler(capture_t2)
    old_level_t2 = root_logger_t2.level
    root_logger_t2.setLevel(logging.WARNING)
    try:
        merged_t2b = merge_carousel_media(
            [video_v, photo_x],
            [existing_video_file] + existing_photo_files,
            _make_fetch_ok(merge_dir3, fetched),
        )
    finally:
        root_logger_t2.removeHandler(capture_t2)
        root_logger_t2.setLevel(old_level_t2)
    check(fetched == [0])
    check(len(merged_t2b) == 2)
    check(merged_t2b[0]["path"] == "/tmp/nd-t2-video.mp4")
    check(merged_t2b[1]["path"].startswith(merge_dir3))
    check(
        all(
            item["path"] not in ("/tmp/nd-t2-photo1.jpg", "/tmp/nd-t2-photo2.jpg")
            for item in merged_t2b
        )
    )
    check(any("частичное скачивание" in m for m in capture_t2.messages))

    # (в) интерлив на двух видео и двух фото: каждый тип берёт свои файлы
    #     по порядку при вперемешку идущих детях.
    fetched = []
    merged_t2c = merge_carousel_media(
        [photo_x, video_v, photo_y, video_v],
        [existing_video_file, existing_video_file2] + existing_photo_files,
        _make_fetch_ok(merge_dir3, fetched),
    )
    check(
        [item["path"] for item in merged_t2c]
        == [
            "/tmp/nd-t2-photo1.jpg",
            "/tmp/nd-t2-video.mp4",
            "/tmp/nd-t2-photo2.jpg",
            "/tmp/nd-t2-video2.mp4",
        ]
    )
    check(fetched == [])


# 19. download_carousel: пустой результат не оставляет carousel_* в data_dir
#     (T2-1/T2-7); непустой media — каталог жив (его чистит вызывающий).
def _make_fake_ydl(playlist_count: int, files_to_write: int):
    class _FakeYdl:
        def __init__(self, opts):
            self._opts = opts

        def __enter__(self):
            return self

        def __exit__(self, *exc_info):
            return False

        def extract_info(self, url, download=True):
            if download and files_to_write:
                work_dir = os.path.dirname(self._opts["outtmpl"])
                for index in range(files_to_write):
                    with open(os.path.join(work_dir, f"{index + 1:03d}.jpg"), "wb") as handle:
                        handle.write(b"\xff\xd8\xff" + b"0" * 64)
            return {
                "_type": "playlist",
                "playlist_count": playlist_count,
                "entries": [None] * playlist_count,
            }

    return _FakeYdl


with tempfile.TemporaryDirectory() as temp_dir5:
    data_dir5 = os.path.join(temp_dir5, "data")
    os.makedirs(data_dir5, exist_ok=True)
    original_cookies_file = downloader_module.COOKIES_FILE
    downloader_module.COOKIES_FILE = None
    try:
        instance5 = downloader_module.VideoDownloader(data_dir=data_dir5)
    finally:
        downloader_module.COOKIES_FILE = original_cookies_file

    original_ydl = downloader_module.YoutubeDL
    original_load = instagram_api.load_sessionid
    original_fetch_info = instagram_api.fetch_media_info

    def _no_carousel_dirs():
        return [n for n in os.listdir(data_dir5) if n.startswith("carousel_")]

    # (а) «медиа недоступно через API» (fetch_media_info → None) → media пуст,
    #     каталог удалён (утечки carousel_* нет).
    instagram_api.load_sessionid = lambda cookiefile: "synthetic-sid"
    instagram_api.fetch_media_info = lambda url, sessionid: None
    downloader_module.YoutubeDL = _make_fake_ydl(playlist_count=3, files_to_write=0)
    try:
        media_leak, info_leak = instance5.download_carousel(
            "https://www.instagram.com/p/DdvelkoDCQP/"
        )
    finally:
        downloader_module.YoutubeDL = original_ydl
        instagram_api.load_sessionid = original_load
        instagram_api.fetch_media_info = original_fetch_info
    check(media_leak == [])
    check(info_leak.get("playlist_count") == 3)
    check(_no_carousel_dirs() == [])

    # (б) «sessionid не найден» → media пуст, каталог удалён, API не зван.
    api_calls: list[str] = []
    instagram_api.load_sessionid = lambda cookiefile: None

    def _must_not_call(url, sessionid):
        api_calls.append(url)
        return None

    instagram_api.fetch_media_info = _must_not_call
    downloader_module.YoutubeDL = _make_fake_ydl(playlist_count=2, files_to_write=0)
    try:
        media_nosid, _info_nosid = instance5.download_carousel(
            "https://www.instagram.com/p/DdvelkoDCQP/"
        )
    finally:
        downloader_module.YoutubeDL = original_ydl
        instagram_api.load_sessionid = original_load
        instagram_api.fetch_media_info = original_fetch_info
    check(media_nosid == [])
    check(api_calls == [])
    check(_no_carousel_dirs() == [])

    # (в) media непустой (yt-dlp отдал файл) → каталог жив, API не зван
    #     (rate-guard: полная раскладка не дёргает приватный API).
    api_calls = []
    instagram_api.fetch_media_info = _must_not_call
    downloader_module.YoutubeDL = _make_fake_ydl(playlist_count=1, files_to_write=1)
    try:
        media_ok, _info_ok = instance5.download_carousel(
            "https://www.instagram.com/p/DdvelkoDCQP/"
        )
    finally:
        downloader_module.YoutubeDL = original_ydl
        instagram_api.fetch_media_info = original_fetch_info
    check(len(media_ok) == 1)
    check(media_ok[0]["is_video"] is False)
    check(os.path.dirname(media_ok[0]["path"]).startswith(data_dir5))
    check(os.path.isdir(os.path.dirname(media_ok[0]["path"])))
    check(api_calls == [])
    shutil.rmtree(os.path.dirname(media_ok[0]["path"]), ignore_errors=True)

# 20. HEIC не сохраняем и не отправляем (T2-5); webp остаётся.
with tempfile.TemporaryDirectory() as temp_dir6:
    data_dir6 = os.path.join(temp_dir6, "data")
    os.makedirs(data_dir6, exist_ok=True)
    original_cookies_file = downloader_module.COOKIES_FILE
    downloader_module.COOKIES_FILE = None
    try:
        instance6 = downloader_module.VideoDownloader(data_dir=data_dir6)
    finally:
        downloader_module.COOKIES_FILE = original_cookies_file

    original_fetch_image = downloader_module._fetch_instagram_image
    heic_child = {
        "is_video": False,
        "image_url": "https://scontent-ams2-1.cdninstagram.com/h.heic",
        "width": 5,
        "height": 5,
    }

    downloader_module._fetch_instagram_image = (
        lambda url, timeout=30.0: b"0000ftypheic" + b"0" * 2048
    )
    try:
        check(instance6._download_instagram_photo(heic_child, data_dir6, 0) is None)
        check(
            instance6._download_instagram_photo(
                dict(heic_child, image_url="https://scontent-ams2-1.cdninstagram.com/h.mif1"),
                data_dir6,
                1,
            )
            is None
        )
    finally:
        downloader_module._fetch_instagram_image = original_fetch_image
    check([n for n in os.listdir(data_dir6) if n.startswith("ig")] == [])

    downloader_module._fetch_instagram_image = (
        lambda url, timeout=30.0: b"RIFF" + b"0" * 4 + b"WEBP" + b"0" * 2048
    )
    try:
        webp_photo = instance6._download_instagram_photo(heic_child, data_dir6, 2)
    finally:
        downloader_module._fetch_instagram_image = original_fetch_image
    check(webp_photo is not None and webp_photo["path"].endswith("ig002.webp"))
    check(os.path.getsize(webp_photo["path"]) > 1024)

# 21. load_sessionid: непечатаемое значение — не кандидат (T2-6).
with tempfile.TemporaryDirectory() as temp_dir7:
    ctrl_file = os.path.join(temp_dir7, "cookies_ctrl.txt")
    with open(ctrl_file, "w", encoding="utf-8") as handle:
        handle.write("# Netscape HTTP Cookie File\n")
        # NUL/вертикальная табуляция внутри значения: такая строка имеет самый
        # свежий expires, но http.client бросил бы ValueError при сборке
        # Cookie-заголовка — значение не кандидат, валидная строка ниже выигрывает.
        handle.write(
            ".instagram.com\tTRUE\t/\tTRUE\t9999999999\tsessionid\tsyn\x00thetic\n"
        )
        handle.write(
            ".instagram.com\tTRUE\t/\tTRUE\t1000000000\tsessionid\tsynthetic-valid\n"
        )
    check(instagram_api.load_sessionid(ctrl_file) == "synthetic-valid")

    only_ctrl_file = os.path.join(temp_dir7, "cookies_only_ctrl.txt")
    with open(only_ctrl_file, "w", encoding="utf-8") as handle:
        handle.write("# Netscape HTTP Cookie File\n")
        handle.write(
            ".instagram.com\tTRUE\t/\tTRUE\t9999999999\tsessionid\tsyn\x0brhetic\n"
        )
    check(instagram_api.load_sessionid(only_ctrl_file) is None)

print(f"TESTS OK: {checks} проверок")
