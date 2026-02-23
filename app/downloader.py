"""Скачивание видео через yt-dlp, разделение больших файлов (FFmpeg)."""

import json
import logging
import os
import re
import subprocess
import time
from dataclasses import dataclass
from collections.abc import Callable

import requests
from yt_dlp import YoutubeDL

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

    # -vf setsar=1: нормализует SAR (Sample Aspect Ratio) в 1:1 —
    # предотвращает растягивание видео на iPhone, которое возникает
    # при конвертации из VP9 (разный SAR в контейнере/потоке).
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
        "-vf", "setsar=1",
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
            "-vf", "setsar=1",
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


class VideoDownloader:
    """Класс для скачивания видео через yt-dlp."""

    def __init__(self, data_dir: str) -> None:
        self.data_dir = data_dir
        os.makedirs(self.data_dir, exist_ok=True)
        self.cookiefile = self._prepare_cookiefile()

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

        if not changed:
            return COOKIES_FILE

        sanitized_path = os.path.join(self.data_dir, "cookies.cleaned.txt")
        with open(sanitized_path, "w", encoding="utf-8") as handle:
            handle.write("# Netscape HTTP Cookie File\n")
            for line in sanitized_lines:
                handle.write(f"{line}\n")
        logging.warning("Файл cookies санитизирован и сохранён: %s", sanitized_path)
        return sanitized_path

    def _base_opts(self, skip_download: bool = False) -> dict:
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
        if VK_USERNAME:
            opts["username"] = VK_USERNAME
        if VK_PASSWORD:
            opts["password"] = VK_PASSWORD
        extractor_args = opts.setdefault("extractor_args", {})
        youtube_args = extractor_args.setdefault("youtube", {})
        if YOUTUBE_PLAYER_CLIENTS:
            youtube_args["player_client"] = YOUTUBE_PLAYER_CLIENTS
        if YOUTUBE_JS_RUNTIME:
            youtube_args["js_runtime"] = YOUTUBE_JS_RUNTIME
        if YOUTUBE_JS_RUNTIME_PATH:
            youtube_args["js_runtime_path"] = YOUTUBE_JS_RUNTIME_PATH
        if self.cookiefile:
            opts["cookiefile"] = self.cookiefile
        logging.debug(
            "Опции yt-dlp: skip_download=%s format=%s merge_output=%s player_clients=%s",
            skip_download,
            opts.get("format"),
            opts.get("merge_output_format"),
            YOUTUBE_PLAYER_CLIENTS or [],
        )
        return opts

    def get_info(self, url: str) -> dict:
        """Получает метаданные видео без скачивания."""
        with YoutubeDL(self._base_opts(skip_download=True)) as ydl:
            return ydl.extract_info(url, download=False)

    def list_formats(self, info: dict) -> list[FormatOption]:
        """Извлекает список доступных форматов (разрешений) из метаданных."""
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
                "Доступные варианты качества: %s",
                ", ".join(option.label for option in sorted_options) or "нет",
            )
        return sorted_options

    def download(
        self,
        url: str,
        format_id: str | None,
        audio_only: bool = False,
        progress_callback: Callable[[dict], None] | None = None,
    ) -> tuple[str, dict]:
        """Скачивает видео/аудио и возвращает (путь_к_файлу, метаданные)."""
        ydl_opts = self._base_opts()
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
                "best"
            )
        if progress_callback:
            ydl_opts["progress_hooks"] = [progress_callback]
        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
        file_path = ydl.prepare_filename(info)
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
        ydl_opts = self._base_opts(skip_download=True)
        ydl_opts["extract_flat"] = True
        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(channel_url, download=False)
        entries = info.get("entries") or []
        if not entries:
            return None
        return entries[0]
