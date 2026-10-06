"""Standalone-проверки куки-монитора Instagram (полностью офлайн, сеть не трогаем).

Кукис в файлах — синтетические. ping_session и probe_media_alive проверяются
с подменой транспорта (monkeypatch instagram_api.api_get_status), реальных
запросов нет.
"""

import os
import sys
import tempfile

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from app import cookie_monitor, instagram_api
from app import utils as app_utils
from app.cookie_monitor import CookieHealthMonitor, _instagram_sessionid_expired

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


# 1. _instagram_sessionid_expired: timestamp-логика на синтетическом файле.
with tempfile.TemporaryDirectory() as temp_dir:
    expired_file = os.path.join(temp_dir, "c_expired.txt")
    _write_netscape(expired_file, [_sid_line("1000000000")])
    check(_instagram_sessionid_expired(expired_file) is True)

    fresh_file = os.path.join(temp_dir, "c_fresh.txt")
    _write_netscape(fresh_file, [_sid_line("9999999999")])
    check(_instagram_sessionid_expired(fresh_file) is False)

    # Регресс: float-expires на проде (MozillaCookieJar на таком файле падал LoadError).
    float_future_file = os.path.join(temp_dir, "c_float_future.txt")
    _write_netscape(float_future_file, [_sid_line("1999999999.5")])
    check(_instagram_sessionid_expired(float_future_file) is False)

    float_past_file = os.path.join(temp_dir, "c_float_past.txt")
    _write_netscape(float_past_file, [_sid_line("1000000000.5")])
    check(_instagram_sessionid_expired(float_past_file) is True)

    # F1: непредставимый timestamp (1e999/inf → OverflowError, nan → ValueError)
    # должен давать None, а не необработанное исключение.
    for bad_expires in ("1e999", "inf", "nan"):
        inf_file = os.path.join(temp_dir, f"c_inf_{bad_expires}.txt")
        _write_netscape(inf_file, [_sid_line(bad_expires)])
        check(_instagram_sessionid_expired(inf_file) is None)

    # F2: 0 — сессионная кука без срока (НЕ просрочена), отрицательное — мусор:
    # данных о сроке нет → None, а не True (без ложного алерта).
    session_cookie_file = os.path.join(temp_dir, "c_session.txt")
    _write_netscape(session_cookie_file, [_sid_line("0")])
    check(_instagram_sessionid_expired(session_cookie_file) is None)

    negative_file = os.path.join(temp_dir, "c_negative.txt")
    _write_netscape(negative_file, [_sid_line("-5")])
    check(_instagram_sessionid_expired(negative_file) is None)

    # F3: несколько instagram-строк sessionid — решение по МАКСИМАЛЬНОМУ
    # expires_at, а не по первой попавшейся строке.
    dup_first_expired = os.path.join(temp_dir, "c_dup1.txt")
    _write_netscape(
        dup_first_expired, [_sid_line("1000000000"), _sid_line("9999999999")]
    )
    check(_instagram_sessionid_expired(dup_first_expired) is False)

    dup_first_fresh = os.path.join(temp_dir, "c_dup2.txt")
    _write_netscape(
        dup_first_fresh, [_sid_line("9999999999"), _sid_line("1000000000")]
    )
    check(_instagram_sessionid_expired(dup_first_fresh) is False)

    # Мультистрочный файл, где даже максимум в прошлом → True.
    dup_all_expired = os.path.join(temp_dir, "c_dup3.txt")
    _write_netscape(
        dup_all_expired,
        [_sid_line("1000000000"), _sid_line("1500000000"), _sid_line("1250000000")],
    )
    check(_instagram_sessionid_expired(dup_all_expired) is True)

    # Мусорный timestamp у первой строки не прячет валидную строку ниже.
    dup_garbage_first = os.path.join(temp_dir, "c_dup4.txt")
    _write_netscape(
        dup_garbage_first, [_sid_line("inf"), _sid_line("9999999999")]
    )
    check(_instagram_sessionid_expired(dup_garbage_first) is False)

    no_sid_file = os.path.join(temp_dir, "c_no_sid.txt")
    _write_netscape(no_sid_file, [_sid_line("9999999999", domain=".youtube.com")])
    check(_instagram_sessionid_expired(no_sid_file) is None)

    # Битая строка (5 полей) не валит парс валидной строки ниже.
    broken_file = os.path.join(temp_dir, "c_broken.txt")
    _write_netscape(
        broken_file,
        ["not-a-netscape-line\twith\tfive\tfields\there", _sid_line("9999999999")],
    )
    check(_instagram_sessionid_expired(broken_file) is False)

    check(_instagram_sessionid_expired(os.path.join(temp_dir, "missing.txt")) is None)
    check(_instagram_sessionid_expired(None) is None)


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


# 1c. HP1–HP7: HttpOnly-формат на уровне читателей (load_sessionid и
#     _instagram_sessionid_expired). Боевой факт 07:05 UTC: такие строки
#     отсекались как комментарии — мониторинг IG был полностью инертен.
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
    check(_instagram_sessionid_expired(hp1_live) is False)  # 2027 — ещё свежа

    # HP1 (довесок): ms-expires в прошлом → True, ms в будущем → False.
    hp1_past = os.path.join(temp_dir, "hp1_past.txt")
    _write_netscape(hp1_past, [_httponly_sid_line("1000000000000")])  # 2001 в сек
    check(_instagram_sessionid_expired(hp1_past) is True)
    hp1_future = os.path.join(temp_dir, "hp1_future.txt")
    _write_netscape(hp1_future, [_httponly_sid_line("1999999999000")])  # 2033 в сек
    check(_instagram_sessionid_expired(hp1_future) is False)

    # HP3 (негатив-похожий): домен-фильтр работает и после срезания префикса.
    hp3_file = os.path.join(temp_dir, "hp3_youtube.txt")
    _write_netscape(
        hp3_file, [_httponly_sid_line("9999999999", domain=".youtube.com")]
    )
    check(instagram_api.load_sessionid(hp3_file) is None)
    check(_instagram_sessionid_expired(hp3_file) is None)

    # HP4 (негатив): прочие `#`-строки — по-прежнему комментарии, не куки.
    hp4_file = os.path.join(temp_dir, "hp4_comments.txt")
    _write_netscape(hp4_file, ["# просто комментарий", "#НеНашиКуки"])
    check(instagram_api.load_sessionid(hp4_file) is None)
    check(_instagram_sessionid_expired(hp4_file) is None)

    # HP5 (негатив-похожий): `#HttpOnly_` без домена и с 5 полями → ignored.
    hp5_file = os.path.join(temp_dir, "hp5_malformed.txt")
    _write_netscape(
        hp5_file,
        ["#HttpOnly_", "#HttpOnly_.instagram.com\tTRUE\t/\tTRUE\t999"],
    )
    check(instagram_api.load_sessionid(hp5_file) is None)
    check(_instagram_sessionid_expired(hp5_file) is None)

    # HP6 (негатив): HttpOnly-строка с мусорным expires пропущена без падения.
    for bad_expires in ("inf", "abc"):
        hp6_file = os.path.join(temp_dir, f"hp6_{bad_expires}.txt")
        _write_netscape(hp6_file, [_httponly_sid_line(bad_expires)])
        check(instagram_api.load_sessionid(hp6_file) is None)
        check(_instagram_sessionid_expired(hp6_file) is None)

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
    check(_instagram_sessionid_expired(hp7_ms) is False)


# 1d. Регресс прежней политики expires=0/отрицательного (раунд 5): парсер
#     ничего не решает про `<=0` — политики расходятся по вызывающим
#     (loader берёт такие строки кандидатами, monitor пропускает).
with tempfile.TemporaryDirectory() as temp_dir:
    # R1: файл с ОДНОЙ строкой sessionid expires 0 — loader отдаёт её value
    # (сессионная кука без срока — легитимный кандидат, поведение до
    # раунда 4), monitor по тому же файлу → None (данных о сроке нет,
    # решает живая проба).
    r1_file = os.path.join(temp_dir, "r1_zero.txt")
    _write_netscape(r1_file, [_plain_sid_line("0", value="synthetic-sid-zero")])
    check(instagram_api.load_sessionid(r1_file) == "synthetic-sid-zero")
    check(_instagram_sessionid_expired(r1_file) is None)

    # R2: 0-строка (сначала) проигрывает строке с положительным сроком —
    # loader отдаёт value ВТОРОЙ строки, expired → False.
    r2_file = os.path.join(temp_dir, "r2_zero_then_fresh.txt")
    _write_netscape(
        r2_file,
        [
            _plain_sid_line("0", value="synthetic-sid-zero"),
            _plain_sid_line("1999999999", value="synthetic-sid-fresh"),
        ],
    )
    check(instagram_api.load_sessionid(r2_file) == "synthetic-sid-fresh")
    check(_instagram_sessionid_expired(r2_file) is False)

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
    check(_instagram_sessionid_expired(r2_rev_file) is False)


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


# 4. CookieHealthMonitor._check_instagram: порядок sid → timestamp → проба
#    (probe_media_alive). Одиночная неудача не алертит — перепроба через
#    _RECHECK_DELAY_SECONDS (в тестах = 0, восстановить в finally), алерт
#    только после двух подряд; mtime-инвариант и F4-довесок сохранены.
class _StubDownloader:
    def __init__(self, cookiefile: str | None) -> None:
        self.cookiefile = cookiefile


alerts: list[tuple[str, str | None]] = []
probes: list[str | None] = []

# Сетевой предохранитель секции 4: проба патчится, но регресс реализации
# к ping_session увёл бы тест в реальный запрос к i.instagram.com. На место
# ping_session ставим маркер — звонок пишется в отдельный список, сеть не
# трогается (восстанавливается в finally секции 4).
ping_calls: list[str | None] = []


def _marker_ping_session(sid: str | None) -> bool | None:
    ping_calls.append(sid)
    return None


def _make_probe(results: list[bool | None]):
    state = {"next": 0}

    def probe(sid: str | None) -> bool | None:
        probes.append(sid)
        index = state["next"]
        state["next"] += 1
        if index < len(results):
            return results[index]
        return results[-1]

    return probe


def _fake_notify(bot, platform: str, reason: str | None = None) -> None:
    alerts.append((platform, reason))


_original_notify = app_utils.notify_admin_cookies_expired
_original_load = cookie_monitor.instagram_api.load_sessionid
_original_probe = cookie_monitor.instagram_api.probe_media_alive
_original_ping = cookie_monitor.instagram_api.ping_session
_original_recheck_delay = cookie_monitor._RECHECK_DELAY_SECONDS
try:
    app_utils.notify_admin_cookies_expired = _fake_notify
    # Перепроба в тестах мгновенная (реальная пауза — 60 с).
    cookie_monitor._RECHECK_DELAY_SECONDS = 0
    # Сетевой предохранитель: ping_session подменяется маркером на все кейсы
    # секции 4 (регресс к статус-пингу не должен дать реальный сетевой запрос).
    cookie_monitor.instagram_api.ping_session = _marker_ping_session

    with tempfile.TemporaryDirectory() as temp_dir:
        fresh_file = os.path.join(temp_dir, "m_fresh.txt")
        _write_netscape(fresh_file, [_sid_line("9999999999")])
        stale_file = os.path.join(temp_dir, "m_stale.txt")
        _write_netscape(stale_file, [_sid_line("1000000000")])

        # (а) sessionid не найдена → ни пробы, ни алерта.
        cookie_monitor.instagram_api.load_sessionid = lambda path: None
        cookie_monitor.instagram_api.probe_media_alive = _make_probe([False])
        monitor = CookieHealthMonitor(None, _StubDownloader(fresh_file))
        monitor._check_instagram()
        check(probes == [] and alerts == [])

        # (б) D1: проба [True] → ровно 1 вызов пробы, алертов нет.
        cookie_monitor.instagram_api.load_sessionid = lambda path: "synthetic-sid"
        probes.clear()
        alerts.clear()
        cookie_monitor.instagram_api.probe_media_alive = _make_probe([True])
        monitor = CookieHealthMonitor(None, _StubDownloader(fresh_file))
        monitor._check_instagram()
        check(probes == ["synthetic-sid"] and alerts == [])

        # (в) D3: [False, False] → ровно 2 вызова, ровно 1 алерт; причина
        #     честная (плановая проверка), без «Пользователь получил ошибку».
        probes.clear()
        alerts.clear()
        cookie_monitor.instagram_api.probe_media_alive = _make_probe([False, False])
        monitor = CookieHealthMonitor(None, _StubDownloader(fresh_file))
        monitor._check_instagram()
        check(probes == ["synthetic-sid", "synthetic-sid"])
        check(len(alerts) == 1 and alerts[0][0] == "Instagram")
        reason_text = (alerts[0][1] or "").lower()
        check("плановая" in reason_text and "проверка" in reason_text)
        check("Пользователь получил ошибку" not in (alerts[0][1] or ""))

        # (г) D4: [None] → ровно 1 вызов (без перепробы), алертов нет.
        probes.clear()
        alerts.clear()
        cookie_monitor.instagram_api.probe_media_alive = _make_probe([None])
        monitor = CookieHealthMonitor(None, _StubDownloader(fresh_file))
        monitor._check_instagram()
        check(probes == ["synthetic-sid"] and alerts == [])

        # (д) D2: [False, True] → ровно 2 вызова, алертов нет (перепроба спасла).
        probes.clear()
        alerts.clear()
        cookie_monitor.instagram_api.probe_media_alive = _make_probe([False, True])
        monitor = CookieHealthMonitor(None, _StubDownloader(fresh_file))
        monitor._check_instagram()
        check(probes == ["synthetic-sid", "synthetic-sid"] and alerts == [])

        # (е) D5: [False, None] → 2 вызова, алертов нет (вторая проба не
        #     подтвердила смерть).
        probes.clear()
        alerts.clear()
        cookie_monitor.instagram_api.probe_media_alive = _make_probe([False, None])
        monitor = CookieHealthMonitor(None, _StubDownloader(fresh_file))
        monitor._check_instagram()
        check(probes == ["synthetic-sid", "synthetic-sid"] and alerts == [])

        # (ж) D6: бот останавливается до решения → 1 вызов пробы, алертов нет
        #     (остановка бота глушит алерт).
        probes.clear()
        alerts.clear()
        cookie_monitor.instagram_api.probe_media_alive = _make_probe([False])
        monitor = CookieHealthMonitor(None, _StubDownloader(fresh_file))
        monitor._stop_event.set()
        monitor._check_instagram()
        check(probes == ["synthetic-sid"] and alerts == [])

        # (и) D7: обе пробы [False, False], но файл подменили ВО ВРЕМЯ пробы
        #     (mtime изменился) → алерт ПОДАВЛЕН (инвариант подмены кукис
        #     сохранён: ответ пробы относится уже к другой куке).
        probes.clear()
        alerts.clear()
        os.utime(stale_file)  # свежий mtime, отличимый от метки подмены ниже

        def _probe_and_swap_file(sid: str | None) -> bool | None:
            probes.append(sid)
            os.utime(stale_file, (1000000000.0, 1000000000.0))
            return False

        cookie_monitor.instagram_api.probe_media_alive = _probe_and_swap_file
        monitor = CookieHealthMonitor(None, _StubDownloader(stale_file))
        monitor._check_instagram()
        check(alerts == [])

        # (и-b) D7b: подмена кукис внутри ВТОРОЙ пробы (на окне перепробы
        #     действует тот же mtime-инвариант) → алерт ПОДАВЛЕН, проб ровно 2.
        probes.clear()
        alerts.clear()
        os.utime(stale_file)  # свежий mtime, отличимый от метки подмены ниже

        def _probe_swap_on_second(sid: str | None) -> bool | None:
            probes.append(sid)
            if len(probes) == 2:
                os.utime(stale_file, (1000000000.0, 1000000000.0))
            return False

        cookie_monitor.instagram_api.probe_media_alive = _probe_swap_on_second
        monitor = CookieHealthMonitor(None, _StubDownloader(stale_file))
        monitor._check_instagram()
        check(alerts == [] and probes == ["synthetic-sid", "synthetic-sid"])

        # (й) T2-3: файл обновился МЕЖДУ load_sessionid и пробой → алерт тоже
        #     подавлен (mtime_before снимается до load_sessionid, ответ пробы
        #     относится уже к другой куке).
        probes.clear()
        alerts.clear()
        os.utime(stale_file)

        def _load_and_swap_file(path):
            os.utime(stale_file, (1000000000.0, 1000000000.0))
            return "synthetic-sid"

        cookie_monitor.instagram_api.load_sessionid = _load_and_swap_file
        cookie_monitor.instagram_api.probe_media_alive = _make_probe([False, False])
        monitor = CookieHealthMonitor(None, _StubDownloader(stale_file))
        monitor._check_instagram()
        check(alerts == [])

        # (к) D8: проба [None] + файл с просроченным timestamp → 1 алерт
        #     (F4: довесок по локальному файлу сохранён).
        cookie_monitor.instagram_api.load_sessionid = lambda path: "synthetic-sid"
        probes.clear()
        alerts.clear()
        cookie_monitor.instagram_api.probe_media_alive = _make_probe([None])
        monitor = CookieHealthMonitor(None, _StubDownloader(stale_file))
        monitor._check_instagram()
        check(len(alerts) == 1 and probes == ["synthetic-sid"])

        # (л) D9: проба [True] + файл с просроченным timestamp → БЕЗ алерта
        #     (серверное состояние главнее локального файла, sliding-renewal).
        probes.clear()
        alerts.clear()
        cookie_monitor.instagram_api.probe_media_alive = _make_probe([True])
        monitor = CookieHealthMonitor(None, _StubDownloader(stale_file))
        monitor._check_instagram()
        check(alerts == [] and probes == ["synthetic-sid"])

        # Сетевой предохранитель секции 4: ни в одном кейсе реальный
        # ping_session не вызывался (маркер-список пуст).
        check(ping_calls == [])
finally:
    app_utils.notify_admin_cookies_expired = _original_notify
    cookie_monitor.instagram_api.load_sessionid = _original_load
    cookie_monitor.instagram_api.probe_media_alive = _original_probe
    cookie_monitor.instagram_api.ping_session = _original_ping
    cookie_monitor._RECHECK_DELAY_SECONDS = _original_recheck_delay


# 4b. HP8 (end-to-end регресс боевого инцидента 07:05 UTC): cookiefile в
#     HttpOnly-формате больше не невидим монитору. load_sessionid здесь
#     РЕАЛЬНЫЙ (не подменён) — sid находится парсером из HttpOnly-файла,
#     проба выполняется по найденному sid, алертов нет.
try:
    app_utils.notify_admin_cookies_expired = _fake_notify
    cookie_monitor._RECHECK_DELAY_SECONDS = 0
    cookie_monitor.instagram_api.probe_media_alive = _make_probe([True])
    cookie_monitor.instagram_api.ping_session = _marker_ping_session
    ping_calls.clear()
    with tempfile.TemporaryDirectory() as temp_dir:
        # HttpOnly-файл с ms-expires в будущем (2033 в секундах) — кукис свежи.
        hp8_file = os.path.join(temp_dir, "m_httponly_live.txt")
        _write_netscape(hp8_file, [_httponly_sid_line("1999999999000")])
        probes.clear()
        alerts.clear()
        monitor = CookieHealthMonitor(None, _StubDownloader(hp8_file))
        monitor._check_instagram()
        check(probes == ["synthetic-sid"])
        check(alerts == [])

        # Довесок HP8: на HttpOnly-файле с ms-expires в прошлом F4-ветка тоже
        # работает (проба неизвестна, локальный timestamp решает) — файл виден
        # монитору в обе стороны, а не только в «здоровую».
        hp8_stale = os.path.join(temp_dir, "m_httponly_stale.txt")
        _write_netscape(hp8_stale, [_httponly_sid_line("1000000000000")])
        probes.clear()
        alerts.clear()
        cookie_monitor.instagram_api.probe_media_alive = _make_probe([None])
        monitor = CookieHealthMonitor(None, _StubDownloader(hp8_stale))
        monitor._check_instagram()
        check(probes == ["synthetic-sid"])
        check(len(alerts) == 1 and alerts[0][0] == "Instagram")

    # Сетевой предохранитель секции 4b: реальный ping_session не звался.
    check(ping_calls == [])
finally:
    app_utils.notify_admin_cookies_expired = _original_notify
    cookie_monitor.instagram_api.probe_media_alive = _original_probe
    cookie_monitor.instagram_api.ping_session = _original_ping
    cookie_monitor._RECHECK_DELAY_SECONDS = _original_recheck_delay


# 5. F6: защитные обёртки цикла — исключение в проверке не убивает шаг
#    (`_run_checks_once`) и не глушит вторую проверку.
youtube_calls: list[str] = []
ig_calls: list[str] = []


class _RaisingDownloader:
    cookiefile = None

    def get_info(self, url: str) -> dict:
        youtube_calls.append(url)
        raise RuntimeError("synthetic youtube failure")


def _raising_load_sessionid(path: str | None) -> str | None:
    ig_calls.append("load")
    raise RuntimeError("synthetic instagram failure")


_original_load_for_f6 = cookie_monitor.instagram_api.load_sessionid
try:
    cookie_monitor.instagram_api.load_sessionid = _raising_load_sessionid
    # Патч notify возвращаем и здесь: без него финальный ассерт alerts == []
    # вакуумный — список к этому моменту никто не наполняет, и он проходил бы
    # даже при реальном алерте.
    app_utils.notify_admin_cookies_expired = _fake_notify
    alerts.clear()
    f6_monitor = CookieHealthMonitor(None, _RaisingDownloader())

    raised = False
    try:
        # Обе проверки рейзят — шаг цикла обязан отработать до конца без падения.
        f6_monitor._run_checks_once()
    except Exception:
        raised = True
    check(raised is False)

    # Позитив: обе проверки действительно ВЫПОЛНИЛИСЬ (YouTube не заглушил Instagram).
    check(len(youtube_calls) == 1 and len(ig_calls) == 1)

    # Негатив: после защитных обёрток алертов быть не должно (обе проверки упали
    # до решения об алерте).
    check(alerts == [])
finally:
    app_utils.notify_admin_cookies_expired = _original_notify
    cookie_monitor.instagram_api.load_sessionid = _original_load_for_f6
    cookie_monitor.instagram_api.load_sessionid = _original_load


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

    # T2: вызов с reason из cookie_monitor → честный текст про плановую
    # проверку, без «Пользователь получил ошибку».
    t2_bot = _StubBot()
    app_utils._cookies_alert_last.clear()
    app_utils.notify_admin_cookies_expired(
        t2_bot, "InstagramT2", cookie_monitor._INSTAGRAM_ALERT_REASON
    )
    check(len(t2_bot.sent) == 1)
    check("плановая проверка не прошла" in t2_bot.sent[0])
    check("Пользователь получил ошибку" not in t2_bot.sent[0])
    check(cookie_monitor._INSTAGRAM_ALERT_REASON in t2_bot.sent[0])
finally:
    app_utils.ADMIN_IDS = _original_admin_ids
    app_utils._cookies_alert_last.clear()
    app_utils._cookies_alert_last.update(_saved_alert_last)

print(f"TESTS OK: {checks} проверок")
