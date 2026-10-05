"""Standalone-проверки мержа IG session-кукис после write-back yt-dlp (офлайн).

Кукис в файлах — только синтетические (значения вида synthetic-sid-...),
сеть не трогаем. Проверяется cookie-механика app.downloader:
_is_instagram_session_cookie, _read_instagram_session_lines,
restore_instagram_session_cookies.

Семантика снапшота/мержа — PLAIN: строка с префиксом `#HttpOnly_` попадает
в merged-файл БЕЗ префикса. Причина: потребители (app.instagram_api.load_sessionid,
app.cookie_monitor._instagram_sessionid_expired) пропускают `#`-строки, и
вербатим-строка была бы для них невидима (алерты продолжались бы).
Интеграционные пробы — именно на этих потребителях.
"""

import http.cookiejar
import os
import stat
import sys
import tempfile
import threading

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

import app.downloader as downloader_module
from app.cookie_monitor import _instagram_sessionid_expired
from app.downloader import (
    VideoDownloader,
    _is_instagram_session_cookie,
    _read_instagram_session_lines,
    _session_cookie_key,
    restore_instagram_session_cookies,
)
from app.instagram_api import load_sessionid

checks = 0


def check(condition: bool) -> None:
    global checks
    assert condition
    checks += 1


def _write_netscape(path: str, lines: list[str]) -> None:
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("# Netscape HTTP Cookie File\n")
        handle.writelines(line + "\n" for line in lines)


def _read_lines(path: str) -> list[str]:
    with open(path, "r", encoding="utf-8") as handle:
        return [line.rstrip("\n") for line in handle]


def _sid_line(
    expires: str,
    domain: str = ".instagram.com",
    name: str = "sessionid",
    value: str = "synthetic-sid",
    httponly: bool = False,
) -> str:
    prefix = "#HttpOnly_" if httponly else ""
    return f"{prefix}{domain}\tTRUE\t/\tTRUE\t{expires}\t{name}\t{value}"


_YT_LINE = ".youtube.com\tTRUE\t/\tTRUE\t1999999999\tVISITOR_INFO\tsynthetic-yt"


# 1. Предикат _is_instagram_session_cookie: прямая таблица позитив/негатив.
for domain in (
    "instagram.com",
    ".instagram.com",
    "www.instagram.com",
    ".www.instagram.com",
    "i.instagram.com",
    "#HttpOnly_.instagram.com",
    "#HttpOnly_instagram.com",
    "instagr.am",
    ".instagr.am",
    "#HttpOnly_.instagr.am",
    ".INSTAGRAM.COM",
):
    check(_is_instagram_session_cookie(domain, "sessionid") is True)

for domain in (
    "instagramcdn.com",
    ".instagramcdn.com",
    "cdninstagram.com",
    "notinstagram.com",
    "fakinstagram.com",
    "instagram.com.evil.com",
    ".instagram.com.evil.com",
    "instagr.am.evil.com",
    "facebook.com",
    "",
):
    check(_is_instagram_session_cookie(domain, "sessionid") is False)

for name in ("ds_user_id", "csrftoken", "sessionid_sig", "shbid", "SessionId", ""):
    check(_is_instagram_session_cookie(".instagram.com", name) is False)


# 2. Главный кейс ТЗ: write-back вымыл sessionid — мерж возвращает plain-строку.
with tempfile.TemporaryDirectory() as temp_dir:
    httponly_sid = _sid_line("1999999999.5", value="synthetic-sid-A", httponly=True)
    plain_sid = _sid_line("1999999999", value="synthetic-sid-A")
    ref_path = os.path.join(temp_dir, "ref.txt")
    _write_netscape(ref_path, [httponly_sid, _YT_LINE])

    reference = _read_instagram_session_lines(ref_path)
    check(reference == [plain_sid])

    washed_path = os.path.join(temp_dir, "washed.txt")
    _write_netscape(washed_path, [_YT_LINE])
    inode_before = os.stat(washed_path).st_ino

    check(restore_instagram_session_cookies(washed_path, reference) == 1)
    merged_lines = _read_lines(washed_path)
    check(merged_lines == ["# Netscape HTTP Cookie File", _YT_LINE, plain_sid])

    # Append-only мерж: файл НЕ пересоздаётся — inode сохраняется
    # (никаких tmp+os.replace, чужой параллельный save не осиротеет).
    check(os.stat(washed_path).st_ino == inode_before)

    # Главный позитив: строка sessionid в merged-файле БЕЗ `#`-префикса,
    # 7 полей, value равен эталонному, expires нормализован (1999999999.5 → 1999999999).
    sid_merged = merged_lines[-1]
    check(not sid_merged.startswith("#"))
    check(len(sid_merged.split("\t")) == 7)
    check(sid_merged.split("\t")[6] == "synthetic-sid-A")
    check(sid_merged.split("\t")[4] == "1999999999")

    # НОВЫЕ интеграционные пробы главных потребителей (ради них и снимается префикс).
    check(load_sessionid(washed_path) == "synthetic-sid-A")
    check(_instagram_sessionid_expired(washed_path) is False)

    jar = http.cookiejar.MozillaCookieJar(washed_path)
    jar.load()
    check(sorted(cookie.name for cookie in jar) == ["VISITOR_INFO", "sessionid"])
    # Мусорных файлов в каталоге прибавиться не должно (мерж без tmp).
    check(sorted(os.listdir(temp_dir)) == ["ref.txt", "washed.txt"])


# 3. Позитив: sessionid без HttpOnly-префикса восстанавливается как есть.
with tempfile.TemporaryDirectory() as temp_dir:
    plain_sid = _sid_line("1999999999", value="synthetic-sid-B")
    ref_path = os.path.join(temp_dir, "ref.txt")
    _write_netscape(ref_path, [plain_sid, _YT_LINE])
    reference = _read_instagram_session_lines(ref_path)
    check(reference == [plain_sid])

    washed_path = os.path.join(temp_dir, "washed.txt")
    _write_netscape(washed_path, [_YT_LINE])
    check(restore_instagram_session_cookies(washed_path, reference) == 1)
    check(_read_lines(washed_path) == ["# Netscape HTTP Cookie File", _YT_LINE, plain_sid])


# 4. Позитив: несколько IG-доменов — восстанавливаются все отсутствующие.
with tempfile.TemporaryDirectory() as temp_dir:
    sid_main = _sid_line("1999999999", domain=".instagram.com", value="synthetic-sid-C1")
    sid_www = _sid_line("1999999999", domain=".www.instagram.com", value="synthetic-sid-C2")
    washed_path = os.path.join(temp_dir, "washed.txt")
    _write_netscape(washed_path, [_YT_LINE])
    check(restore_instagram_session_cookies(washed_path, [sid_main, sid_www]) == 2)
    check(_read_lines(washed_path) == ["# Netscape HTTP Cookie File", _YT_LINE, sid_main, sid_www])


# 5. Позитив: float-expires нормализуется как у санитайзера (int(float)).
with tempfile.TemporaryDirectory() as temp_dir:
    float_sid = _sid_line("1999999999.5", value="synthetic-sid-D")
    ref_path = os.path.join(temp_dir, "ref.txt")
    _write_netscape(ref_path, [float_sid])
    reference = _read_instagram_session_lines(ref_path)
    check(reference == [_sid_line("1999999999", value="synthetic-sid-D")])

    washed_path = os.path.join(temp_dir, "washed.txt")
    _write_netscape(washed_path, [_YT_LINE])
    check(restore_instagram_session_cookies(washed_path, reference) == 1)
    check(_read_lines(washed_path)[-1].endswith("\t1999999999\tsessionid\tsynthetic-sid-D"))


# 6. Негатив: похожие домены (cdn/подделки) — НЕ IG session, не мержим.
with tempfile.TemporaryDirectory() as temp_dir:
    similar_lines = [
        _sid_line("1999999999", domain=".instagramcdn.com", value="synthetic-sid-E1"),
        _sid_line("1999999999", domain="notinstagram.com", value="synthetic-sid-E2"),
        _sid_line("1999999999", domain="instagram.com.evil.com", value="synthetic-sid-E3"),
    ]
    ref_path = os.path.join(temp_dir, "ref.txt")
    _write_netscape(ref_path, similar_lines)
    check(_read_instagram_session_lines(ref_path) == [])

    washed_path = os.path.join(temp_dir, "washed.txt")
    _write_netscape(washed_path, [_YT_LINE])
    before = _read_lines(washed_path)
    check(restore_instagram_session_cookies(washed_path, similar_lines) == 0)
    check(_read_lines(washed_path) == before)


# 7. Негатив: чужая платформа (sessionid на youtube.com) — не мержим.
with tempfile.TemporaryDirectory() as temp_dir:
    ref_path = os.path.join(temp_dir, "ref.txt")
    _write_netscape(
        ref_path, [_sid_line("1999999999", domain=".youtube.com", value="synthetic-sid-F")]
    )
    check(_read_instagram_session_lines(ref_path) == [])

    washed_path = os.path.join(temp_dir, "washed.txt")
    _write_netscape(washed_path, [_YT_LINE])
    before = _read_lines(washed_path)
    check(
        restore_instagram_session_cookies(
            washed_path, [_sid_line("1999999999", domain=".youtube.com", value="synthetic-sid-F")]
        )
        == 0
    )
    check(_read_lines(washed_path) == before)


# 8. Негатив: чужое имя кукис на IG-домене (скоуп строго sessionid).
with tempfile.TemporaryDirectory() as temp_dir:
    other_names = [
        _sid_line("1999999999", name="ds_user_id", value="synthetic-uid"),
        _sid_line("1999999999", name="csrftoken", value="synthetic-csrf"),
    ]
    ref_path = os.path.join(temp_dir, "ref.txt")
    _write_netscape(ref_path, other_names)
    check(_read_instagram_session_lines(ref_path) == [])

    washed_path = os.path.join(temp_dir, "washed.txt")
    _write_netscape(washed_path, [_YT_LINE])
    before = _read_lines(washed_path)
    check(restore_instagram_session_cookies(washed_path, other_names) == 0)
    check(_read_lines(washed_path) == before)


# 9. Негатив: idempotentность — повторный restore ничего не дописывает.
with tempfile.TemporaryDirectory() as temp_dir:
    sid = _sid_line("1999999999", value="synthetic-sid-H")
    washed_path = os.path.join(temp_dir, "washed.txt")
    _write_netscape(washed_path, [_YT_LINE])
    check(restore_instagram_session_cookies(washed_path, [sid]) == 1)
    after_first = open(washed_path, "rb").read()
    check(restore_instagram_session_cookies(washed_path, [sid]) == 0)
    check(open(washed_path, "rb").read() == after_first)


# 10. Негатив: нет эталона / нет cookiefile — no-op без падения.
with tempfile.TemporaryDirectory() as temp_dir:
    washed_path = os.path.join(temp_dir, "washed.txt")
    _write_netscape(washed_path, [_YT_LINE])
    before = open(washed_path, "rb").read()
    check(restore_instagram_session_cookies(washed_path, []) == 0)
    check(restore_instagram_session_cookies(None, [_sid_line("1999999999")]) == 0)
    check(open(washed_path, "rb").read() == before)


# 11. Негатив: битый washed-файл — restore не падает, sessionid дописан.
with tempfile.TemporaryDirectory() as temp_dir:
    sid = _sid_line("1999999999", value="synthetic-sid-I")
    washed_path = os.path.join(temp_dir, "broken.txt")
    _write_netscape(washed_path, ["broken\tline\tthree", "", "just-one-field"])
    check(restore_instagram_session_cookies(washed_path, [sid]) == 1)
    lines = _read_lines(washed_path)
    check(lines[-1] == sid)
    check(lines[:-1] == ["# Netscape HTTP Cookie File", "broken\tline\tthree", "", "just-one-field"])


# 12. Мусорный expires в эталоне — строка пропущена, restore не упал.
with tempfile.TemporaryDirectory() as temp_dir:
    ref_path = os.path.join(temp_dir, "ref.txt")
    _write_netscape(
        ref_path,
        [
            _sid_line("garbage", value="synthetic-sid-J"),
            _sid_line("1999999999", value="synthetic-sid-J2"),
        ],
    )
    reference = _read_instagram_session_lines(ref_path)
    check(reference == [_sid_line("1999999999", value="synthetic-sid-J2")])

    washed_path = os.path.join(temp_dir, "washed.txt")
    _write_netscape(washed_path, [_YT_LINE])
    check(restore_instagram_session_cookies(washed_path, reference) == 1)


# 13. Снапшот: недоступный файл — пустой список, не исключение.
with tempfile.TemporaryDirectory() as temp_dir:
    check(_read_instagram_session_lines(os.path.join(temp_dir, "missing.txt")) == [])


# 14. Смоук поточной безопасности: 4 потока × 20 restore на один файл.
with tempfile.TemporaryDirectory() as temp_dir:
    shared_path = os.path.join(temp_dir, "shared.txt")
    _write_netscape(shared_path, [_YT_LINE])
    reference = [_sid_line("1999999999", value="synthetic-sid-K")]
    errors: list[Exception] = []

    def _worker() -> None:
        try:
            for _ in range(20):
                restore_instagram_session_cookies(shared_path, reference)
        except Exception as exc:  # noqa: BLE001 — собираем для проверки в main-потоке
            errors.append(exc)

    threads = [threading.Thread(target=_worker) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    check(not errors)
    jar = http.cookiejar.MozillaCookieJar(shared_path)
    jar.load()
    sids = [cookie for cookie in jar if cookie.name == "sessionid"]
    check(len(sids) == 1)
    # Append-only гонки: строк sessionid в файле после 80 restore не больше,
    # чем уникальных ключей + 1 — дописывание по ключу дублей не плодит.
    with open(shared_path, "r", encoding="utf-8") as handle:
        raced_lines = [line.rstrip("\n") for line in handle]
    raced_keys = {
        key for key in (_session_cookie_key(line) for line in raced_lines)
        if key is not None
    }
    raced_sid_count = sum(
        1
        for line in raced_lines
        if len(line.split("\t")) == 7 and line.split("\t")[5] == "sessionid"
    )
    check(raced_sid_count <= len(raced_keys) + 1)
    check(sorted(os.listdir(temp_dir)) == ["shared.txt"])


# 15. Интеграция: у VideoDownloader есть обёртка мержа (call-site смоук).
check(hasattr(VideoDownloader, "_restore_instagram_session"))


# 16. Прямые пробы читателя: эталонная #HttpOnly_-строка → plain-вариант в
#     списке; instagr.am попадает, похожие домены и чужие имена — нет.
with tempfile.TemporaryDirectory() as temp_dir:
    ref_path = os.path.join(temp_dir, "ref.txt")
    _write_netscape(
        ref_path,
        [
            _sid_line("1999999999", value="synthetic-sid-L", httponly=True),
            _sid_line("1999999999", domain="instagr.am", value="synthetic-sid-M"),
            _sid_line("1999999999", domain=".instagramcdn.com", value="synthetic-sid-N"),
            _sid_line("1999999999", domain="notinstagram.com", value="synthetic-sid-O"),
            _sid_line("1999999999", domain="instagram.com.evil.com", value="synthetic-sid-P"),
            _sid_line("1999999999", domain=".facebook.com", value="synthetic-sid-Q"),
            _sid_line("1999999999", name="ds_user_id", value="synthetic-sid-R"),
            _sid_line("1999999999", name="csrftoken", value="synthetic-sid-S"),
        ],
    )
    check(_read_instagram_session_lines(ref_path) == [
        _sid_line("1999999999", value="synthetic-sid-L"),
        _sid_line("1999999999", domain="instagr.am", value="synthetic-sid-M"),
    ])


# 17. Append-only: права credentials-файла не сбрасываются (tmp+replace с
#     umask-правами убраны), режим 0600 переживает мерж.
with tempfile.TemporaryDirectory() as temp_dir:
    sid = _sid_line("1999999999", value="synthetic-sid-T")
    washed_path = os.path.join(temp_dir, "washed.txt")
    _write_netscape(washed_path, [_YT_LINE])
    os.chmod(washed_path, 0o600)
    inode_before = os.stat(washed_path).st_ino
    check(restore_instagram_session_cookies(washed_path, [sid]) == 1)
    check(stat.S_IMODE(os.stat(washed_path).st_mode) == 0o600)
    check(os.stat(washed_path).st_ino == inode_before)
    check(_read_lines(washed_path) == ["# Netscape HTTP Cookie File", _YT_LINE, sid])


# 18. Append-only: файл без завершающего \n — добавленная строка не склеена
#     с последней (prepend одного \n), повторный restore — 0.
with tempfile.TemporaryDirectory() as temp_dir:
    sid = _sid_line("1999999999", value="synthetic-sid-U")
    washed_path = os.path.join(temp_dir, "washed.txt")
    with open(washed_path, "w", encoding="utf-8") as handle:
        handle.write("# Netscape HTTP Cookie File\n")
        handle.write(_YT_LINE)  # намеренно без завершающего \n
    check(restore_instagram_session_cookies(washed_path, [sid]) == 1)
    check(_read_lines(washed_path) == ["# Netscape HTTP Cookie File", _YT_LINE, sid])
    after_first = open(washed_path, "rb").read()
    check(restore_instagram_session_cookies(washed_path, [sid]) == 0)
    check(open(washed_path, "rb").read() == after_first)


# 19. Хелпер ключа: одна кукиса в разных текстовых формах даёт один ключ;
#     не-7-полевые строки и комментарии — None.
check(
    _session_cookie_key(
        "#HttpOnly_.Instagram.com\tTRUE\t/\tTRUE\t1999999999\tsessionid\tsynthetic-k1"
    )
    == ("instagram.com", "/", "sessionid")
)
check(
    _session_cookie_key("instagram.com\tTRUE\t/\tTRUE\t1999999999\tsessionid\tsynthetic-k2")
    == ("instagram.com", "/", "sessionid")
)
check(
    _session_cookie_key(".instagr.am\tTRUE\t/profile\tFALSE\t1999999999\tsessionid\tsynthetic-k3")
    == ("instagr.am", "/profile", "sessionid")
)
check(_session_cookie_key("broken\tline\tthree") is None)
check(_session_cookie_key("# Netscape HTTP Cookie File") is None)
check(_session_cookie_key("") is None)


# 20. Key-match: в файле уже СВЕЖАЯ строка той же кукисы в другой форме
#     (#HttpOnly_), в эталоне — устаревшая plain: restore = 0, устаревшее
#     значение поверх свежего НЕ дописывается, файл байт в байт тот же.
with tempfile.TemporaryDirectory() as temp_dir:
    fresh_httponly = _sid_line("2099999999", value="synthetic-fresh-sid", httponly=True)
    stale_plain = _sid_line("1999999999", value="synthetic-sid-A")
    washed_path = os.path.join(temp_dir, "washed.txt")
    _write_netscape(washed_path, [_YT_LINE, fresh_httponly])
    before = open(washed_path, "rb").read()
    check(restore_instagram_session_cookies(washed_path, [stale_plain]) == 0)
    check(open(washed_path, "rb").read() == before)


# 21. Key-match: домен нормализуется (instagr.am == .instagr.am) — та же
#     кукиса в файле в другой форме блокирует дописывание (restore = 0).
with tempfile.TemporaryDirectory() as temp_dir:
    ref_line = _sid_line("1999999999", domain="instagr.am", value="synthetic-sid-X")
    file_line = _sid_line(
        "2099999999", domain=".instagr.am", value="synthetic-sid-Y", httponly=True
    )
    washed_path = os.path.join(temp_dir, "washed.txt")
    _write_netscape(washed_path, [_YT_LINE, file_line])
    before = open(washed_path, "rb").read()
    check(restore_instagram_session_cookies(washed_path, [ref_line]) == 0)
    check(open(washed_path, "rb").read() == before)


# 22. Дедуп эталона по ключу: дубликаты той же кукисы в reference (plain и
#     #HttpOnly_-форма одного ключа) дают одну дописанную строку.
with tempfile.TemporaryDirectory() as temp_dir:
    sid_plain = _sid_line("1999999999", value="synthetic-sid-D1")
    sid_httponly = _sid_line("1999999999", value="synthetic-sid-D1", httponly=True)
    washed_path = os.path.join(temp_dir, "washed.txt")
    _write_netscape(washed_path, [_YT_LINE])
    check(
        restore_instagram_session_cookies(washed_path, [sid_plain, sid_httponly]) == 1
    )
    check(
        _read_lines(washed_path) == ["# Netscape HTTP Cookie File", _YT_LINE, sid_plain]
    )


# 23. Защита снапшота: из СУЩЕСТВУЮЩЕГО непустого файла прочитан 0 session-
#     строк → ровно ОДНО перечитывание; при результате/пустом/битом файле —
#     один снимок, без лишнего чтения и без падения.
with tempfile.TemporaryDirectory() as temp_dir:
    no_sessions_path = os.path.join(temp_dir, "no_sessions.txt")
    _write_netscape(no_sessions_path, [_YT_LINE])
    with_session_path = os.path.join(temp_dir, "with_session.txt")
    _write_netscape(with_session_path, [_YT_LINE, _sid_line("1999999999")])
    missing_path = os.path.join(temp_dir, "missing.txt")
    empty_path = os.path.join(temp_dir, "empty.txt")
    open(empty_path, "w").close()

    original_once = downloader_module._read_instagram_session_lines_once
    snapshots = {"count": 0}

    def _counting_once(path: str) -> list[str]:
        snapshots["count"] += 1
        return original_once(path)

    downloader_module._read_instagram_session_lines_once = _counting_once
    try:
        check(_read_instagram_session_lines(no_sessions_path) == [])
        check(snapshots["count"] == 2)  # второй снимок — защита от среза save
        snapshots["count"] = 0
        check(len(_read_instagram_session_lines(with_session_path)) == 1)
        check(snapshots["count"] == 1)  # результат есть — перечитывать не нужно
        snapshots["count"] = 0
        check(_read_instagram_session_lines(empty_path) == [])
        check(snapshots["count"] == 1)  # пустой файл — перечитывать нечего
        snapshots["count"] = 0
        check(_read_instagram_session_lines(missing_path) == [])
        check(snapshots["count"] == 1)  # файла нет — один снимок, без падения
    finally:
        downloader_module._read_instagram_session_lines_once = original_once


# 24. reload_cookies: старый эталон непустой, новый снапшот пуст (файл
#     существует) → warning и СТАРЫЙ эталон остаётся, защита не выключается;
#     при отсутствующем файле пустой снапшот присваивается как обычно.
with tempfile.TemporaryDirectory() as temp_dir:
    cookies_path = os.path.join(temp_dir, "cookies.txt")
    live_sid = _sid_line("1999999999", value="synthetic-sid-Z")
    _write_netscape(cookies_path, [_YT_LINE, live_sid])
    original_cookies_file = downloader_module.COOKIES_FILE
    downloader_module.COOKIES_FILE = cookies_path
    try:
        downloader = VideoDownloader(temp_dir)
        check(downloader.cookiefile == cookies_path)
        check(downloader._instagram_session_ref == [live_sid])

        # Write-back yt-dlp: файл перезаписан без session-кукис.
        _write_netscape(cookies_path, [_YT_LINE])
        downloader.reload_cookies()
        check(downloader._instagram_session_ref == [live_sid])

        # Файл исчез совсем — защиты держать не на чем, эталон пустеет.
        os.remove(cookies_path)
        downloader.reload_cookies()
        check(downloader._instagram_session_ref == [])
    finally:
        downloader_module.COOKIES_FILE = original_cookies_file


print(f"TESTS OK: {checks} проверок")
