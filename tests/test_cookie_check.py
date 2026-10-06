"""Standalone-проверки кукис-механики (полностью офлайн, сеть не трогаем).

Кукис в файлах — синтетические. Реактивные IG-потребители (парсер строк,
load_sessionid) и транспорт ping_session/probe_media_alive проверяются
с подменой (monkeypatch instagram_api.api_get_status), реальных запросов нет.
Плановый IG-мониторинг демонтирован — монитору покрыта только YouTube-ветка.
"""

import inspect
import os
import sys
import tempfile
import threading

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from app import cookie_monitor, instagram_api
from app import utils as app_utils
from app.cookie_monitor import CookieHealthMonitor

checks = 0


def check(condition: bool) -> None:
    global checks
    assert condition
    checks += 1


def _write_netscape(path: str, lines: list[str]) -> None:
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("# Netscape HTTP Cookie File\n")
        handle.writelines(line + "\n" for line in lines)


def _sid_line(expires: str, domain: str = ".instagram.com", name: str = "sessionid") -> str:
    return f"{domain}\tTRUE\t/\tTRUE\t{expires}\t{name}\tsynthetic-sid"


# 1b. _parse_netscape_cookie_line: общий парсер строки (unit, без файлов) —
#     детектор боевого HttpOnly-формата и нормализация единиц expires.
check(
    instagram_api._parse_netscape_cookie_line(
        ".instagram.com\tTRUE\t/\tTRUE\t1811526512.091545\tsessionid\tsynthetic-sid"
    )
    == (".instagram.com", "sessionid", 1811526512)
)
# Боевой формат 07:05 UTC: префикс срезан, ms-expires 1822766115894 → 1822766115 с.
check(
    instagram_api._parse_netscape_cookie_line(
        "#HttpOnly_.instagram.com\tTRUE\t/\tTRUE\t1822766115894\tsessionid\tsynthetic-sid"
    )
    == (".instagram.com", "sessionid", 1822766115)
)
# Секундное значение ниже порога 1e11 за миллисекунды не принято.
check(
    instagram_api._parse_netscape_cookie_line(
        ".instagram.com\tTRUE\t/\tTRUE\t9999999999\tsessionid\tsynthetic-sid"
    )
    == (".instagram.com", "sessionid", 9999999999)
)
# Граница порога: ровно 1e11 — ещё секунды; строго выше — уже ms.
check(
    instagram_api._parse_netscape_cookie_line(
        f".instagram.com\tTRUE\t/\tTRUE\t{10**11}\tsessionid\tsynthetic-sid"
    )
    == (".instagram.com", "sessionid", 10**11)
)
check(
    instagram_api._parse_netscape_cookie_line(
        f".instagram.com\tTRUE\t/\tTRUE\t{10**11 + 1000}\tsessionid\tsynthetic-sid"
    )
    == (".instagram.com", "sessionid", (10**11 + 1000) // 1000)
)
# float-нотация ms-значений: int(float(...)) точен ниже 2^53.
check(
    instagram_api._parse_netscape_cookie_line(
        "#HttpOnly_.instagram.com\tTRUE\t/\tTRUE\t1e12\tsessionid\tsynthetic-sid"
    )
    == (".instagram.com", "sessionid", 1000000000)
)
check(
    instagram_api._parse_netscape_cookie_line(
        "#HttpOnly_.instagram.com\tTRUE\t/\tTRUE\t1.822766115894e12\tsessionid\tsynthetic-sid"
    )
    == (".instagram.com", "sessionid", 1822766115)
)

# Негативы: прочие `#`-строки — комментарии, пустая строка — мусор.
for junk_line in ("# просто комментарий", "#НеНашиКуки", "#HttpOnly_", ""):
    check(instagram_api._parse_netscape_cookie_line(junk_line) is None)

# Негативы: не ровно 7 полей → None (с префиксом и без, и лишнее поле).
for broken_line in (
    "#HttpOnly_.instagram.com\tTRUE\t/\tTRUE\t999",
    "not-a-netscape-line\twith\tfive\tfields\there",
    ".instagram.com\tTRUE\t/\tTRUE\t999\tsessionid\tsynthetic-sid\textra",
):
    check(instagram_api._parse_netscape_cookie_line(broken_line) is None)

# Нечитаемый expires (abc/inf/1e999/nan): строка валидна как кука,
# expires=None, парсер не рейзит.
for bad_expires in ("abc", "inf", "1e999", "nan"):
    check(
        instagram_api._parse_netscape_cookie_line(
            "#HttpOnly_.instagram.com\tTRUE\t/\tTRUE\t"
            f"{bad_expires}\tsessionid\tsynthetic-sid"
        )
        == (".instagram.com", "sessionid", None)
    )

# 0/отрицательное парсер НЕ решает за вызывающего: возвращаются КАК ЕСТЬ
# (0 и -5) — политика расходится по вызывающим (loader/monitor), раунд 5.
for zeroish_expires, parsed_expires in (("0", 0), ("-5", -5)):
    check(
        instagram_api._parse_netscape_cookie_line(
            "#HttpOnly_.instagram.com\tTRUE\t/\tTRUE\t"
            f"{zeroish_expires}\tsessionid\tsynthetic-sid"
        )
        == (".instagram.com", "sessionid", parsed_expires)
    )


# 1c. HP1–HP7: HttpOnly-формат на уровне читателя load_sessionid. Боевой
#     факт 07:05 UTC: такие строки отсекались как комментарии — читатель
#     был полностью инертен.
def _httponly_sid_line(
    expires: str, value: str = "synthetic-sid", domain: str = ".instagram.com"
) -> str:
    return f"#HttpOnly_{domain}\tTRUE\t/\tTRUE\t{expires}\tsessionid\t{value}"


def _plain_sid_line(
    expires: str, value: str = "synthetic-sid", domain: str = ".instagram.com"
) -> str:
    return f"{domain}\tTRUE\t/\tTRUE\t{expires}\tsessionid\t{value}"


with tempfile.TemporaryDirectory() as temp_dir:
    # HP1 (позитив, боевой формат 06.10.2026): строка с префиксом и ms-expires
    # 1822766115894 (это 2027-10-06 в секундах) видна load_sessionid.
    hp1_live = os.path.join(temp_dir, "hp1_live.txt")
    _write_netscape(hp1_live, [_httponly_sid_line("1822766115894")])
    check(instagram_api.load_sessionid(hp1_live) == "synthetic-sid")

    # HP3 (негатив-похожий): домен-фильтр работает и после срезания префикса.
    hp3_file = os.path.join(temp_dir, "hp3_youtube.txt")
    _write_netscape(
        hp3_file, [_httponly_sid_line("9999999999", domain=".youtube.com")]
    )
    check(instagram_api.load_sessionid(hp3_file) is None)

    # HP4 (негатив): прочие `#`-строки — по-прежнему комментарии, не куки.
    hp4_file = os.path.join(temp_dir, "hp4_comments.txt")
    _write_netscape(hp4_file, ["# просто комментарий", "#НеНашиКуки"])
    check(instagram_api.load_sessionid(hp4_file) is None)

    # HP5 (негатив-похожий): `#HttpOnly_` без домена и с 5 полями → ignored.
    hp5_file = os.path.join(temp_dir, "hp5_malformed.txt")
    _write_netscape(
        hp5_file,
        ["#HttpOnly_", "#HttpOnly_.instagram.com\tTRUE\t/\tTRUE\t999"],
    )
    check(instagram_api.load_sessionid(hp5_file) is None)

    # HP6 (негатив): HttpOnly-строка с мусорным expires пропущена без падения.
    for bad_expires in ("inf", "abc"):
        hp6_file = os.path.join(temp_dir, f"hp6_{bad_expires}.txt")
        _write_netscape(hp6_file, [_httponly_sid_line(bad_expires)])
        check(instagram_api.load_sessionid(hp6_file) is None)

    # HP7 (смешанный файл, разные единицы): «свежайшая» решается по
    #     НОРМАЛИЗОВАННЫМ секундам. Raw-сравнение выбрало бы ms-строку
    #     (1000000123456 > 9999999999), нормализованная — plain
    #     (9999999999 с > 1000000123 с): победитель меняется, тест
    #     детерминирует саму нормализацию.
    hp7_norm = os.path.join(temp_dir, "hp7_norm.txt")
    _write_netscape(
        hp7_norm,
        [
            _plain_sid_line("9999999999", value="synthetic-sid-plain"),
            _httponly_sid_line("1000000123456", value="synthetic-sid-ms"),
        ],
    )
    check(instagram_api.load_sessionid(hp7_norm) == "synthetic-sid-plain")

    # HP7 (обратный случай): ms-строка реально свежее после нормализации —
    #     побеждает она (ms → 1999999999 с против plain 1000000000 с).
    hp7_ms = os.path.join(temp_dir, "hp7_ms.txt")
    _write_netscape(
        hp7_ms,
        [
            _plain_sid_line("1000000000", value="synthetic-sid-plain"),
            _httponly_sid_line("1999999999000", value="synthetic-sid-ms"),
        ],
    )
    check(instagram_api.load_sessionid(hp7_ms) == "synthetic-sid-ms")


# 1d. Регресс прежней политики expires=0/отрицательного (раунд 5): парсер
#     ничего не решает про `<=0` — loader берёт такие строки кандидатами
#     (сессионная кука без срока — легитимный кандидат).
with tempfile.TemporaryDirectory() as temp_dir:
    # R1: файл с ОДНОЙ строкой sessionid expires 0 — loader отдаёт её value
    # (сессионная кука без срока — легитимный кандидат, поведение до
    # раунда 4).
    r1_file = os.path.join(temp_dir, "r1_zero.txt")
    _write_netscape(r1_file, [_plain_sid_line("0", value="synthetic-sid-zero")])
    check(instagram_api.load_sessionid(r1_file) == "synthetic-sid-zero")

    # R2: 0-строка (сначала) проигрывает строке с положительным сроком —
    # loader отдаёт value ВТОРОЙ строки.
    r2_file = os.path.join(temp_dir, "r2_zero_then_fresh.txt")
    _write_netscape(
        r2_file,
        [
            _plain_sid_line("0", value="synthetic-sid-zero"),
            _plain_sid_line("1999999999", value="synthetic-sid-fresh"),
        ],
    )
    check(instagram_api.load_sessionid(r2_file) == "synthetic-sid-fresh")

    # R2 (обратный порядок строк): max-политика — результат тот же.
    r2_rev_file = os.path.join(temp_dir, "r2_fresh_then_zero.txt")
    _write_netscape(
        r2_rev_file,
        [
            _plain_sid_line("1999999999", value="synthetic-sid-fresh"),
            _plain_sid_line("0", value="synthetic-sid-zero"),
        ],
    )
    check(instagram_api.load_sessionid(r2_rev_file) == "synthetic-sid-fresh")


# 2. ping_session: таблица статусов с подменой транспорта.
calls: list[tuple] = []
_response: list[tuple] = []


def _set_response(response: tuple) -> None:
    _response.clear()
    _response.append(response)


def _fake_api_get_status(
    path_query: str, sessionid: str | None, timeout: float = 15.0
) -> tuple[int, dict | None]:
    calls.append((path_query, sessionid))
    return _response[0]


_original_api_get_status = instagram_api.api_get_status
try:
    instagram_api.api_get_status = _fake_api_get_status

    # 200 + JSON → True.
    _set_response((200, {"items": [{"pk": 1949525278281554174}]}))
    check(instagram_api.ping_session("synthetic-sid") is True)
    check(len(calls) == 1)

    # {401,403,404} → False (мёртвая/чужая сессия отвечает HTML).
    for code in (401, 403, 404):
        _set_response((code, None))
        check(instagram_api.ping_session("synthetic-sid") is False)

    # Сетевая неудача → None (это НЕ смерть кукис).
    _set_response((0, None))
    check(instagram_api.ping_session("synthetic-sid") is None)

    # 429/5xx → None (rate-limit/сбой — не смерть кукис, ложный алерт недопустим).
    for code in (429, 503):
        _set_response((code, None))
        check(instagram_api.ping_session("synthetic-sid") is None)

    # sid=None → None, запрос НЕ выполнялся.
    calls.clear()
    _set_response((200, {"items": [{"pk": 1}]}))
    check(instagram_api.ping_session(None) is None)
    check(calls == [])
finally:
    instagram_api.api_get_status = _original_api_get_status


# 2b. probe_media_alive: контентная проба по посту-«яйцу» (транспорт подменён).
try:
    instagram_api.api_get_status = _fake_api_get_status
    calls.clear()

    # P1 (позитив): 200 + непустой items → True; путь запроса — ровно
    # эндпоинт поста-«яйца».
    _set_response((200, {"items": [{"pk": 1}]}))
    check(instagram_api.probe_media_alive("synthetic-sid") is True)
    _egg_path = f"/api/v1/media/{instagram_api.shortcode_to_pk('BsOGulcndj-')}/info/"
    check(calls == [(_egg_path, "synthetic-sid")])

    # P2 (позитив): 401/403/404 → False (боевая сигнатура мёртвых кукис — 404).
    for code in (401, 403, 404):
        _set_response((code, None))
        check(instagram_api.probe_media_alive("synthetic-sid") is False)

    # N1 (негатив-похожий): 200 без разобранного JSON → None. Это подпись
    # login-редиректа (urllib следует 302, HTML не парсится) — тот самый
    # класс, что алертил ложно.
    _set_response((200, None))
    check(instagram_api.probe_media_alive("synthetic-sid") is None)

    # N2 (негатив-похожий): 200-JSON без непустого list-items → None: сессия
    # ВАЛИДирована (иначе был бы 401/403/404), содержимого нет — не смерть
    # кукис. Пустой dict / пустой items / нестрочный items.
    for payload in ({}, {"items": []}, {"items": "мусор"}):
        _set_response((200, payload))
        check(instagram_api.probe_media_alive("synthetic-sid") is None)

    # N3: сетевая неудача / rate-limit / 5xx → None.
    for status_code, payload in ((0, None), (429, None), (503, None)):
        _set_response((status_code, payload))
        check(instagram_api.probe_media_alive("synthetic-sid") is None)

    # N4: sid=None → None, запрос НЕ выполнялся.
    calls.clear()
    _set_response((200, {"items": [{"pk": 1}]}))
    check(instagram_api.probe_media_alive(None) is None)
    check(calls == [])
finally:
    instagram_api.api_get_status = _original_api_get_status


# 3. load_sessionid: смок (полное покрытие — в tests/test_instagram_photos.py).
with tempfile.TemporaryDirectory() as temp_dir:
    sid_file = os.path.join(temp_dir, "cookies_sid.txt")
    _write_netscape(sid_file, [_sid_line("1811526512.091545")])
    check(instagram_api.load_sessionid(sid_file) == "synthetic-sid")

    empty_file = os.path.join(temp_dir, "cookies_empty.txt")
    _write_netscape(empty_file, [_sid_line("1811526512.091545", domain=".youtube.com")])
    check(instagram_api.load_sessionid(empty_file) is None)
    check(instagram_api.load_sessionid(None) is None)


# 5. YouTube-only монитор: плановый IG-мониторинг демонтирован (решение
#    владельца 06.10.2026). Инстанс через object.__new__, всё офлайн —
#    проверка подменена счётчиком.
yt_calls: list[str] = []


def _make_monitor(check_youtube) -> CookieHealthMonitor:
    monitor = object.__new__(CookieHealthMonitor)
    monitor._stop_event = threading.Event()
    monitor._check_youtube = check_youtube
    return monitor


def _counting_check_youtube() -> None:
    yt_calls.append("yt")


# П1 (позитив): _run_checks_once() вызывает _check_youtube ровно 1 раз.
monitor = _make_monitor(_counting_check_youtube)
monitor._run_checks_once()
check(yt_calls == ["yt"])

# Регресс защитной обёртки шага (былая секция F6, YouTube-половина):
# исключение в проверке не убивает шаг _run_checks_once.
def _raising_check_youtube() -> None:
    yt_calls.append("raise")
    raise RuntimeError("synthetic youtube failure")


monitor = _make_monitor(_raising_check_youtube)
raised = False
try:
    monitor._run_checks_once()
except Exception:
    raised = True
check(raised is False)
check(yt_calls == ["yt", "raise"])

# П2 (позитив): YouTube-проверка у монитора осталась.
check(hasattr(CookieHealthMonitor, "_check_youtube") is True)

# N1 (негатив): IG-метод монитора удалён.
check(hasattr(CookieHealthMonitor, "_check_instagram") is False)

# N2 (негатив): удалённая timestamp-функция недоступна из модуля.
check(hasattr(cookie_monitor, "_instagram_sessionid_expired") is False)

# N3 (негатив): в модуле монитора не осталось обращений к IG API.
check(not any("instagram_api" in name for name in dir(cookie_monitor)))
monitor_source = inspect.getsource(cookie_monitor)
check("probe_media_alive" not in monitor_source)
check("ping_session" not in monitor_source)
check("load_sessionid" not in monitor_source)


# 6. Тексты уведомлений notify_admin_cookies_expired: реальная функция со
#    stub-ботом; _cookies_alert_last чистится перед кейсом, platform-ключи
#    различаются, чтобы кулдаун не съел второй кейс.
class _StubBot:
    def __init__(self) -> None:
        self.sent: list[str] = []

    def send_message(self, chat_id, text, parse_mode=None) -> None:
        self.sent.append(text)


_original_admin_ids = app_utils.ADMIN_IDS
_saved_alert_last = dict(app_utils._cookies_alert_last)
try:
    app_utils.ADMIN_IDS = [424242]

    # T1: вызов с двумя аргументами (как YouTube и handlers) → прежний текст
    # не сломан.
    t1_bot = _StubBot()
    app_utils._cookies_alert_last.clear()
    app_utils.notify_admin_cookies_expired(t1_bot, "YouTubeT1")
    check(len(t1_bot.sent) == 1)
    check("Пользователь получил ошибку" in t1_bot.sent[0])
    check("протухли" in t1_bot.sent[0])

    # T2: вызов с произвольным литералом-reason (параметр reason в utils
    # остался) → честный текст про плановую проверку, без
    # «Пользователь получил ошибку».
    t2_reason = "Синтетическая причина: тестовый литерал reason."
    t2_bot = _StubBot()
    app_utils._cookies_alert_last.clear()
    app_utils.notify_admin_cookies_expired(t2_bot, "TestT2", t2_reason)
    check(len(t2_bot.sent) == 1)
    check("плановая проверка не прошла" in t2_bot.sent[0])
    check("Пользователь получил ошибку" not in t2_bot.sent[0])
    check(t2_reason in t2_bot.sent[0])
finally:
    app_utils.ADMIN_IDS = _original_admin_ids
    app_utils._cookies_alert_last.clear()
    app_utils._cookies_alert_last.update(_saved_alert_last)

print(f"TESTS OK: {checks} проверок")
