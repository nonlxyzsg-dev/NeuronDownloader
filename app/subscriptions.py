import logging
import os
import queue
import threading

from telebot import TeleBot

from app.config import POLL_INTERVAL_SECONDS
from app.download_queue import DownloadManager
from app.downloader import VideoDownloader
from app.storage import Storage


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
        """Останавливает мониторинг подписок.
        
        Args:
            timeout: Максимальное время ожидания завершения потока в секундах
        """
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
            latest = self.downloader.get_latest_entry(channel_url)
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
            logging.info("Пропускаем загрузку подписки из-за завершения работы")
            return
        try:
            info = self.downloader.get_info(target_url)
            format_id = self.downloader.resolve_format_id(info, resolution)
            audio_only = resolution == "audio"
            file_path, info = self.downloader.download(target_url, format_id, audio_only=audio_only)
            caption = (info.get("title") or "Видео")[:1024]
            with open(file_path, "rb") as handle:
                if audio_only:
                    self.bot.send_audio(user_id, handle, caption=caption)
                else:
                    self.bot.send_video(user_id, handle, caption=caption)
            try:
                os.remove(file_path)
            except OSError:
                logging.exception(
                    "Failed to удалить файл подписки %s после отправки",
                    file_path,
                )
            self.storage.update_last_video(user_id, channel_url, latest_id)
            self.storage.log_download(user_id, info.get("extractor_key", "unknown"), "success")
        except Exception as exc:
            self.storage.log_download(user_id, "unknown", "failed")
            self.bot.send_message(user_id, f"Ошибка при скачивании: {exc}")
