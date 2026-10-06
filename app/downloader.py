"""Скачивание видео через yt-dlp, разделение больших файлов (FFmpeg)."""

import http.client
import json
import logging
import os
import re
import subprocess
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from collections.abc import Callable
from urllib.parse import urlparse

import requests
from yt_dlp import YoutubeDL

from app import instagram_api
from app.config import (
    COOKIES_FILE,
    USER_AGENT,
    VK_PASSWORD,
    VK_USERNAME,
    YOUTUBE_JS_RUNTIME,
    YOUTUBE_JS_RUNTIME_PATH,
    YOUTUBE_PLAYER_CLIENTS,
)
from app.constants import PREFERRED_VIDEO_FORMAT

# Наборы player_client для повторных попыток YouTube.
# Если первая попытка с текущими настройками провалилась с
# «Requested format is not available», перебираем альтернативные конфигурации:
# SABR, PO-token и возрастные ограничения затрагивают клиентов по-разному.
_YOUTUBE_RETRY_CLIENT_SETS: list[list[str]] = [
    ["android_vr", "web"],
    ["default"],
    ["tv_downgraded", "web"],
]


def compute_download_deadline(
    download_started: float,
    total_bytes: int | float | None,
    floor_seconds: int,
    ceiling_seconds: int,
    min_speed_bps: int,
) -> float:
    """Абсолютный дедлайн скачивания: пол floor_seconds, при известном
    размере — max(пол, размер/скорость), потолок ceiling_seconds.
    """
    if total_bytes:
        seconds = min(
            ceiling_seconds,
            max(floor_seconds, total_bytes / min_speed_bps),
        )
        return download_started + seconds
    return download_started + floor_seconds


def _is_youtube_format_error(exc: Exception) -> bool:
    """Ошибка «Requested format is not available» от YouTube."""
    error_lower = str(exc).lower()
    return (
        "youtube" in error_lower
        and "requested format is not available" in error_lower
    )


def _is_h264(vcodec: str | None) -> bool:
    """Проверяет, является ли видеокодек H.264 (AVC).

    H.264 поддерживается всеми Apple-устройствами и корректно
    воспроизводится в Telegram на iOS/macOS. VP9 и AV1 могут
    приводить к проблемам: звук идёт, а видео зависает на первом кадре.
    """
    if not vcodec:
        return False
    v = vcodec.lower()
    return v.startswith("avc") or v.startswith("h264")


def _get_video_codec(file_path: str) -> str | None:
    """Определяет видеокодек файла через ffprobe."""
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-select_streams", "v:0",
                "-show_entries", "stream=codec_name",
                "-of", "json",
                file_path,
            ],
            capture_output=True, text=True, timeout=30,
        )
        info = json.loads(result.stdout)
        streams = info.get("streams") or []
        if streams:
            return streams[0].get("codec_name")
    except Exception:
        logging.exception("ffprobe не удался для %s", file_path)
    return None


def _get_video_sar(file_path: str) -> str | None:
    """Возвращает SAR (sample_aspect_ratio) первого видеопотока, например '1:1'."""
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-select_streams", "v:0",
                "-show_entries", "stream=sample_aspect_ratio",
                "-of", "json",
                file_path,
            ],
            capture_output=True, text=True, timeout=30,
        )
        info = json.loads(result.stdout)
        streams = info.get("streams") or []
        if streams:
            return streams[0].get("sample_aspect_ratio")
    except Exception:
        logging.exception("ffprobe SAR не удался для %s", file_path)
    return None


def get_video_dimensions(file_path: str) -> tuple[int | None, int | None]:
    """Возвращает (width, height) видео с учётом поворота и SAR.

    Использует ffprobe для получения реальных отображаемых размеров.
    Это важно для корректного отображения на iPhone — Telegram iOS
    может неправильно определить размеры из метаданных файла.
    """
    try:
        # Простой вызов ffprobe — только width, height, SAR
        result = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-select_streams", "v:0",
                "-show_entries", "stream=width,height,sample_aspect_ratio",
                "-of", "json",
                file_path,
            ],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            logging.warning(
                "ffprobe вернул код %d для %s: %s",
                result.returncode, file_path, result.stderr[:200],
            )
            return None, None

        if not result.stdout.strip():
            logging.warning("ffprobe вернул пустой stdout для %s", file_path)
            return None, None

        info = json.loads(result.stdout)
        streams = info.get("streams") or []
        if not streams:
            logging.warning("ffprobe не нашёл видеопотоков в %s", file_path)
            return None, None

        stream = streams[0]
        width = stream.get("width")
        height = stream.get("height")
        if width is None or height is None:
            logging.warning("ffprobe не вернул width/height для %s: %s", file_path, stream)
            return None, None

        width = int(width)
        height = int(height)

        # Учитываем SAR (sample aspect ratio)
        sar = stream.get("sample_aspect_ratio")
        if sar and sar not in ("1:1", "N/A", "0:1"):
            parts = sar.split(":")
            if len(parts) == 2:
                try:
                    sar_num, sar_den = int(parts[0]), int(parts[1])
                    if sar_den > 0:
                        width = int(width * sar_num / sar_den)
                except (ValueError, ZeroDivisionError):
                    pass

        # Учитываем поворот через уже проверенную функцию
        rotation = _get_video_rotation(file_path)
        if rotation in (90, 270):
            width, height = height, width

        return width, height

    except Exception:
        logging.exception("get_video_dimensions не удался для %s", file_path)
    return None, None


def cleanup_video_metadata(file_path: str) -> str:
    """Очищает метаданные видео для корректного отображения на iPhone.

    Быстрая операция без перекодирования (stream copy):
    - Убирает rotate-тег из метаданных
    - Перемещает moov atom в начало файла (faststart)

    Используется для iPhone, когда видео H.264, SAR=1:1, rotation=0,
    но всё равно отображается сплющенным из-за остаточных метаданных
    в контейнере.
    """
    base, ext = os.path.splitext(file_path)
    output_path = f"{base}_clean{ext}"
    cmd = [
        "ffmpeg", "-y", "-i", file_path,
        "-c", "copy",
        "-metadata:s:v:0", "rotate=0",
        "-movflags", "+faststart",
        output_path,
    ]
    try:
        subprocess.run(cmd, capture_output=True, timeout=60, check=True)
    except Exception:
        logging.debug("cleanup_video_metadata не удался для %s", file_path)
        try:
            os.remove(output_path)
        except OSError:
            pass
        return file_path

    if not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
        try:
            os.remove(output_path)
        except OSError:
            pass
        return file_path

    try:
        os.remove(file_path)
        os.replace(output_path, file_path)
    except OSError:
        if os.path.exists(output_path):
            return output_path
        return file_path

    return file_path


def _get_video_rotation(file_path: str) -> int:
    """Возвращает угол поворота видео (0, 90, 180, 270).

    Проверяет display matrix (side_data) и legacy-тег rotate.
    iPhone/Telegram iOS не всегда корректно применяют метаданные поворота
    из контейнера — вертикальное видео отображается сплющенным.
    """
    # Способ 1: display matrix (side_data) — современный стандарт
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-select_streams", "v:0",
                "-show_entries", "stream_side_data=rotation",
                "-of", "json",
                file_path,
            ],
            capture_output=True, text=True, timeout=30,
        )
        info = json.loads(result.stdout)
        for stream in info.get("streams") or []:
            for side_data in stream.get("side_data_list") or []:
                rotation = side_data.get("rotation")
                if rotation is not None:
                    return int(float(rotation)) % 360
    except Exception:
        logging.debug("ffprobe side_data rotation не удался для %s", file_path)

    # Способ 2: тег rotate в метаданных потока (legacy MP4)
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-select_streams", "v:0",
                "-show_entries", "stream_tags=rotate",
                "-of", "json",
                file_path,
            ],
            capture_output=True, text=True, timeout=30,
        )
        info = json.loads(result.stdout)
        for stream in info.get("streams") or []:
            tags = stream.get("tags") or {}
            rotate = tags.get("rotate")
            if rotate is not None:
                return int(rotate) % 360
    except Exception:
        logging.debug("ffprobe tag rotate не удался для %s", file_path)

    return 0


def fix_video_rotation(file_path: str) -> tuple[str, bool]:
    """Впекает поворот видео в пиксели (с перекодированием в H.264).

    iPhone/Telegram iOS не всегда корректно применяют метаданные поворота
    из контейнера — вертикальное видео отображается сплющенным как
    горизонтальное. Перекодирование с автоповоротом ffmpeg решает проблему:
    ffmpeg считывает display matrix, поворачивает пиксели и убирает метаданные.
    Одновременно фиксит SAR на 1:1.

    Возвращает (путь_к_файлу, было_перекодировано).
    """
    rotation = _get_video_rotation(file_path)
    if rotation == 0:
        logging.info("Поворот 0°, пропускаем: %s", file_path)
        return file_path, False

    logging.info("Поворот %d°, впекаем в пиксели (перекодирование): %s", rotation, file_path)

    base, ext = os.path.splitext(file_path)
    output_path = f"{base}_rot{ext}"

    # ffmpeg по умолчанию автоматически поворачивает видео при перекодировании
    # (autorotate). Фильтр scale впекает SAR в реальные пиксели, setsar=1
    # нормализует. Autorotate применяется ДО -vf фильтров, поэтому scale
    # получает уже повёрнутые размеры — корректно для любого угла.
    cmd = [
        "ffmpeg", "-y", "-i", file_path,
        "-c:v", "libx264",
        "-preset", "faster",
        "-crf", "23",
        "-profile:v", "high",
        "-level", "4.1",
        "-pix_fmt", "yuv420p",
        "-vf", "scale=trunc(iw*sar/2)*2:trunc(ih/2)*2,setsar=1",
        "-c:a", "copy",
        "-movflags", "+faststart",
        output_path,
    ]

    try:
        subprocess.run(cmd, capture_output=True, timeout=600, check=True)
    except subprocess.CalledProcessError:
        logging.warning("Копирование аудио не удалось при fix_rotation, перекодируем аудио в AAC")
        cmd_full = [
            "ffmpeg", "-y", "-i", file_path,
            "-c:v", "libx264",
            "-preset", "faster",
            "-crf", "23",
            "-profile:v", "high",
            "-level", "4.1",
            "-pix_fmt", "yuv420p",
            "-vf", "scale=trunc(iw*sar/2)*2:trunc(ih/2)*2,setsar=1",
            "-c:a", "aac", "-b:a", "192k",
            "-movflags", "+faststart",
            output_path,
        ]
        try:
            subprocess.run(cmd_full, capture_output=True, timeout=600, check=True)
        except Exception:
            logging.exception("fix_rotation (полное перекодирование) не удалось для %s", file_path)
            try:
                os.remove(output_path)
            except OSError:
                pass
            return file_path, False
    except Exception:
        logging.exception("fix_rotation не удалось для %s", file_path)
        try:
            os.remove(output_path)
        except OSError:
            pass
        return file_path, False

    if not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
        logging.error("Файл после fix_rotation пустой: %s", output_path)
        try:
            os.remove(output_path)
        except OSError:
            pass
        return file_path, False

    try:
        os.remove(file_path)
        os.replace(output_path, file_path)
    except OSError:
        logging.exception("Не удалось заменить %s после fix_rotation", file_path)
        if os.path.exists(output_path):
            return output_path, True
        return file_path, False

    logging.info("Поворот %d° впечён в пиксели: %s", rotation, file_path)
    return file_path, True


def fix_h264_sar(file_path: str, force_reencode: bool = False) -> tuple[str, bool]:
    """Исправляет SAR на 1:1 для H.264 видео.

    По умолчанию использует bitstream filter (быстро, без перекодирования).
    При force_reencode=True выполняет полное перекодирование с впеканием
    SAR в пиксели — необходимо для iPhone, т.к. Telegram iOS не всегда
    корректно применяет SAR из метаданных, и вертикальное видео
    отображается сплющенным.

    Возвращает (путь_к_файлу, было_перекодировано).
    """
    sar = _get_video_sar(file_path)
    if sar is None or sar in ("1:1", "N/A"):
        logging.info("SAR уже корректен (%s), пропускаем: %s", sar, file_path)
        return file_path, False

    base, ext = os.path.splitext(file_path)
    output_path = f"{base}_sar{ext}"

    if force_reencode:
        # Полное перекодирование: впекаем SAR в реальные пиксели.
        # iPhone/Telegram iOS не применяют SAR из контейнера корректно —
        # вертикальное видео с SAR != 1:1 показывается сплющенным.
        logging.info("SAR=%s, впекаем в пиксели (перекодирование для iPhone): %s", sar, file_path)
        cmd = [
            "ffmpeg", "-y", "-i", file_path,
            "-c:v", "libx264",
            "-preset", "faster",
            "-crf", "23",
            "-profile:v", "high",
            "-level", "4.1",
            "-pix_fmt", "yuv420p",
            "-vf", "scale=trunc(iw*sar/2)*2:trunc(ih/2)*2,setsar=1",
            "-c:a", "copy",
            "-movflags", "+faststart",
            output_path,
        ]
        try:
            subprocess.run(cmd, capture_output=True, timeout=600, check=True)
        except subprocess.CalledProcessError:
            logging.warning("Копирование аудио не удалось при SAR-фиксе, перекодируем аудио в AAC")
            cmd_full = [
                "ffmpeg", "-y", "-i", file_path,
                "-c:v", "libx264",
                "-preset", "faster",
                "-crf", "23",
                "-profile:v", "high",
                "-level", "4.1",
                "-pix_fmt", "yuv420p",
                "-vf", "scale=trunc(iw*sar/2)*2:trunc(ih/2)*2,setsar=1",
                "-c:a", "aac", "-b:a", "192k",
                "-movflags", "+faststart",
                output_path,
            ]
            try:
                subprocess.run(cmd_full, capture_output=True, timeout=600, check=True)
            except Exception:
                logging.exception("SAR-фикс (полное перекодирование) не удалось для %s", file_path)
                try:
                    os.remove(output_path)
                except OSError:
                    pass
                return file_path, False
        except Exception:
            logging.exception("SAR-фикс (перекодирование) не удалось для %s", file_path)
            try:
                os.remove(output_path)
            except OSError:
                pass
            return file_path, False
    else:
        # Быстрый фикс через bitstream filter (без перекодирования)
        logging.info("SAR=%s, исправляем на 1:1 (без перекодирования): %s", sar, file_path)
        cmd = [
            "ffmpeg", "-y", "-i", file_path,
            "-c", "copy",
            "-bsf:v", "h264_metadata=sample_aspect_ratio=1/1",
            "-movflags", "+faststart",
            output_path,
        ]
        try:
            subprocess.run(cmd, capture_output=True, timeout=60, check=True)
        except Exception:
            logging.exception("Не удалось исправить SAR для %s", file_path)
            try:
                os.remove(output_path)
            except OSError:
                pass
            return file_path, False

    if not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
        logging.error("Файл после SAR-фикса пустой: %s", output_path)
        try:
            os.remove(output_path)
        except OSError:
            pass
        return file_path, False

    # Заменяем оригинал
    try:
        os.remove(file_path)
        os.replace(output_path, file_path)
    except OSError:
        logging.exception("Не удалось заменить файл после SAR-фикса: %s", file_path)
        if os.path.exists(output_path):
            return output_path, force_reencode
        return file_path, False

    logging.info("SAR исправлен на 1:1 (reencode=%s): %s", force_reencode, file_path)
    return file_path, force_reencode


def ensure_h264(file_path: str) -> tuple[str, bool, str | None]:
    """Перекодирует видео в H.264, если текущий кодек несовместим с Apple.

    Возвращает (путь_к_файлу, было_перекодировано, исходный_кодек).
    H.265 (HEVC), VP9, AV1 и другие кодеки вызывают зависание видео
    на первом кадре в Telegram на iOS/macOS — звук идёт, картинка нет.
    """
    codec = _get_video_codec(file_path)
    if codec is None:
        logging.warning("Не удалось определить кодек для %s, пропускаем перекодирование", file_path)
        return file_path, False, None

    if codec == "h264":
        logging.info("Видео уже в H.264, перекодирование не требуется: %s", file_path)
        return file_path, False, codec

    logging.info(
        "Видеокодек %s не совместим с Apple, перекодируем в H.264: %s",
        codec, file_path,
    )

    base, ext = os.path.splitext(file_path)
    output_path = f"{base}_h264{ext}"

    # -vf scale=...,setsar=1: «впекает» SAR в реальные пиксели, затем
    # выставляет SAR 1:1. Без scale одного setsar=1 недостаточно — если
    # исходное видео (VP9 шортсы и т.п.) хранится с нестандартным SAR,
    # плеер на iPhone покажет сплющенную картинку, т.к. Telegram/iOS
    # не всегда корректно применяют SAR из контейнера.
    # trunc(...*sar/2)*2 — ширина с учётом SAR, округлённая до чётного
    # (H.264 требует чётные размеры). Если SAR=1:1, scale — identity.
    # -pix_fmt yuv420p: максимальная совместимость с Apple-устройствами
    # (некоторые исходники в yuv444p/yuv422p, которые iOS не отображает).
    ffmpeg_cmd = [
        "ffmpeg", "-y", "-i", file_path,
        "-c:v", "libx264",
        "-preset", "faster",
        "-crf", "23",
        "-profile:v", "high",
        "-level", "4.1",
        "-pix_fmt", "yuv420p",
        "-vf", "scale=trunc(iw*sar/2)*2:trunc(ih/2)*2,setsar=1",
        "-c:a", "copy",
        "-movflags", "+faststart",
        output_path,
    ]

    try:
        subprocess.run(ffmpeg_cmd, capture_output=True, timeout=600, check=True)
    except subprocess.CalledProcessError:
        # Аудиокодек не совместим с mp4-контейнером — перекодируем и аудио
        logging.warning("Копирование аудио не удалось, перекодируем аудио в AAC")
        ffmpeg_cmd_full = [
            "ffmpeg", "-y", "-i", file_path,
            "-c:v", "libx264",
            "-preset", "faster",
            "-crf", "23",
            "-profile:v", "high",
            "-level", "4.1",
            "-pix_fmt", "yuv420p",
            "-vf", "scale=trunc(iw*sar/2)*2:trunc(ih/2)*2,setsar=1",
            "-c:a", "aac", "-b:a", "192k",
            "-movflags", "+faststart",
            output_path,
        ]
        try:
            subprocess.run(ffmpeg_cmd_full, capture_output=True, timeout=600, check=True)
        except Exception:
            logging.exception("FFmpeg перекодирование (полное) не удалось для %s", file_path)
            try:
                os.remove(output_path)
            except OSError:
                pass
            return file_path, False, codec
    except Exception:
        logging.exception("FFmpeg перекодирование не удалось для %s", file_path)
        try:
            os.remove(output_path)
        except OSError:
            pass
        return file_path, False, codec

    if not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
        logging.error("Перекодированный файл пустой или отсутствует: %s", output_path)
        try:
            os.remove(output_path)
        except OSError:
            pass
        return file_path, False, codec

    # Заменяем оригинал перекодированным файлом
    try:
        os.remove(file_path)
        os.replace(output_path, file_path)
    except OSError:
        logging.exception("Не удалось заменить %s перекодированным файлом", file_path)
        if os.path.exists(output_path):
            return output_path, True, codec
        return file_path, False, codec

    logging.info(
        "Перекодирование завершено: %s (кодек %s -> h264)",
        file_path, codec,
    )
    return file_path, True, codec


def download_preview_image(url: str | None) -> bytes | None:
    """Скачивает превью-картинку по URL и возвращает байты изображения.

    Используется для отправки превью в сообщении выбора качества.
    Instagram/TikTok CDN блокируют прямой доступ по URL из серверов Telegram,
    поэтому скачиваем картинку локально и отправляем как файл.
    """
    if not url:
        return None
    try:
        resp = requests.get(url, timeout=15, headers={"User-Agent": USER_AGENT})
        resp.raise_for_status()
        if not resp.content or len(resp.content) < 100:
            return None
        return resp.content
    except Exception:
        logging.debug("Не удалось скачать превью-картинку: %s", url)
        return None


def download_thumbnail(url: str | None, data_dir: str) -> str | None:
    """Скачивает превью-картинку по URL и возвращает путь к файлу.

    Telegram требует JPEG-изображение не больше 200 КБ и 320x320 пикселей
    для превью видео. Скачиваем картинку и при необходимости конвертируем
    через ffmpeg в подходящий JPEG.
    """
    if not url:
        return None

    thumb_path = os.path.join(data_dir, f"thumb_{int(time.time())}.jpg")
    try:
        resp = requests.get(url, timeout=15, headers={"User-Agent": USER_AGENT})
        resp.raise_for_status()
        if not resp.content or len(resp.content) < 100:
            return None

        raw_path = os.path.join(data_dir, f"thumb_raw_{int(time.time())}")
        with open(raw_path, "wb") as f:
            f.write(resp.content)

        # Конвертируем в JPEG 320x320 (вписываем, сохраняя пропорции)
        try:
            subprocess.run(
                [
                    "ffmpeg", "-y", "-i", raw_path,
                    "-vf", "scale=320:320:force_original_aspect_ratio=decrease",
                    "-q:v", "5",
                    thumb_path,
                ],
                capture_output=True, timeout=15, check=True,
            )
        except Exception:
            logging.debug("ffmpeg-конвертация превью не удалась, используем оригинал")
            # Если ffmpeg не сработал, пробуем использовать файл как есть
            if resp.headers.get("content-type", "").startswith("image/jpeg"):
                os.replace(raw_path, thumb_path)
            else:
                try:
                    os.remove(raw_path)
                except OSError:
                    pass
                return None
        else:
            try:
                os.remove(raw_path)
            except OSError:
                pass

        if os.path.exists(thumb_path) and os.path.getsize(thumb_path) > 0:
            return thumb_path

    except Exception:
        logging.debug("Не удалось скачать превью: %s", url)

    try:
        os.remove(thumb_path)
    except OSError:
        pass
    return None


def _detect_image_ext(content: bytes) -> str | None:
    """Расширение картинки по magic-байтам (jpeg/png/webp/heic), иначе None."""
    if len(content) < 12:
        return None
    if content[:3] == b"\xff\xd8\xff":
        return "jpg"
    if content[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        return "webp"
    if content[4:12] in (b"ftypheic", b"ftypheix", b"ftypmif1"):
        return "heic"
    return None


# Жёсткий кап тела картинки с CDN Instagram (недоверенный источник).
_MAX_INSTAGRAM_IMAGE_BYTES = 30 * 1024 * 1024


class _SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Редирект-хендлер с SSRF-ревалидацией каждого хопа.

    URL картинки приходит из ответа приватного API (недоверенный): редирект
    разрешён только на https и хост *.cdninstagram.com / *.fbcdn.net. Чужой
    хоп → None (редирект не следуем, наверх уходит исходный 3xx).
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not instagram_api.is_allowed_media_host(newurl):
            try:
                host = urlparse(newurl).hostname or "?"
            except ValueError:
                host = "?"
            logging.warning(
                "Instagram CDN: редирект на недопустимый хост отклонён (%s)", host,
            )
            return None
        return super().redirect_request(req, fp, code, msg, headers, newurl)


# Отдельный opener: глобальный urlopen ходил бы по редиректам на любой хост.
_IG_IMAGE_OPENER = urllib.request.build_opener(_SafeRedirectHandler())


def _fetch_instagram_image(url: str, timeout: float = 30.0) -> bytes | None:
    """Скачивает картинку с CDN Instagram (UA обязателен, cookie не нужен).

    Ровно один повтор только при сетевой ошибке; HTTP-код != 200 → None.
    URL из ответа API недоверенный: редиректы следуются только на https и
    CDN-хосты Instagram (каждый хоп ревалидируется), тело читается с капом.
    """
    try:
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    except ValueError as exc:
        # Control-символы или битый IPv6-литерал в URL — Request не собирается.
        logging.debug("Instagram CDN: некорректный URL картинки: %s", exc)
        return None
    for attempt in (1, 2):
        try:
            with _IG_IMAGE_OPENER.open(request, timeout=timeout) as response:
                if response.status != 200:
                    logging.debug("Instagram CDN: HTTP %s при скачивании фото", response.status)
                    return None
                content = response.read(_MAX_INSTAGRAM_IMAGE_BYTES + 1)
        except urllib.error.HTTPError as exc:
            logging.debug("Instagram CDN: HTTP %s при скачивании фото", exc.code)
            return None
        except http.client.InvalidURL as exc:
            logging.debug("Instagram CDN: некорректный URL картинки: %s", exc)
            return None
        except ValueError as exc:
            logging.debug("Instagram CDN: некорректный URL картинки: %s", exc)
            return None
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            logging.debug(
                "Instagram CDN: сетевая ошибка (попытка %d/2): %s", attempt, exc,
            )
            if attempt == 2:
                break
            time.sleep(1.0)
            continue
        if len(content) > _MAX_INSTAGRAM_IMAGE_BYTES:
            logging.warning(
                "Instagram CDN: файл слишком большой (> %d байт), пропускаю",
                _MAX_INSTAGRAM_IMAGE_BYTES,
            )
            return None
        return content
    return None


def merge_carousel_media(
    children: list[dict],
    existing_media: list[dict],
    fetch_photo: Callable[[dict, int], dict | None],
) -> list[dict]:
    """Сшивает порядок детей поста со скачанными yt-dlp файлами.

    Существующие файлы делятся на видео и фото. Точная раскладка (видео-ребёнок
    берёт следующий существующий видео-файл, фото-ребёнок — следующий
    существующий фото-файл) возможна, только когда количества обоих типов
    сходятся с детьми. При любом расхождении существующий порядок НЕ трогаем
    (autonumber), все фото-дети скачиваются через fetch_photo и дописываются в
    конец, а существующие фото-файлы в merge не попадают (остаются в каталоге,
    не отправляются — задвоения нет): иначе содержимое молча попадает в чужие
    слоты альбома. Фото-ребёнок без своего файла скачивается через
    fetch_photo(child, idx) (None/исключение — warning и пропуск). Возвращает
    ordered list [{'path', 'is_video', 'width', 'height'}].
    """
    existing: list[dict] = [dict(item) for item in (existing_media or [])]
    video_files = [item for item in existing if item.get("is_video")]
    photo_files = [item for item in existing if not item.get("is_video")]
    child_list = list(children or [])
    video_children_count = sum(1 for child in child_list if child.get("is_video"))
    photo_children_count = len(child_list) - video_children_count
    exact_layout = (
        len(video_files) == video_children_count
        and (photo_children_count == 0 or len(photo_files) == photo_children_count)
    )
    if not exact_layout:
        logging.warning(
            "Instagram photo fallback: порядок элементов может не совпадать: "
            "частичное скачивание видео (файлов %d, видео-элементов %d), "
            "фото (файлов %d, фото-элементов %d)",
            len(video_files),
            video_children_count,
            len(photo_files),
            photo_children_count,
        )
        merged = list(video_files)
        photo_index = 0
        for child in child_list:
            if child.get("is_video"):
                continue
            photo = _fetch_merged_photo(child, photo_index, fetch_photo)
            photo_index += 1
            if photo:
                merged.append(photo)
        return merged
    merged: list[dict] = []
    video_index = 0
    photo_index = 0
    for child in child_list:
        if child.get("is_video"):
            if video_index < len(video_files):
                merged.append(video_files[video_index])
                video_index += 1
            else:
                logging.warning(
                    "Instagram photo fallback: для видео-элемента нет скачанного "
                    "файла, пропускаю",
                )
            continue
        if photo_index < len(photo_files):
            merged.append(photo_files[photo_index])
            photo_index += 1
            continue
        photo = _fetch_merged_photo(child, photo_index, fetch_photo)
        photo_index += 1
        if photo:
            merged.append(photo)
    return merged


def _fetch_merged_photo(
    child: dict,
    photo_index: int,
    fetch_photo: Callable[[dict, int], dict | None],
) -> dict | None:
    """Одно фото-ребёнок через fetch_photo; сбой — warning и None."""
    try:
        photo = fetch_photo(child, photo_index)
    except Exception as exc:  # noqa: BLE001 — сбой одного фото не должен валить карусель
        logging.warning("Instagram photo fallback: фото не скачалось: %s", exc)
        return None
    if not photo:
        logging.warning("Instagram photo fallback: фото пропущено (нет файла)")
        return None
    return photo


class YtDlpLogger:
    """Логгер-обёртка для перенаправления вывода yt-dlp в стандартный logging."""

    def debug(self, message: str) -> None:
        logging.debug("yt-dlp: %s", message)

    def warning(self, message: str) -> None:
        logging.warning("yt-dlp: %s", message)

    def error(self, message: str) -> None:
        logging.error("yt-dlp: %s", message)


@dataclass
class FormatOption:
    """Вариант качества для выбора пользователем."""
    label: str
    format_id: str
    height: int | None


# Домены, на которых живёт IG session-кука (sessionid). yt-dlp при каждом
# вызове перезаписывает cookiefile и вымывает session-куки Instagram —
# поэтому снимаем снапшот ДО вызова и мержим ПОСЛЕ каждого (см. restore).
_INSTAGRAM_SESSION_DOMAINS = ("instagram.com", "instagr.am")

# Префикс HttpOnly-куки в браузерном cookies.txt (соглашение curl/wget),
# регистр ровно такой, как в соглашении.
_HTTPONLY_PREFIX = "#HttpOnly_"

# Мерж под замком: два воркера очереди могут звать restore одновременно.
_INSTAGRAM_SESSION_LOCK = threading.Lock()


def _is_instagram_session_cookie(domain: str, name: str) -> bool:
    """sessionid на instagram.com / instagr.am и их поддоменах (см. тесты)."""
    if name != "sessionid":
        return False
    normalized = (domain or "").lower().removeprefix("#httponly_").removeprefix(".")
    return normalized in _INSTAGRAM_SESSION_DOMAINS or normalized.endswith(
        tuple(f".{item}" for item in _INSTAGRAM_SESSION_DOMAINS)
    )


def _normalize_session_line(line: str) -> str | None:
    """Нормализует строку cookies-файла в plain 7-поле или вернёт None.

    Вход — строка без краевых пробелов/переводов строк. Строка с префиксом
    `#HttpOnly_` разбирается как обычная (префикс снят), прочие `#`-строки —
    комментарий/шапка Netscape-файла. Результат — 7-полевая строка БЕЗ
    префикса: потребитель app.instagram_api.load_sessionid пропускает
    `#`-строки, поэтому снапшот и мерж идут plain. Неверное число полей,
    чужой домен или имя, пустое/непечатаемое значение, нечитаемый expires —
    None, а не ошибка.
    """
    if line.startswith(_HTTPONLY_PREFIX):
        line = line[len(_HTTPONLY_PREFIX):]
    elif line.startswith("#"):
        return None
    parts = line.split("\t")
    if len(parts) != 7:
        return None
    domain, domain_specified, path, secure, expires, name, value = parts
    if not _is_instagram_session_cookie(domain, name):
        return None
    if not value or not value.isprintable():
        # Непечатаемое значение load_sessionid всё равно отбросил бы —
        # в снапшот и файл такую строку не берём.
        return None
    try:
        normalized_expires = str(int(float(expires)))
    except (ValueError, OverflowError):
        # Мусорное expires чинить нечем — строку пропускаем (как дроп
        # мусорной строки в санитайзере).
        return None
    return "\t".join(
        [domain, domain_specified, path, secure, normalized_expires, name, value]
    )


def _read_instagram_session_lines_once(cookiefile: str) -> list[str]:
    """Один снимок Netscape-файла: НОРМАЛИЗОВАННЫЕ plain строки IG session-кукис.

    Строки `#HttpOnly_...` возвращаются БЕЗ префикса: потребители снапшота
    (load_sessionid, фото-фолбэк) пропускают `#`-строки, и
    вербатим-строка была бы для них невидима. Обычные строки нормализуются
    так же (`int(float(expires))`, как в _prepare_cookiefile), прочие
    `#`-строки, пустые, не-7-полевые и нечитаемые пропускаются. Дубликаты
    после нормализации убираются, порядок — как в файле.
    """
    try:
        with open(cookiefile, "r", encoding="utf-8") as handle:
            raw_lines = [line.rstrip("\n") for line in handle]
    except (OSError, UnicodeDecodeError) as exc:
        logging.warning("Файл cookies недоступен для снапшота IG session: %s", exc)
        return []

    session_lines: list[str] = []
    seen: set[str] = set()
    for line in raw_lines:
        stripped = line.strip()
        if not stripped:
            continue
        normalized = _normalize_session_line(stripped)
        if normalized is None or normalized in seen:
            continue
        seen.add(normalized)
        session_lines.append(normalized)
    return session_lines


def _read_instagram_session_lines(cookiefile: str) -> list[str]:
    """Снапшот IG session-кукис с защитой от среза середины чужого save.

    cookiefile пишут и без нашего лока, НЕАТОМАРНО (`open(file, "w")` —
    truncate + write, так закрывает YoutubeDL сам yt-dlp): снимок мог попасть
    на середину записи и молча увидеть усечённый файл → пустой эталон →
    защита вымывания выключена. Если из СУЩЕСТВУЮЩЕГО непустого файла не
    прочитано ни одной session-строки — перечитываем ОДИН раз.
    """
    session_lines = _read_instagram_session_lines_once(cookiefile)
    if session_lines:
        return session_lines
    try:
        non_empty = os.path.getsize(cookiefile) > 0
    except OSError:
        non_empty = False
    if non_empty:
        return _read_instagram_session_lines_once(cookiefile)
    return session_lines


def _is_instagram_session_line(line: str) -> bool:
    """Строка Netscape-файла — IG session-кука (предикат по 1-му и 6-му полю)."""
    parts = line.split("\t", 6)
    if len(parts) != 7:
        return False
    return _is_instagram_session_cookie(parts[0], parts[5])


def _session_cookie_key(line: str) -> tuple[str, str, str] | None:
    """Ключ кукисы из 7-полевой строки Netscape-файла: (домен, path, имя).

    Префикс `#HttpOnly_` у домена снимается, домен приводится к lower,
    ведущая точка снимается: одна и та же кукиса в разных текстовых формах
    (`#HttpOnly_.instagram.com` и `.instagram.com`) даёт один ключ — текст
    строк различается, а кукиса та же. Не-7-полевые и мусорные строки
    (комментарии/шапка файла) — None.
    """
    if line.startswith(_HTTPONLY_PREFIX):
        line = line[len(_HTTPONLY_PREFIX):]
    elif line.startswith("#"):
        return None
    parts = line.split("\t")
    if len(parts) != 7:
        return None
    domain, _domain_specified, path, _secure, _expires, name, _value = parts
    return (domain.lower().removeprefix("."), path, name)


def restore_instagram_session_cookies(
    cookiefile: str | None, reference_lines: list[str]
) -> int:
    """Дописывает отсутствующие IG session-куки из reference_lines в cookiefile.

    Возвращает число дописанных строк. Файл не трогается, если добавлять
    нечего. Запись — APPEND-ONLY: файл открывается в режиме "a", всё
    добавленное — одним `write`, без truncate и без подмены inode. Причина:
    cookiefile пишут и без нашего лока — yt-dlp при закрытии YoutubeDL
    сохраняет его НЕАТОМАРНО (truncate + write), админ-загрузка свежих кукис
    пишет `open(path, "wb")`; перезапись целого файла (tmp + os.replace)
    могла бы навсегда зафиксировать чужой усечённый снимок или откатить
    только что загруженные кукисы. Дописывание в "a" этого не делает:
    inode, права и текущее содержимое файла сохраняются, чужой недописанный
    save не осиротеет.

    Сравнение наличия — по КЛЮЧУ кукисы (домен/path/имя, см.
    _session_cookie_key), а не по тексту строки: в файле может уже лежать
    свежая строка той же кукисы в другой форме (`#HttpOnly_...`) — устаревшее
    значение из эталона поверх неё не дописывается, дубликаты в самом
    эталоне тоже схлопываются по ключу.

    Пишет строки reference как есть — эталон уже нормализован в plain
    читателем (без `#HttpOnly_`, иначе load_sessionid такие
    строки пропускают). Фильтр по предикату IG session-кукис остаётся как
    защита от мусора в reference. Если файл не оканчивается на "\\n"
    (чужой писатель мог дописать строку между нашим чтением и append) —
    перед добавленными строками prepend одного "\\n": в худшем случае лишняя
    пустая строка, для Netscape-парсеров безвредна. Лок держит связку
    read-decide-append. Любое исключение — logging.warning, никогда не
    raise (fail-open: загрузка важнее мержа; падение append на
    ENOSPC/EACCES — тоже warning).
    """
    if not cookiefile or not reference_lines:
        return 0

    missing: list[str] = []
    with _INSTAGRAM_SESSION_LOCK:
        try:
            with open(cookiefile, "r", encoding="utf-8") as handle:
                content = handle.read()
            current_lines = content.split("\n")
            if current_lines and current_lines[-1] == "":
                # Файл корректно заканчивается переводом строки — хвостовой
                # пустой элемент после split не строка файла.
                current_lines.pop()
            present = {
                key
                for key in (_session_cookie_key(line) for line in current_lines)
                if key is not None
            }
            seen: set[tuple[str, str, str]] = set()
            for line in reference_lines:
                if not _is_instagram_session_line(line):
                    continue
                key = _session_cookie_key(line)
                if key is None or key in present or key in seen:
                    continue
                seen.add(key)
                missing.append(line)
            if not missing:
                return 0

            prefix = "\n" if content and not content.endswith("\n") else ""
            with open(cookiefile, "a", encoding="utf-8") as handle:
                handle.write(prefix + "".join(f"{line}\n" for line in missing))
        except Exception as exc:  # noqa: BLE001 — fail-open: загрузка важнее мержа
            logging.warning(
                "Кукис Instagram sessionid не удалось восстановить в %s: %s",
                cookiefile, exc,
            )
            return 0

    logging.warning(
        "Кукис Instagram sessionid восстановлены после yt-dlp: %d шт.", len(missing),
    )
    return len(missing)


class VideoDownloader:
    """Класс для скачивания видео через yt-dlp."""

    def __init__(self, data_dir: str) -> None:
        self.data_dir = data_dir
        os.makedirs(self.data_dir, exist_ok=True)
        # Эталон IG session-кукис: снимаем с итогового cookiefile ДО любого
        # вызова yt-dlp (его write-back вымывает sessionid — см. restore).
        self._instagram_session_ref: list[str] = []
        self._last_instagram_restored = 0
        self.cookiefile = self._prepare_cookiefile()
        self._instagram_session_ref = (
            _read_instagram_session_lines(self.cookiefile) if self.cookiefile else []
        )

    def reload_cookies(self) -> str | None:
        """Перечитывает файл cookies с диска (после обновления)."""
        self.cookiefile = self._prepare_cookiefile()
        snapshot = (
            _read_instagram_session_lines(self.cookiefile) if self.cookiefile else []
        )
        if (
            not snapshot
            and self._instagram_session_ref
            and self.cookiefile
            and os.path.exists(self.cookiefile)
        ):
            # Старый эталон был, а новый снапшот пуст при существующем файле:
            # чаще всего снимок снова попал на середину не-атомарного save.
            # Защита вымывания не выключается молча — оставляем СТАРЫЙ эталон;
            # если session-куку вымыли по-настоящему, это видно по warning.
            logging.warning("Снапшот IG session-кукис пуст, хотя файл кукис существует")
            return self.cookiefile
        self._instagram_session_ref = snapshot
        return self.cookiefile

    def _prepare_cookiefile(self) -> str | None:
        """Подготавливает и санитизирует файл cookies (формат Netscape)."""
        if not COOKIES_FILE:
            return None
        if not os.path.exists(COOKIES_FILE):
            logging.warning("Файл cookies не найден: %s", COOKIES_FILE)
            return None

        sanitized_lines: list[str] = []
        has_magic = False
        changed = False
        normalized_expires = 0
        dropped_expires = 0

        with open(COOKIES_FILE, "r", encoding="utf-8") as handle:
            for line in handle:
                stripped = line.rstrip("\n")
                if stripped.startswith("# Netscape HTTP Cookie File"):
                    has_magic = True
                    continue
                if stripped.strip().startswith(("#", "$")) or stripped.strip() == "":
                    sanitized_lines.append(stripped)
                    continue
                parts = stripped.split("\t", 6)
                if len(parts) != 7:
                    changed = True
                    continue
                domain, domain_specified, path, secure, expires, name, value = parts
                if not expires.lstrip("-").isdigit():
                    # Свежие дампы кукис содержат float-expires (например
                    # «1811526512.091545») — такой файл не грузит ни stdlib,
                    # ни yt-dlp («invalid Netscape format cookies file»).
                    try:
                        expires = str(int(float(expires)))
                        changed = True
                        normalized_expires += 1
                    except (ValueError, OverflowError):
                        # Мусорное/пустое expires чинить нечем: строка с ним
                        # невалидна для MozillaCookieJar — дропаем её целиком.
                        changed = True
                        dropped_expires += 1
                        continue
                initial_dot = domain.startswith(".")
                domain_specified_flag = domain_specified.upper() == "TRUE"
                if initial_dot and not domain_specified_flag:
                    domain_specified = "TRUE"
                    changed = True
                elif not initial_dot and domain_specified_flag:
                    domain = f".{domain}"
                    changed = True
                sanitized_lines.append(
                    "\t".join([domain, domain_specified, path, secure, expires, name, value])
                )

        if not has_magic:
            changed = True

        if normalized_expires:
            logging.debug(
                "Файл cookies: нормализовано float-expires полей: %d", normalized_expires,
            )
        if dropped_expires:
            logging.debug(
                "Файл cookies: дропнуто строк с мусорным expires: %d", dropped_expires,
            )

        if not changed:
            return COOKIES_FILE

        sanitized_path = os.path.join(self.data_dir, "cookies.cleaned.txt")
        with open(sanitized_path, "w", encoding="utf-8") as handle:
            handle.write("# Netscape HTTP Cookie File\n")
            for line in sanitized_lines:
                handle.write(f"{line}\n")
        logging.warning("Файл cookies санитизирован и сохранён: %s", sanitized_path)
        return sanitized_path

    def _restore_instagram_session(self) -> None:
        """Мержит эталон IG session-кукис обратно в cookiefile после yt-dlp."""
        self._last_instagram_restored = restore_instagram_session_cookies(
            self.cookiefile, self._instagram_session_ref
        )

    @staticmethod
    def _is_vk_url(url: str) -> bool:
        """Проверяет, ведёт ли URL на VK/VK Video."""
        from urllib.parse import urlparse
        try:
            host = urlparse(url).hostname or ""
        except Exception:
            return False
        host = host.lower().removeprefix("www.")
        return host in ("vk.com", "vk.ru", "vkvideo.ru", "m.vk.com")

    def _base_opts(self, skip_download: bool = False, url: str = "") -> dict:
        """Формирует базовые опции для yt-dlp."""
        output_template = os.path.join(self.data_dir, "%(id)s.%(ext)s")
        opts: dict = {
            # Принудительно H.264 (AVC) видео + AAC аудио — работает на ВСЕХ
            # устройствах без перекодирования (iPhone, Android, десктоп).
            # VP9/AV1 не воспроизводятся в Telegram на iOS/macOS.
            # best[ext=mp4] подхватывает Instagram H.264 форматы, у которых
            # yt-dlp не может определить кодек (vcodec=null).
            "format": (
                "bestvideo[vcodec^=avc]+bestaudio[acodec^=mp4a]/"
                "bestvideo[vcodec^=avc]+bestaudio/"
                "best[ext=mp4]/"
                "bestvideo+bestaudio/"
                "best"
            ),
            # Предпочитаем H.264 при сортировке форматов — критично для
            # Apple-совместимости и отсутствия перекодирования.
            "format_sort": ["vcodec:h264"],
            "quiet": True,
            "skip_download": skip_download,
            "noplaylist": True,
            "outtmpl": output_template,
            "restrictfilenames": True,
            "user_agent": USER_AGENT,
            "logger": YtDlpLogger(),
            "remote_components": ["ejs:github"],
            # Принудительно mp4 при слиянии видео+аудио,
            # чтобы избежать webm, который Telegram не воспроизводит инлайн
            "merge_output_format": PREFERRED_VIDEO_FORMAT,
        }
        if VK_USERNAME and self._is_vk_url(url):
            opts["username"] = VK_USERNAME
        if VK_PASSWORD and self._is_vk_url(url):
            opts["password"] = VK_PASSWORD
        # JS-рантайм для YouTube n-challenge — top-level опция yt-dlp,
        # НЕ YouTube extractor arg. Формат: {'node': {'path': '/usr/bin/node'}}.
        # По умолчанию yt-dlp включает только deno; если deno нет — нужно
        # явно включить доступный рантайм, иначе n-challenge не решится
        # и YouTube отдаст только картинки вместо видеоформатов.
        if YOUTUBE_JS_RUNTIME:
            rt_config: dict = {}
            if YOUTUBE_JS_RUNTIME_PATH:
                rt_config["path"] = YOUTUBE_JS_RUNTIME_PATH
            opts["js_runtimes"] = {YOUTUBE_JS_RUNTIME: rt_config}
        extractor_args = opts.setdefault("extractor_args", {})
        youtube_args = extractor_args.setdefault("youtube", {})
        if YOUTUBE_PLAYER_CLIENTS:
            youtube_args["player_client"] = YOUTUBE_PLAYER_CLIENTS
        if self.cookiefile:
            opts["cookiefile"] = self.cookiefile
        logging.debug(
            "Опции yt-dlp: skip_download=%s format=%s merge_output=%s player_clients=%s js_runtimes=%s",
            skip_download,
            opts.get("format"),
            opts.get("merge_output_format"),
            YOUTUBE_PLAYER_CLIENTS or [],
            opts.get("js_runtimes", {}),
        )
        return opts

    def get_info(self, url: str) -> dict:
        """Получает метаданные видео без скачивания."""
        opts = self._base_opts(skip_download=True, url=url)
        # Для получения метаданных используем максимально допустимый формат,
        # чтобы не получить «Requested format is not available» на DASH-only
        # видео (YouTube Shorts и др.), где нет комбинированных форматов.
        # Реальный выбор формата происходит при скачивании.
        opts["format"] = "bestvideo*+bestaudio/best"
        try:
            with YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=False)
            self._restore_instagram_session()
            return info
        except Exception as exc:
            if not _is_youtube_format_error(exc):
                raise
            # YouTube: ошибка «Requested format is not available» часто
            # транзиентна (rate-limit, ротация форматов, SABR flap).
            # Ждём 3 секунды и пробуем те же настройки ещё раз.
            logging.info(
                "YouTube get_info: формат недоступен, retry через 3с: %s", url,
            )
            import time as _time
            _time.sleep(3)
            try:
                with YoutubeDL(opts) as ydl:
                    info = ydl.extract_info(url, download=False)
                self._restore_instagram_session()
                return info
            except Exception:
                pass
            # Простой retry не помог — пробуем альтернативные player_client
            # (SABR/PO-token/age-gate затрагивают разных клиентов по-разному).
            return self._retry_with_fallback_clients(url, opts, exc)

    def _retry_with_fallback_clients(
        self,
        url: str,
        base_opts: dict,
        original_exc: Exception,
        download: bool = False,
    ) -> dict:
        """Повторяет запрос к YouTube с альтернативными player_client."""
        import copy

        for clients in _YOUTUBE_RETRY_CLIENT_SETS:
            try:
                opts = copy.deepcopy(base_opts)
                opts.setdefault("extractor_args", {})["youtube"] = {
                    "player_client": clients,
                }
                logging.info(
                    "YouTube retry с player_client=%s: %s", clients, url,
                )
                with YoutubeDL(opts) as ydl:
                    info = ydl.extract_info(url, download=download)
                self._restore_instagram_session()
                return info
            except Exception:
                continue

        # Последняя попытка: без cookies (иногда cookies вызывают
        # SABR-enforcement, а без них YouTube отдаёт обычные форматы).
        try:
            opts = copy.deepcopy(base_opts)
            opts.pop("cookiefile", None)
            opts.setdefault("extractor_args", {})["youtube"] = {
                "player_client": ["default"],
            }
            logging.info(
                "YouTube retry без cookies, player_client=default: %s", url,
            )
            with YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=download)
            self._restore_instagram_session()
            return info
        except Exception:
            pass

        raise original_exc

    def list_formats(self, info: dict) -> tuple[list[FormatOption], bool]:
        """Извлекает список доступных форматов (разрешений) из метаданных.

        Возвращает (список_форматов, h264_unavailable). Флаг h264_unavailable
        равен True, если ни одного H.264 формата не найдено и показаны VP9/AV1
        (fallback) — полезно для предупреждения пользователей iPhone.
        """
        formats = info.get("formats", [])
        # (FormatOption, bitrate, is_h264) — H.264 приоритетнее для Apple-совместимости
        options: dict[str, tuple[FormatOption, float, bool]] = {}
        raw_heights: list[tuple[str, int | None]] = []
        for fmt in formats:
            vcodec = fmt.get("vcodec")
            if vcodec in (None, "none"):
                # Instagram H.264 форматы: yt-dlp не определяет кодек (vcodec=null),
                # но ffprobe подтверждает H.264. Включаем mp4-форматы с высотой —
                # это позволяет пользователю выбрать качество вместо слепого best.
                if not (vcodec is None and fmt.get("height") and (fmt.get("ext") or "").lower() == "mp4"):
                    continue
            height = fmt.get("height")
            if height is None:
                format_note = fmt.get("format_note") or ""
                resolution = fmt.get("resolution") or ""
                combined = f"{format_note} {resolution}"
                match = re.search(r"(\d{3,4})p", combined)
                if match:
                    height = int(match.group(1))
                else:
                    match = re.search(r"\d{3,4}x(\d{3,4})", combined)
                    if match:
                        height = int(match.group(1))
            format_id = fmt.get("format_id")
            raw_heights.append((str(format_id), height))
            if height is None or format_id is None:
                continue
            if height < 144:
                continue
            label = f"{height}p"
            current = options.get(label)
            current_tbr = float(fmt.get("tbr") or 0)
            is_h264 = _is_h264(fmt.get("vcodec"))
            # Instagram mp4 с неизвестным кодеком — считаем H.264
            # (yt-dlp не определяет, но ffprobe подтверждает H.264)
            if not is_h264 and fmt.get("vcodec") is None and (fmt.get("ext") or "").lower() == "mp4":
                is_h264 = True
            if current is None:
                options[label] = (
                    FormatOption(label=label, format_id=format_id, height=height),
                    current_tbr,
                    is_h264,
                )
            else:
                prev_is_h264 = current[2]
                prev_tbr = current[1]
                # Предпочитаем H.264 для совместимости с Apple-устройствами;
                # при одинаковом типе кодека выбираем больший битрейт
                prefer_new = False
                if is_h264 and not prev_is_h264:
                    prefer_new = True
                elif is_h264 == prev_is_h264 and current_tbr > prev_tbr:
                    prefer_new = True
                if prefer_new:
                    options[label] = (
                        FormatOption(label=label, format_id=format_id, height=height),
                        current_tbr,
                        is_h264,
                    )
        # Показываем только разрешения, для которых есть H.264.
        # Это гарантирует, что пользователь не сможет выбрать VP9/AV1-only
        # формат (например, 1440p/4K на YouTube), который не воспроизводится
        # на iPhone. Если H.264 нет вообще — показываем все (fallback).
        h264_options = {k: v for k, v in options.items() if v[2]}
        h264_unavailable = bool(options) and not h264_options
        if h264_options:
            options = h264_options

        sorted_options = sorted(
            (value[0] for value in options.values()),
            key=lambda opt: opt.height or 0,
            reverse=True,
        )
        if formats:
            logging.info(
                "Исходные форматы: %s",
                ", ".join(
                    f"{format_id or 'unknown'}:{height or 'n/a'}"
                    for format_id, height in raw_heights
                ),
            )
            logging.info(
                "Доступные варианты качества: %s (h264_unavailable=%s)",
                ", ".join(option.label for option in sorted_options) or "нет",
                h264_unavailable,
            )
        return sorted_options, h264_unavailable

    def download(
        self,
        url: str,
        format_id: str | None,
        audio_only: bool = False,
        progress_callback: Callable[[dict], None] | None = None,
    ) -> tuple[str, dict]:
        """Скачивает видео/аудио и возвращает (путь_к_файлу, метаданные)."""
        ydl_opts = self._base_opts(url=url)
        if audio_only:
            ydl_opts["format"] = "bestaudio/best"
            # Для аудио предпочитаем m4a/mp3 вместо webm/opus
            ydl_opts["merge_output_format"] = None
            ydl_opts["postprocessors"] = [
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "m4a",
                }
            ]
        elif format_id:
            # Предпочитаем AAC аудио — Opus в mp4 не воспроизводится на Apple.
            # Если запрошенный format_id окажется недоступен — фолбек на лучший
            # H.264, чтобы никогда не скачать VP9/AV1 случайно.
            # best[ext=mp4] — для Instagram H.264 с неопределённым кодеком.
            ydl_opts["format"] = (
                f"{format_id}+bestaudio[acodec^=mp4a]/"
                f"{format_id}+bestaudio/"
                "bestvideo[vcodec^=avc]+bestaudio[acodec^=mp4a]/"
                "bestvideo[vcodec^=avc]+bestaudio/"
                "best[ext=mp4]/"
                "bestvideo+bestaudio/"
                "best"
            )
        if progress_callback:
            ydl_opts["progress_hooks"] = [progress_callback]
        try:
            with YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                file_path = ydl.prepare_filename(info)
            self._restore_instagram_session()
        except Exception as exc:
            if not _is_youtube_format_error(exc):
                raise
            # Транзиентная ошибка YouTube — ждём 3с и пробуем ещё раз
            # с теми же настройками (ротация форматов, rate-limit flap).
            logging.warning(
                "YouTube download: формат недоступен, retry через 3с: %s", url,
            )
            import time as _time
            _time.sleep(3)
            try:
                with YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(url, download=True)
                    file_path = ydl.prepare_filename(info)
                self._restore_instagram_session()
            except Exception:
                # Простой retry не помог — пробуем fallback-клиенты
                # с базовым форматом (без привязки к конкретному format_id).
                logging.warning(
                    "YouTube download: retry не помог, fallback-клиенты: %s", url,
                )
                fallback_opts = self._base_opts(url=url)
                if progress_callback:
                    fallback_opts["progress_hooks"] = [progress_callback]
                info = self._retry_with_fallback_clients(
                    url, fallback_opts, exc, download=True,
                )
                with YoutubeDL(fallback_opts) as ydl_fb:
                    file_path = ydl_fb.prepare_filename(info)
                self._restore_instagram_session()
        if info.get("_filename"):
            file_path = info["_filename"]
        # После постобработки расширение могло измениться — проверяем наличие файла
        if not os.path.exists(file_path):
            # Пробуем типичные расширения после конвертации
            base = os.path.splitext(file_path)[0]
            for ext in ("mp4", "m4a", "mp3", "mkv"):
                candidate = f"{base}.{ext}"
                if os.path.exists(candidate):
                    file_path = candidate
                    break
        safe_path = self._rename_to_safe_filename(file_path, info)
        return safe_path, info

    def download_carousel(
        self,
        url: str,
        progress_callback: Callable[[dict], None] | None = None,
    ) -> tuple[list[dict], dict]:
        """Скачивает все элементы карусели (Instagram sidecar) в отдельную папку.

        Возвращает (media, info), где media — список словарей в порядке
        элементов карусели: {'path', 'is_video', 'width', 'height'}.
        Папку с файлами (общий родитель) вызывающий код обязан удалить после
        отправки.
        """
        import shutil
        import tempfile

        work_dir = tempfile.mkdtemp(prefix="carousel_", dir=self.data_dir)
        media: list[dict] = []
        try:
            ydl_opts = self._base_opts(url=url)
            # Карусель — это плейлист, поэтому noplaylist выключаем, иначе yt-dlp
            # вернёт только один элемент.
            ydl_opts["noplaylist"] = False
            # Один битый элемент карусели не должен валить всю загрузку.
            ydl_opts["ignoreerrors"] = True
            # Нумеруем файлы по порядку, чтобы сохранить очерёдность в альбоме.
            ydl_opts["outtmpl"] = os.path.join(work_dir, "%(autonumber)03d.%(ext)s")
            if progress_callback:
                ydl_opts["progress_hooks"] = [progress_callback]

            with YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
            self._restore_instagram_session()

            video_exts = {"mp4", "mov", "mkv", "webm", "m4v"}
            photo_exts = {"jpg", "jpeg", "png", "webp", "heic"}
            for name in sorted(os.listdir(work_dir)):
                full = os.path.join(work_dir, name)
                if not os.path.isfile(full):
                    continue
                ext = os.path.splitext(name)[1].lower().lstrip(".")
                is_video = ext in video_exts
                is_photo = ext in photo_exts
                if not (is_video or is_photo):
                    continue
                width = height = None
                if is_video:
                    try:
                        width, height = get_video_dimensions(full)
                    except Exception:
                        pass
                media.append({
                    "path": full,
                    "is_video": is_video,
                    "width": width,
                    "height": height,
                })

            # Фолбэк фото из приватного API Instagram: yt-dlp на фото-постах не
            # отдаёт файлов (плейлист с пустыми entries). Триггер — только при
            # нехватке элементов, чтобы полная видео-карусель не дёргала API.
            expected = (info or {}).get("playlist_count") or len((info or {}).get("entries") or [])
            if expected and len(media) < expected:
                sessionid = instagram_api.load_sessionid(self.cookiefile)
                if not sessionid:
                    logging.warning("Instagram photo fallback: sessionid не найден, пропускаю")
                    return media, (info or {})
                media_item = instagram_api.fetch_media_info(url, sessionid)
                if not media_item:
                    logging.warning("Instagram photo fallback: медиа недоступно через API")
                    return media, (info or {})
                children = instagram_api.iter_photo_children(media_item)

                def fetch_photo(child: dict, idx: int) -> dict | None:
                    return self._download_instagram_photo(child, work_dir, idx)

                media = merge_carousel_media(children, media, fetch_photo)
                logging.info(
                    "Instagram photo fallback: получено фото %d из %d элементов (url=%s)",
                    sum(1 for item in media if not item.get("is_video")),
                    len(children),
                    url,
                )
            return media, (info or {})
        finally:
            # Пустой media (ранние возвраты фолбэка, пустой результат после
            # merge, исключение yt-dlp) — каталог пуст или ненужен, убираем на
            # ЛЮБОМ пути выхода: вызывающий код удаляет папку только при
            # непустом media, иначе carousel_* утекает в data_dir навсегда.
            if not media:
                shutil.rmtree(work_dir, ignore_errors=True)

    def _download_instagram_photo(
        self, child: dict, work_dir: str, idx: int
    ) -> dict | None:
        """Скачивает одно фото-ребёнок поста в work_dir как ig{idx:03d}.<ext>.

        Возвращает media-словарь {'path', 'is_video': False, 'width', 'height'}
        либо None (нет ссылки, чужой хост, плохой контент, ошибка сохранения).
        Префикс ig не конфликтует с autonumber-нумерацией yt-dlp.
        """
        image_url = child.get("image_url")
        if not image_url:
            logging.warning("Instagram photo fallback: у элемента нет ссылки на фото")
            return None
        if not instagram_api.is_allowed_media_host(image_url):
            try:
                # URL вроде «https://[::1/x.jpg» бросает ValueError при разборе.
                host = urlparse(image_url).hostname or "?"
            except ValueError:
                host = "?"
            logging.warning(
                "Instagram photo fallback: недопустимый хост картинки, пропускаю (%s)",
                host,
            )
            return None
        content = _fetch_instagram_image(image_url)
        if not content or len(content) <= 1024:
            logging.warning("Instagram photo fallback: картинка пустая или слишком мала")
            return None
        ext = _detect_image_ext(content)
        if ext == "heic":
            # Bot API InputMediaPhoto принимает PNG/JPEG/WEBP; HEIC валит всю
            # группу альбома — элемент пропускаем, файл не сохраняем.
            logging.warning("Instagram photo fallback: HEIC не поддерживается Telegram, пропускаю")
            return None
        if not ext:
            logging.warning("Instagram photo fallback: неизвестный формат картинки")
            return None
        path = os.path.join(work_dir, f"ig{idx:03d}.{ext}")
        try:
            with open(path, "wb") as handle:
                handle.write(content)
        except OSError as exc:
            logging.warning("Instagram photo fallback: не удалось сохранить фото: %s", exc)
            return None
        return {
            "path": path,
            "is_video": False,
            "width": child.get("width"),
            "height": child.get("height"),
        }

    def download_instagram_photos(self, url: str) -> list[dict]:
        """Фото-пост целиком через приватный API Instagram.

        Для кейса, когда get_info уже упал или пуст по форматам: создаёт
        work_dir внутри self.data_dir (удалит вызывающий код), качает все
        фото-дети поста и возвращает ordered media
        [{'path', 'is_video': False, 'width', 'height'}]. Пустой список, если
        фото не добыты (тогда временная папка удаляется здесь же).
        """
        import shutil
        import tempfile

        sessionid = instagram_api.load_sessionid(self.cookiefile)
        if not sessionid:
            logging.warning("Instagram photo fallback: sessionid не найден, пропускаю")
            return []
        media_item = instagram_api.fetch_media_info(url, sessionid)
        if not media_item:
            logging.warning("Instagram photo fallback: медиа недоступно через API")
            return []
        work_dir = tempfile.mkdtemp(prefix="carousel_", dir=self.data_dir)
        children = [
            child for child in instagram_api.iter_photo_children(media_item)
            if not child.get("is_video")
        ]
        media: list[dict] = []
        for idx, child in enumerate(children):
            try:
                photo = self._download_instagram_photo(child, work_dir, idx)
            except Exception as exc:  # noqa: BLE001 — гибель одного фото не валит пакет
                logging.warning("Instagram photo fallback: фото не скачалось: %s", exc)
                photo = None
            if photo:
                media.append(photo)
        if not media:
            shutil.rmtree(work_dir, ignore_errors=True)
        logging.info(
            "Instagram photo fallback: получено фото %d шт (url=%s)", len(media), url,
        )
        return media

    def get_direct_url(
        self,
        info: dict,
        format_id: str | None,
        audio_only: bool = False,
    ) -> tuple[str | None, int | None]:
        """Получает прямой HTTP URL для формата, который Telegram может воспроизвести.

        Для видео возвращает только URL mp4-контейнеров, чтобы избежать проблем с webm.
        """
        formats = info.get("formats") or []
        candidates = []
        if audio_only:
            candidates = [
                fmt
                for fmt in formats
                if fmt.get("acodec") not in (None, "none")
                and fmt.get("vcodec") in (None, "none")
            ]
        else:
            candidates = [
                fmt
                for fmt in formats
                if (
                    # Стандартные: известный видео + аудио кодек
                    (fmt.get("vcodec") not in (None, "none")
                     and fmt.get("acodec") not in (None, "none"))
                    or
                    # Instagram-стиль: mp4 с высотой, но vcodec/acodec не определены.
                    # Реально содержат H.264+AAC (подтверждено ffprobe).
                    (fmt.get("vcodec") is None
                     and fmt.get("height")
                     and (fmt.get("ext") or "").lower() == "mp4")
                )
            ]
        candidates = [
            fmt
            for fmt in candidates
            if fmt.get("url")
            and (fmt.get("protocol") or "").startswith("http")
            and fmt.get("protocol") not in ("m3u8", "m3u8_native", "dash")
        ]
        # Фильтруем webm/не-mp4 форматы для корректного воспроизведения в Telegram
        if not audio_only:
            mp4_candidates = [
                fmt for fmt in candidates
                if (fmt.get("ext") or "").lower() == "mp4"
            ]
            # Используем mp4-фильтр только если есть mp4-кандидаты
            if mp4_candidates:
                candidates = mp4_candidates
            # Предпочитаем H.264 (AVC) — VP9/AV1 в mp4 не воспроизводятся на Apple.
            # Instagram mp4 с неизвестным кодеком тоже считаем H.264-совместимыми
            # (yt-dlp не определяет кодек, но ffprobe подтверждает H.264).
            h264_candidates = [
                fmt for fmt in candidates
                if _is_h264(fmt.get("vcodec"))
                or (fmt.get("vcodec") is None and (fmt.get("ext") or "").lower() == "mp4")
            ]
            if h264_candidates:
                candidates = h264_candidates
        if format_id:
            exact = [fmt for fmt in candidates if str(fmt.get("format_id")) == format_id]
            if exact:
                fmt = max(exact, key=lambda item: float(item.get("tbr") or 0))
                return fmt.get("url"), fmt.get("filesize") or fmt.get("filesize_approx")
            requested = next(
                (fmt for fmt in formats if str(fmt.get("format_id")) == format_id),
                None,
            )
            target_height = requested.get("height") if requested else None
            if target_height:
                by_height = [fmt for fmt in candidates if fmt.get("height") == target_height]
                if by_height:
                    fmt = max(by_height, key=lambda item: float(item.get("tbr") or 0))
                    return (
                        fmt.get("url"),
                        fmt.get("filesize") or fmt.get("filesize_approx"),
                    )
        if not candidates:
            return None, None
        fmt = max(candidates, key=lambda item: float(item.get("tbr") or 0))
        return fmt.get("url"), fmt.get("filesize") or fmt.get("filesize_approx")

    def _rename_to_safe_filename(self, file_path: str, info: dict) -> str:
        """Переименовывает файл в безопасное имя (только ASCII-символы)."""
        base = info.get("id") or info.get("display_id") or info.get("title") or "video"
        timestamp = info.get("timestamp") or int(time.time())
        base = f"{base}_{timestamp}"
        safe_base = re.sub(r"[^0-9A-Za-z]+", "_", base).strip("_")
        if not safe_base:
            safe_base = "video"
        ext = info.get("ext") or os.path.splitext(file_path)[1].lstrip(".")
        if ext:
            candidate = os.path.join(self.data_dir, f"{safe_base}.{ext}")
        else:
            candidate = os.path.join(self.data_dir, safe_base)
        if candidate == file_path:
            return file_path
        unique_path = candidate
        counter = 2
        while os.path.exists(unique_path):
            suffix = f"_{counter}"
            if ext:
                unique_path = os.path.join(self.data_dir, f"{safe_base}{suffix}.{ext}")
            else:
                unique_path = os.path.join(self.data_dir, f"{safe_base}{suffix}")
            counter += 1
        try:
            os.replace(file_path, unique_path)
        except OSError:
            logging.exception("Не удалось переименовать %s -> %s", file_path, unique_path)
            return file_path
        return unique_path

    def resolve_format_id(self, info: dict, resolution: str | None) -> str | None:
        """Находит format_id по разрешению (например, '720p')."""
        if not resolution or resolution == "best":
            return None
        target_height = None
        if resolution.endswith("p"):
            try:
                target_height = int(resolution.rstrip("p"))
            except ValueError:
                target_height = None
        if target_height is None:
            return None
        for option in self.list_formats(info):
            if option.height == target_height:
                return option.format_id
        return None

    def split_video(self, file_path: str, max_size: int = 45 * 1024 * 1024) -> list[str]:
        """Разделяет видео на части примерно по max_size байт через FFmpeg."""
        import math
        import subprocess

        file_size = os.path.getsize(file_path)
        if file_size <= max_size:
            return [file_path]

        # Получаем длительность через ffprobe
        try:
            result = subprocess.run(
                [
                    "ffprobe", "-v", "error",
                    "-show_entries", "format=duration",
                    "-of", "default=noprint_wrappers=1:nokey=1",
                    file_path,
                ],
                capture_output=True, text=True, timeout=30,
            )
            duration = float(result.stdout.strip())
        except Exception:
            logging.exception("ffprobe не удался для %s", file_path)
            return [file_path]

        if duration <= 0:
            return [file_path]

        num_parts = math.ceil(file_size / max_size)
        segment_duration = duration / num_parts
        base, ext = os.path.splitext(file_path)

        parts = []
        for i in range(num_parts):
            start = i * segment_duration
            part_path = f"{base}_part{i + 1}{ext}"
            try:
                subprocess.run(
                    [
                        "ffmpeg", "-y", "-i", file_path,
                        "-ss", str(start), "-t", str(segment_duration),
                        "-c", "copy", part_path,
                    ],
                    capture_output=True, timeout=120,
                )
                if os.path.exists(part_path) and os.path.getsize(part_path) > 0:
                    parts.append(part_path)
                else:
                    logging.warning("Часть %d пуста или отсутствует: %s", i + 1, part_path)
            except Exception:
                logging.exception("FFmpeg: ошибка при разделении части %d файла %s", i + 1, file_path)

        return parts if parts else [file_path]

    def get_latest_entry(self, channel_url: str) -> dict | None:
        """Получает последнее видео с канала (flat-извлечение)."""
        ydl_opts = self._base_opts(skip_download=True, url=channel_url)
        ydl_opts["extract_flat"] = True
        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(channel_url, download=False)
        self._restore_instagram_session()
        entries = info.get("entries") or []
        if not entries:
            return None
        return entries[0]
