import os

from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
DATA_DIR = os.getenv("DATA_DIR", "/workspace/NeuronDownloader/data")
POLL_INTERVAL_SECONDS = int(os.getenv("POLL_INTERVAL_SECONDS", "300"))
COOKIES_FILE = os.getenv("COOKIES_FILE", "")
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
