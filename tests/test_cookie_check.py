"""Standalone-проверки куки-монитора Instagram (полностью офлайн, сеть не трогаем).

Кукис в файлах — синтетические. ping_session проверяется с подменой транспорта
(monkeypatch instagram_api.api_get_status), реальных запросов нет.
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


# 3. load_sessionid: смок (полное покрытие — в tests/test_instagram_photos.py).
with tempfile.TemporaryDirectory() as temp_dir:
    sid_file = os.path.join(temp_dir, "cookies_sid.txt")
    _write_netscape(sid_file, [_sid_line("1811526512.091545")])
    check(instagram_api.load_sessionid(sid_file) == "synthetic-sid")

    empty_file = os.path.join(temp_dir, "cookies_empty.txt")
    _write_netscape(empty_file, [_sid_line("1811526512.091545", domain=".youtube.com")])
    check(instagram_api.load_sessionid(empty_file) is None)
    check(instagram_api.load_sessionid(None) is None)


# 4. CookieHealthMonitor._check_instagram: порядок sid → timestamp → ping,
#    тяжёлый yt-dlp get_info для Instagram больше не зовётся (сеть не трогаем).
class _StubDownloader:
    def __init__(self, cookiefile: str | None) -> None:
        self.cookiefile = cookiefile


alerts: list[str] = []
pings: list[str | None] = []


def _make_ping(result: bool | None):
    def ping(sid: str | None) -> bool | None:
        pings.append(sid)
        return result
    return ping


def _fake_notify(bot, platform: str) -> None:
    alerts.append(platform)


_original_notify = app_utils.notify_admin_cookies_expired
_original_load = cookie_monitor.instagram_api.load_sessionid
_original_ping = cookie_monitor.instagram_api.ping_session
try:
    app_utils.notify_admin_cookies_expired = _fake_notify

    with tempfile.TemporaryDirectory() as temp_dir:
        fresh_file = os.path.join(temp_dir, "m_fresh.txt")
        _write_netscape(fresh_file, [_sid_line("9999999999")])
        stale_file = os.path.join(temp_dir, "m_stale.txt")
        _write_netscape(stale_file, [_sid_line("1000000000")])

        # (а) sessionid не найдена → ни пинга, ни алерта.
        cookie_monitor.instagram_api.load_sessionid = lambda path: None
        cookie_monitor.instagram_api.ping_session = _make_ping(False)
        monitor = CookieHealthMonitor(None, _StubDownloader(fresh_file))
        monitor._check_instagram()
        check(pings == [] and alerts == [])

        # (б) живой sid → ping, алерта нет.
        cookie_monitor.instagram_api.load_sessionid = lambda path: "synthetic-sid"
        cookie_monitor.instagram_api.ping_session = _make_ping(True)
        monitor = CookieHealthMonitor(None, _StubDownloader(fresh_file))
        monitor._check_instagram()
        check(pings == ["synthetic-sid"] and alerts == [])

        # (в) мёртвый sid (ping=False) → алерт.
        alerts.clear()
        pings.clear()
        cookie_monitor.instagram_api.ping_session = _make_ping(False)
        monitor = CookieHealthMonitor(None, _StubDownloader(fresh_file))
        monitor._check_instagram()
        check(pings == ["synthetic-sid"] and alerts == ["Instagram"])

        # (г) статус неизвестен (сеть/rate-limit) → без алерта.
        alerts.clear()
        pings.clear()
        cookie_monitor.instagram_api.ping_session = _make_ping(None)
        monitor = CookieHealthMonitor(None, _StubDownloader(fresh_file))
        monitor._check_instagram()
        check(pings == ["synthetic-sid"] and alerts == [])

        # (д) просрочена по файлу + живую проверку провести не удалось → алерт (F4).
        alerts.clear()
        pings.clear()
        cookie_monitor.instagram_api.ping_session = _make_ping(None)
        monitor = CookieHealthMonitor(None, _StubDownloader(stale_file))
        monitor._check_instagram()
        check(alerts == ["Instagram"] and pings == ["synthetic-sid"])

        # (е) timestamp просрочен, но сервер подтвердил живость → БЕЗ алерта (F4):
        # серверное состояние главнее локального файла, sliding-renewal.
        alerts.clear()
        pings.clear()
        cookie_monitor.instagram_api.ping_session = _make_ping(True)
        monitor = CookieHealthMonitor(None, _StubDownloader(stale_file))
        monitor._check_instagram()
        check(alerts == [] and pings == ["synthetic-sid"])

        # (ж) мёртв по пингу при просроченном timestamp → алерт по пингу (F4).
        alerts.clear()
        pings.clear()
        cookie_monitor.instagram_api.ping_session = _make_ping(False)
        monitor = CookieHealthMonitor(None, _StubDownloader(stale_file))
        monitor._check_instagram()
        check(alerts == ["Instagram"] and pings == ["synthetic-sid"])

        # (и) F5: файл подменили ВО ВРЕМЯ пинга (mtime изменился) → алерт подавлен,
        # даже если пинг ответил «мёртв» (ответ относится уже к другой куке).
        alerts.clear()
        pings.clear()

        def _ping_and_swap_file(sid: str | None) -> bool | None:
            pings.append(sid)
            os.utime(stale_file, (1000000000.0, 1000000000.0))
            return False

        cookie_monitor.instagram_api.ping_session = _ping_and_swap_file
        monitor = CookieHealthMonitor(None, _StubDownloader(stale_file))
        monitor._check_instagram()
        check(alerts == [] and pings == ["synthetic-sid"])

        # (к) T2-3: файл обновился МЕЖДУ load_sessionid и пингом → алерт тоже
        #     подавлен (mtime_before снимается до load_sessionid, ответ пинга
        #     относится уже к другой куке).
        alerts.clear()
        pings.clear()
        os.utime(stale_file)  # свежий mtime, отличный от метки кейса (и)

        def _load_and_swap_file(path):
            os.utime(stale_file, (1000000000.0, 1000000000.0))
            return "synthetic-sid"

        cookie_monitor.instagram_api.load_sessionid = _load_and_swap_file
        cookie_monitor.instagram_api.ping_session = _make_ping(False)
        monitor = CookieHealthMonitor(None, _StubDownloader(stale_file))
        monitor._check_instagram()
        check(alerts == [] and pings == ["synthetic-sid"])
finally:
    app_utils.notify_admin_cookies_expired = _original_notify
    cookie_monitor.instagram_api.load_sessionid = _original_load
    cookie_monitor.instagram_api.ping_session = _original_ping


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
    cookie_monitor.instagram_api.load_sessionid = _original_load_for_f6
    cookie_monitor.instagram_api.load_sessionid = _original_load
    cookie_monitor.instagram_api.ping_session = _original_ping

print(f"TESTS OK: {checks} проверок")
