import logging
import os
from dataclasses import dataclass

from yt_dlp import YoutubeDL

from app.config import (
    COOKIES_FILE,
    USER_AGENT,
    VK_PASSWORD,
    VK_USERNAME,
    YOUTUBE_PLAYER_CLIENTS,
)

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
        output_template = os.path.join(self.data_dir, "%(title)s.%(ext)s")
        opts: dict = {
            "format": "bestvideo+bestaudio/best",
            "quiet": True,
            "skip_download": skip_download,
            "noplaylist": True,
            "outtmpl": output_template,
            "user_agent": USER_AGENT,
        }
        if VK_USERNAME:
            opts["username"] = VK_USERNAME
        if VK_PASSWORD:
            opts["password"] = VK_PASSWORD
        if YOUTUBE_PLAYER_CLIENTS:
            opts["extractor_args"] = {
                "youtube": {
                    "player_client": YOUTUBE_PLAYER_CLIENTS,
                }
            }
        if self.cookiefile:
            opts["cookiefile"] = self.cookiefile
        return opts

    def get_info(self, url: str) -> dict:
        with YoutubeDL(self._base_opts(skip_download=True)) as ydl:
            return ydl.extract_info(url, download=False)

    def list_formats(self, info: dict) -> list[FormatOption]:
        formats = info.get("formats", [])
        options: dict[str, tuple[FormatOption, float]] = {}
        for fmt in formats:
            height = fmt.get("height")
            format_id = fmt.get("format_id")
            if height is None or format_id is None:
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
        return sorted_options

    def download(self, url: str, format_id: str | None) -> tuple[str, dict]:
        ydl_opts = self._base_opts()
        if format_id:
            ydl_opts["format"] = f"{format_id}+bestaudio/best"
        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
        file_path = ydl.prepare_filename(info)
        if info.get("_filename"):
            file_path = info["_filename"]
        return file_path, info

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
