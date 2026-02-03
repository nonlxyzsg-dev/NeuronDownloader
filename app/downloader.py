import logging
import os
import re
import time
from dataclasses import dataclass
from collections.abc import Callable

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


class YtDlpLogger:
    def debug(self, message: str) -> None:
        logging.debug("yt-dlp: %s", message)

    def warning(self, message: str) -> None:
        logging.warning("yt-dlp: %s", message)

    def error(self, message: str) -> None:
        logging.error("yt-dlp: %s", message)

@dataclass
class FormatOption:
    label: str
    format_id: str
    height: int | None


class VideoDownloader:
    def __init__(self, data_dir: str) -> None:
        self.data_dir = data_dir
        os.makedirs(self.data_dir, exist_ok=True)
        self.cookiefile = self._prepare_cookiefile()

    def _prepare_cookiefile(self) -> str | None:
        if not COOKIES_FILE:
            return None
        if not os.path.exists(COOKIES_FILE):
            logging.warning("Cookie файл не найден: %s", COOKIES_FILE)
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
        logging.warning("Cookie файл очищен и сохранен в %s", sanitized_path)
        return sanitized_path

    def _base_opts(self, skip_download: bool = False) -> dict:
        output_template = os.path.join(self.data_dir, "%(id)s.%(ext)s")
        opts: dict = {
            "format": "bestvideo+bestaudio/best",
            "quiet": True,
            "skip_download": skip_download,
            "noplaylist": True,
            "outtmpl": output_template,
            "restrictfilenames": True,
            "user_agent": USER_AGENT,
            "logger": YtDlpLogger(),
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
            "yt-dlp options prepared: skip_download=%s format=%s player_clients=%s js_runtime=%s js_runtime_path=%s",
            skip_download,
            opts.get("format"),
            YOUTUBE_PLAYER_CLIENTS or [],
            YOUTUBE_JS_RUNTIME or "default",
            YOUTUBE_JS_RUNTIME_PATH or "default",
        )
        return opts

    def get_info(self, url: str) -> dict:
        with YoutubeDL(self._base_opts(skip_download=True)) as ydl:
            return ydl.extract_info(url, download=False)

    def list_formats(self, info: dict) -> list[FormatOption]:
        formats = info.get("formats", [])
        options: dict[str, tuple[FormatOption, float]] = {}
        raw_heights: list[tuple[str, int | None]] = []
        for fmt in formats:
            if fmt.get("vcodec") in (None, "none"):
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
            if current is None or current_tbr > current[1]:
                options[label] = (
                    FormatOption(label=label, format_id=format_id, height=height),
                    current_tbr,
                )
        sorted_options = sorted(
            (value[0] for value in options.values()),
            key=lambda opt: opt.height or 0,
            reverse=True,
        )
        if formats:
            logging.info(
                "Доступные форматы (сырые): %s",
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
        ydl_opts = self._base_opts()
        if audio_only:
            ydl_opts["format"] = "bestaudio/best"
        elif format_id:
            ydl_opts["format"] = f"{format_id}+bestaudio/best"
        if progress_callback:
            ydl_opts["progress_hooks"] = [progress_callback]
        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
        file_path = ydl.prepare_filename(info)
        if info.get("_filename"):
            file_path = info["_filename"]
        safe_path = self._rename_to_safe_filename(file_path, info)
        return safe_path, info

    def _rename_to_safe_filename(self, file_path: str, info: dict) -> str:
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
            logging.exception("Failed to rename file %s to %s", file_path, unique_path)
            return file_path
        return unique_path

    def resolve_format_id(self, info: dict, resolution: str | None) -> str | None:
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

    def get_latest_entry(self, channel_url: str) -> dict | None:
        ydl_opts = self._base_opts(skip_download=True)
        ydl_opts["extract_flat"] = True
        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(channel_url, download=False)
        entries = info.get("entries") or []
        if not entries:
            return None
        return entries[0]
