import logging
import os
import queue
import threading

from telebot import TeleBot

from app.config import POLL_INTERVAL_SECONDS, TELEGRAM_UPLOAD_TIMEOUT_SECONDS
from app.constants import STATUS_FAILED, STATUS_SUCCESS
from app.download_queue import DownloadManager
from app.downloader import VideoDownloader
from app.storage import Storage
from app.utils import format_caption, send_with_retry


class SubscriptionMonitor:
    def __init__(
        self,
        bot: TeleBot,
        storage: Storage,
        downloader: VideoDownloader,
        download_manager: DownloadManager,
    ) -> None:
        self.bot = bot
        self.storage = storage
        self.downloader = downloader
        self.download_manager = download_manager
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._stop_event = threading.Event()

    def start(self) -> None:
        self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        self._stop_event.set()
        self._thread.join(timeout=timeout)

    def _run(self) -> None:
        while not self._stop_event.is_set():
            self._poll_subscriptions()
            self._stop_event.wait(POLL_INTERVAL_SECONDS)

    def _poll_subscriptions(self) -> None:
        for user_id, channel_url, resolution, last_video_id in self.storage.list_subscriptions():
            if self.storage.is_blocked(user_id):
                continue
            try:
                latest = self.downloader.get_latest_entry(channel_url)
            except Exception:
                logging.exception(
                    "Failed to fetch latest entry for channel %s", channel_url,
                )
                continue
            if not latest:
                continue
            latest_id = latest.get("id") or latest.get("url")
            if latest_id is None:
                continue
            if latest_id == last_video_id:
                continue
            target_url = latest.get("url") or latest.get("id")
            if target_url is None:
                continue
            if not target_url.startswith("http") and "youtube" in channel_url:
                target_url = f"https://www.youtube.com/watch?v={target_url}"
            try:
                self.download_manager.submit_user(
                    user_id,
                    self._download_and_send,
                    user_id,
                    channel_url,
                    target_url,
                    latest_id,
                    resolution,
                )
            except queue.Full:
                logging.warning(
                    "Queue full, skipping subscription download for user %s",
                    user_id,
                )

    def _download_and_send(
        self,
        user_id: int,
        channel_url: str,
        target_url: str,
        latest_id: str,
        resolution: str | None,
    ) -> None:
        if self.storage.is_blocked(user_id):
            return
        if self._stop_event.is_set():
            logging.info("Skipping subscription download due to shutdown")
            return
        try:
            info = self.downloader.get_info(target_url)
            format_id = self.downloader.resolve_format_id(info, resolution)
            audio_only = resolution == "audio"
            file_path, info = self.downloader.download(
                target_url, format_id, audio_only=audio_only,
            )
            caption = format_caption(info.get("title") or "Видео")
            with open(file_path, "rb") as handle:
                if audio_only:
                    send_with_retry(
                        self.bot.send_audio,
                        user_id,
                        handle,
                        caption=caption,
                        timeout=TELEGRAM_UPLOAD_TIMEOUT_SECONDS,
                    )
                else:
                    send_with_retry(
                        self.bot.send_video,
                        user_id,
                        handle,
                        caption=caption,
                        timeout=TELEGRAM_UPLOAD_TIMEOUT_SECONDS,
                        supports_streaming=True,
                    )
            try:
                os.remove(file_path)
            except OSError:
                logging.exception(
                    "Failed to delete subscription file %s after upload",
                    file_path,
                )
            self.storage.update_last_video(user_id, channel_url, latest_id)
            self.storage.log_download(
                user_id, info.get("extractor_key", "unknown"), STATUS_SUCCESS,
            )
        except Exception as exc:
            self.storage.log_download(user_id, "unknown", STATUS_FAILED)
            logging.exception(
                "Subscription download failed for user %s, url %s: %s",
                user_id, target_url, exc,
            )
            try:
                self.bot.send_message(
                    user_id, f"Ошибка при скачивании подписки: {exc}",
                )
            except Exception:
                pass
