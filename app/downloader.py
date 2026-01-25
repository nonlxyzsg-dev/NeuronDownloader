import os
from dataclasses import dataclass

from yt_dlp import YoutubeDL

from app.config import COOKIES_FILE

@dataclass
class FormatOption:
    label: str
    format_id: str
    height: int | None


class VideoDownloader:
    def __init__(self, data_dir: str) -> None:
        self.data_dir = data_dir
        os.makedirs(self.data_dir, exist_ok=True)

    def _base_opts(self) -> dict:
        opts: dict = {
            "quiet": True,
            "skip_download": True,
        }
        if COOKIES_FILE:
            opts["cookiefile"] = COOKIES_FILE
        return opts

    def get_info(self, url: str) -> dict:
        with YoutubeDL(self._base_opts()) as ydl:
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
        output_template = os.path.join(self.data_dir, "%(title)s.%(ext)s")
        ydl_opts = self._base_opts()
        ydl_opts.update(
            {
                "outtmpl": output_template,
                "merge_output_format": "mp4",
            }
        )
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
        ydl_opts = self._base_opts()
        ydl_opts["extract_flat"] = True
        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(channel_url, download=False)
        entries = info.get("entries") or []
        if not entries:
            return None
        return entries[0]
