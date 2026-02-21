import os

from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
DATA_DIR = os.getenv("DATA_DIR", "/workspace/NeuronDownloader/data")
DB_FILENAME = os.getenv("DB_FILENAME", "bot.db")
CLEANUP_INTERVAL_SECONDS = int(os.getenv("CLEANUP_INTERVAL_SECONDS", "600"))
CLEANUP_MAX_AGE_SECONDS = int(os.getenv("CLEANUP_MAX_AGE_SECONDS", "18000"))
COOKIES_FILE = os.getenv("COOKIES_FILE", "cookies.txt")
ADMIN_IDS = [
    int(value.strip())
    for value in os.getenv("ADMIN_IDS", "").split(",")
    if value.strip()
]
REQUIRED_CHAT_IDS = [
    int(value.strip())
    for value in os.getenv("REQUIRED_CHAT_IDS", "").split(",")
    if value.strip()
]
MAX_CONCURRENT_DOWNLOADS = int(os.getenv("MAX_CONCURRENT_DOWNLOADS", "2"))
MAX_QUEUE_SIZE = int(os.getenv("MAX_QUEUE_SIZE", "20"))
MAX_ACTIVE_TASKS_PER_USER = int(os.getenv("MAX_ACTIVE_TASKS_PER_USER", "1"))
FREE_DOWNLOAD_LIMIT = int(os.getenv("FREE_DOWNLOAD_LIMIT", "5"))
FREE_DOWNLOAD_WINDOW_SECONDS = int(os.getenv("FREE_DOWNLOAD_WINDOW_SECONDS", "86400"))
TELEGRAM_UPLOAD_TIMEOUT_SECONDS = int(
    os.getenv("TELEGRAM_UPLOAD_TIMEOUT_SECONDS", "600")
)
TELEBOT_LOG_LEVEL = os.getenv("TELEBOT_LOG_LEVEL", "CRITICAL").upper()
TELEGRAM_POLLING_ERROR_DELAY_SECONDS = int(
    os.getenv("TELEGRAM_POLLING_ERROR_DELAY_SECONDS", "5")
)
TELEGRAM_POLLING_DNS_DELAY_SECONDS = int(
    os.getenv("TELEGRAM_POLLING_DNS_DELAY_SECONDS", "30")
)
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
VK_USERNAME = os.getenv("VK_USERNAME", "")
VK_PASSWORD = os.getenv("VK_PASSWORD", "")
SUPPORTED_YOUTUBE_PLAYER_CLIENTS = [
    "default",
    "all",
    "android",
    "android_vr",
    "ios",
    "web",
    "web_safari",
    "web_embedded",
    "web_music",
    "web_creator",
    "mweb",
    "tv",
    "tv_simply",
    "tv_downgraded",
]
YOUTUBE_PLAYER_CLIENTS = [
    value.strip()
    for value in os.getenv("YOUTUBE_PLAYER_CLIENTS", "android,web").split(",")
    if value.strip() and value.strip() in SUPPORTED_YOUTUBE_PLAYER_CLIENTS
]
YOUTUBE_JS_RUNTIME = os.getenv("YOUTUBE_JS_RUNTIME", "").strip()
YOUTUBE_JS_RUNTIME_PATH = os.getenv("YOUTUBE_JS_RUNTIME_PATH", "").strip()
USER_AGENT = os.getenv(
    "USER_AGENT",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36",
)
ENABLE_REACTIONS = os.getenv("ENABLE_REACTIONS", "true").strip().lower() in (
    "1",
    "true",
    "yes",
    "on",
)
# URL локального Telegram Bot API Server (например, http://localhost:8081).
# Если задан — используется локальный сервер с лимитом 2000 МБ вместо 50 МБ.
TELEGRAM_API_SERVER_URL = os.getenv("TELEGRAM_API_SERVER_URL", "").strip()
# Прокси для yt-dlp (например, socks5://127.0.0.1:1080 или http://user:pass@proxy:8080).
# Необходим когда IP сервера заблокирован площадкой (Instagram, TikTok и др.),
# а куки привязаны к другому IP/геолокации.
YTDLP_PROXY = os.getenv("YTDLP_PROXY", "").strip()
