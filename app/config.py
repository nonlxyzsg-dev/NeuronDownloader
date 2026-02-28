import os
import shutil

from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
DATA_DIR = os.getenv("DATA_DIR", "/workspace/NeuronDownloader/data")
DB_FILENAME = os.getenv("DB_FILENAME", "bot.db")
CLEANUP_INTERVAL_SECONDS = int(os.getenv("CLEANUP_INTERVAL_SECONDS", "600"))
CLEANUP_MAX_AGE_SECONDS = int(os.getenv("CLEANUP_MAX_AGE_SECONDS", "18000"))
_cookies_file_env = os.getenv("COOKIES_FILE", "").strip()
COOKIES_FILE = _cookies_file_env if _cookies_file_env else os.path.join(DATA_DIR, "cookies.txt")
COOKIE_CHECK_INTERVAL_SECONDS = int(os.getenv("COOKIE_CHECK_INTERVAL_SECONDS", "1800"))
YOUTUBE_TEST_URL = os.getenv(
    "YOUTUBE_TEST_URL", "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
)
INSTAGRAM_TEST_URL = os.getenv(
    "INSTAGRAM_TEST_URL", "https://www.instagram.com/instagram/"
)
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
    for value in os.getenv("YOUTUBE_PLAYER_CLIENTS", "android_vr,web,web_safari").split(",")
    if value.strip() and value.strip() in SUPPORTED_YOUTUBE_PLAYER_CLIENTS
]
YOUTUBE_JS_RUNTIME = os.getenv("YOUTUBE_JS_RUNTIME", "").strip()
YOUTUBE_JS_RUNTIME_PATH = os.getenv("YOUTUBE_JS_RUNTIME_PATH", "").strip()

# Автодетект JS-рантайма для YouTube n-challenge, если не задан явно.
# Без рантайма yt-dlp не может решить n-параметр и YouTube отдаёт только картинки.
if not YOUTUBE_JS_RUNTIME or not YOUTUBE_JS_RUNTIME_PATH:
    _JS_RUNTIMES = [
        ("node", "node"),
        ("deno", "deno"),
        ("bun", "bun"),
    ]
    for _rt_name, _rt_bin in _JS_RUNTIMES:
        _found = shutil.which(_rt_bin)
        if _found:
            if not YOUTUBE_JS_RUNTIME:
                YOUTUBE_JS_RUNTIME = _rt_name
            if not YOUTUBE_JS_RUNTIME_PATH:
                YOUTUBE_JS_RUNTIME_PATH = _found
            break
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
