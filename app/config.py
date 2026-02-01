import os

from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
DATA_DIR = os.getenv("DATA_DIR", "/workspace/NeuronDownloader/data")
POLL_INTERVAL_SECONDS = int(os.getenv("POLL_INTERVAL_SECONDS", "300"))
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
FREE_DOWNLOAD_LIMIT = int(os.getenv("FREE_DOWNLOAD_LIMIT", "1"))
FREE_DOWNLOAD_WINDOW_SECONDS = int(os.getenv("FREE_DOWNLOAD_WINDOW_SECONDS", "86400"))
VK_USERNAME = os.getenv("VK_USERNAME", "")
VK_PASSWORD = os.getenv("VK_PASSWORD", "")
YOUTUBE_PLAYER_CLIENTS = [
    value.strip()
    for value in os.getenv("YOUTUBE_PLAYER_CLIENTS", "android,web").split(",")
    if value.strip()
]
USER_AGENT = os.getenv(
    "USER_AGENT",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36",
)
