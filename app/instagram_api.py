"""Обвязка приватного API Instagram: фото-посты, которые yt-dlp не отдаёт.

Без сторонних зависимостей (urllib + json). sessionid нигде не логируется.
Фактура: GET https://i.instagram.com/api/v1/media/{pk}/info/ с заголовками
браузерного UA и X-IG-App-ID; без живого sessionid — 404 HTML.
"""

import json
import logging
import os
import re
import time
import urllib.error
import urllib.request
from urllib.parse import urlparse

from app.config import USER_AGENT

IG_APP_ID = "936619743392459"
# Публичный пост-«яйцо» (самый известный пост Instagram) — фиксированная цель
# контентной пробы живости sessionid: эндпоинт реально отдаёт медиа поста
# (accounts/current_user → 403, web_profile_info → 429, проверено живьём).
_PING_SHORTCODE = "BsOGulcndj-"

_ENCODING_CHARS = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"

_API_BASE = "https://i.instagram.com"
_ALLOWED_MEDIA_HOST_SUFFIXES = (".cdninstagram.com", ".fbcdn.net")
_ALLOWED_POST_HOSTS = (
    "instagram.com",
    "www.instagram.com",
    "instagr.am",
    "www.instagr.am",
)
_PATH_SHORTCODE_RE = re.compile(r"^/(?:p|reel|tv)/([A-Za-z0-9_-]+)")


def shortcode_to_pk(shortcode: str) -> int:
    """Копия алгоритма yt-dlp instagram._id_to_pk (сам экстрактор не патчим).

    len>28 → срезать последние 28 символов. Пустой/невалидный символ → ValueError.
    """
    if not isinstance(shortcode, str):
        raise TypeError("shortcode должен быть строкой")
    if not shortcode:
        raise ValueError("shortcode пуст")
    if len(shortcode) > 28:
        shortcode = shortcode[:-28]
    table = {char: index for index, char in enumerate(_ENCODING_CHARS)}
    result = 0
    for char in shortcode:
        if char not in table:
            raise ValueError(f"Невалидный символ shortcode: {char!r}")
        result = result * len(table) + table[char]
    return result


def extract_shortcode(url: str) -> str | None:
    """Из https://www.instagram.com/p|reel|tv/<code>/… (с query или без) → <code>.

    Хост строго из белого списка (чужой домен вида evil.com/p/<code>/ → None),
    query-параметры поддерживаются.
    """
    if not isinstance(url, str) or not url:
        return None
    try:
        parsed = urlparse(url)
    except ValueError:
        return None
    if (parsed.hostname or "").lower() not in _ALLOWED_POST_HOSTS:
        return None
    match = _PATH_SHORTCODE_RE.match(parsed.path or "")
    if not match:
        return None
    return match.group(1)


def load_sessionid(cookiefile: str | None) -> str | None:
    """Читает sessionid домена instagram.com из Netscape-файла.

    Файл парсится построчно и tab-split (7 полей). Подходящих строк sessionid
    несколько → побеждает строка с МАКСИМАЛЬНЫМ expires_at (та же политика
    «свежайшая строка побеждает», что у _instagram_sessionid_expired в
    cookie_monitor): expires парсится как int(float(...)), строка с нечитаемым
    expires пропускается, а не валит чтение. Файла/подходящих строк нет → None.
    """
    if not cookiefile or not os.path.exists(cookiefile):
        return None
    best_expires: int | None = None
    best_value: str | None = None
    try:
        with open(cookiefile, "r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                stripped = line.strip()
                if not stripped or stripped.startswith("#"):
                    continue
                parts = stripped.split("\t")
                if len(parts) != 7:
                    continue
                domain, _flag, _path, _secure, expires, name, value = parts
                if name != "sessionid" or "instagram.com" not in domain.lower():
                    continue
                value = value.strip()
                if not value or not value.isprintable():
                    # Значение с непечатаемыми символами (CR/LF внутри) — не
                    # кандидат: http.client бросит ValueError при сборке
                    # Cookie-заголовка на ровном месте.
                    continue
                try:
                    expires_at = int(float(expires))
                except (ValueError, OverflowError):
                    # Мусорный или непредставимый timestamp — у этой строки
                    # данных о сроке нет, чтение не валит.
                    continue
                if best_expires is None or expires_at > best_expires:
                    best_expires = expires_at
                    best_value = value
    except OSError as exc:
        logging.debug("Instagram sessionid: не удалось прочитать файл кукис: %s", exc)
        return None
    return best_value


def api_get_status(
    path_query: str, sessionid: str | None, timeout: float = 15.0
) -> tuple[int, dict | None]:
    """GET https://i.instagram.com<path_query> с приватными заголовками.

    Ровно один повтор — только на сетевые ошибки (URLError/timeout), HTTP-коды
    без повторов. Возвращает (status, parsed_json_or_None): status 0 — сетевая
    неудача; JSON парсится только при 200 и валидном теле. Секреты не логируем.
    """
    headers = {
        "User-Agent": USER_AGENT,
        "X-IG-App-ID": IG_APP_ID,
        "Accept": "*/*",
    }
    if sessionid:
        headers["Cookie"] = f"sessionid={sessionid}"
    request = urllib.request.Request(_API_BASE + path_query, headers=headers)
    for attempt in (1, 2):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                status = response.status
                if status != 200:
                    logging.debug("Instagram API: HTTP %s при %s", status, path_query)
                    return status, None
                body = response.read()
            try:
                return status, json.loads(body.decode("utf-8", errors="replace"))
            except (ValueError, UnicodeDecodeError):
                logging.debug("Instagram API: тело не JSON при %s", path_query)
                return status, None
        except urllib.error.HTTPError as exc:
            logging.debug("Instagram API: HTTP %s при %s", exc.code, path_query)
            return exc.code, None
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            logging.debug(
                "Instagram API: сетевая ошибка (попытка %d/2) при %s: %s",
                attempt, path_query, exc,
            )
            if attempt == 2:
                break
            time.sleep(1.0)
    return 0, None


def api_get_json(
    path_query: str, sessionid: str | None, timeout: float = 15.0
) -> dict | None:
    """Обёртка api_get_status: dict при status==200, иначе None."""
    status, payload = api_get_status(path_query, sessionid, timeout)
    return payload if status == 200 else None


def ping_session(sessionid: str | None) -> bool | None:
    """Дешёвая проверка живости sessionid по фиксированному публичному посту.

    True — 200 и JSON (sid живой); False — 401/403/404 (мёртвый/отклонённый sid);
    None — sid нет, сетевая неудача (status 0), 200 без разобранного JSON или
    прочий не-200 (429/5xx — rate-limit/сбой Instagram: статус неизвестен,
    это НЕ смерть кукис, алертить нельзя).
    """
    if not sessionid:
        return None
    try:
        pk = shortcode_to_pk(_PING_SHORTCODE)
    except ValueError:
        return None
    status, payload = api_get_status(f"/api/v1/media/{pk}/info/", sessionid)
    if status in (401, 403, 404):
        return False
    if status == 200 and payload is not None:
        return True
    return None


def probe_media_alive(sessionid: str | None) -> bool | None:
    """Контентная проба живости sessionid: пост-«яйцо» реально отдаёт медиа.

    True — 200 и непустой items (кукис реально тянут контент);
    False — 401/403/404 (сессия отклонена; живьём 06.10.2026: мёртвый sid → 404
    без JSON);
    None — sid нет, сетевая неудача (0), 429/5xx, 200 без разобранного JSON
    (login-редирект отдаёт HTML-страницу) или 200-JSON с пустым/нестрочным
    items (яйцо недоступно при живой сессии — это НЕ смерть кукис, алертить
    нельзя).
    """
    if not sessionid:
        return None
    try:
        pk = shortcode_to_pk(_PING_SHORTCODE)
    except ValueError:
        return None
    status, payload = api_get_status(f"/api/v1/media/{pk}/info/", sessionid)
    if status in (401, 403, 404):
        return False
    if status == 200 and isinstance(payload, dict):
        items = payload.get("items")
        if isinstance(items, list) and items:
            return True
        logging.debug(
            "Instagram API: проба 200 без непустого items (пост-яйцо недоступен?)"
        )
    return None


def fetch_media_info(url: str, sessionid: str | None) -> dict | None:
    """Словарь медиа поста (items[0]) через приватный API; любая неудача → None."""
    shortcode = extract_shortcode(url)
    if not shortcode:
        logging.debug("Instagram API: shortcode не извлечён из url")
        return None
    try:
        pk = shortcode_to_pk(shortcode)
    except ValueError as exc:
        logging.debug("Instagram API: shortcode не декодирован: %s", exc)
        return None
    payload = api_get_json(f"/api/v1/media/{pk}/info/", sessionid)
    if not isinstance(payload, dict):
        return None
    items = payload.get("items") or []
    if not items or not isinstance(items[0], dict):
        return None
    return items[0]


def _best_image_candidate(
    image_versions2: dict | None,
) -> tuple[str | None, int | None, int | None]:
    """Кандидат с максимальной площадью width*height из image_versions2."""
    if not isinstance(image_versions2, dict):
        return None, None, None
    best: tuple[str | None, int | None, int | None] | None = None
    best_area = -1
    for candidate in image_versions2.get("candidates") or []:
        if not isinstance(candidate, dict):
            continue
        url = candidate.get("url")
        if not isinstance(url, str) or not url:
            continue
        width = candidate.get("width")
        height = candidate.get("height")
        if not isinstance(width, int) or isinstance(width, bool):
            width = None
        if not isinstance(height, int) or isinstance(height, bool):
            height = None
        area = (width or 0) * (height or 0)
        if area > best_area:
            best_area = area
            best = (url, width, height)
    return best if best is not None else (None, None, None)


def iter_photo_children(media_item: dict | None) -> list[dict]:
    """Дети поста ПО ПОРЯДКУ в нормализованном виде.

    carousel_media есть → дети из него; иначе [media_item] (одиночный пост).
    Для ответа API с пустым items → []. Каждый ребёнок:
    {'is_video': bool, 'image_url': str|None, 'width': int|None, 'height': int|None}.
    Ничего не бросает.
    """
    if not isinstance(media_item, dict):
        return []
    if "items" in media_item:
        items = media_item.get("items") or []
        if not items or not isinstance(items[0], dict):
            return []
        media_item = items[0]
    raw_children = media_item.get("carousel_media")
    if not raw_children:
        raw_children = [media_item]
    children: list[dict] = []
    for child in raw_children:
        if not isinstance(child, dict):
            continue
        image_url, width, height = _best_image_candidate(child.get("image_versions2"))
        children.append({
            "is_video": child.get("media_type") != 1 or bool(child.get("video_versions")),
            "image_url": image_url,
            "width": width,
            "height": height,
        })
    return children


def is_allowed_media_host(url: str) -> bool:
    """Гигиена SSRF: только https:// и хост *.cdninstagram.com / *.fbcdn.net."""
    if not isinstance(url, str) or not url:
        return False
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    if parsed.scheme != "https":
        return False
    host = (parsed.hostname or "").lower()
    if not host:
        return False
    return host.endswith(_ALLOWED_MEDIA_HOST_SUFFIXES)
