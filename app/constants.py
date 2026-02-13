"""Shared constants used across the bot."""

# --- Telegram limits ---
TELEGRAM_MAX_FILE_SIZE = 50 * 1024 * 1024  # 50 MB for bot uploads
TELEGRAM_CAPTION_MAX_LENGTH = 1024
TELEGRAM_CALLBACK_DATA_MAX_BYTES = 64
TELEGRAM_MAX_BUTTONS_PER_KEYBOARD = 100

# --- Upload retry settings ---
UPLOAD_MAX_RETRIES = 3
UPLOAD_RETRY_DELAYS = (2, 5, 10)  # seconds between retries

# --- Download timeout (seconds) ---
DOWNLOAD_TIMEOUT_SECONDS = 600  # 10 minutes

# --- Membership cache TTL (seconds) ---
MEMBERSHIP_CACHE_TTL = 300  # 5 minutes

# --- Preferred output format for video (avoids webm issues in Telegram) ---
PREFERRED_VIDEO_FORMAT = "mp4"

# --- Bot signature for captions ---
BOT_SIGNATURE = "\U0001f4be Нейрон-Downloader @NeuronDownloader_Bot"

# --- Callback data prefixes ---
CB_DOWNLOAD = "dl"
CB_SUBSCRIBE = "sub"
CB_SUBMENU = "submenu"
CB_BACK = "back"
CB_UNSUB = "unsub"
CB_SUBDEL = "subdel"
CB_SUBDEL_ALL = "subdel_all"

# --- Emojis ---
EMOJI_VIDEO = "\U0001f3ac"       # 🎬
EMOJI_BEST = "\U0001f680"        # 🚀
EMOJI_AUDIO = "\U0001f3a7"       # 🎧
EMOJI_STAR = "\u2b50"            # ⭐
EMOJI_BACK = "\u2b05\ufe0f"      # ⬅️
EMOJI_UNSUB = "\U0001f9f9"       # 🧹
EMOJI_DELETE = "\U0001f5d1\ufe0f" # 🗑️
EMOJI_DOWNLOAD = "\u2b07\ufe0f"  # ⬇️
EMOJI_DONE = "\u2705"            # ✅
EMOJI_ERROR = "\u274c"           # ❌
EMOJI_HOURGLASS = "\u23f3"       # ⏳
EMOJI_ZAP = "\u26a1\ufe0f"      # ⚡️

# --- Chat action types ---
ACTION_UPLOAD_VIDEO = "upload_video"
ACTION_UPLOAD_AUDIO = "upload_audio"
ACTION_TYPING = "typing"

# --- Download statuses ---
STATUS_SUCCESS = "success"
STATUS_FAILED = "failed"

# --- Format identifiers ---
FORMAT_BEST = "best"
FORMAT_AUDIO = "audio"

# --- Menu button labels ---
MENU_DOWNLOAD = "\U0001f4e5 Скачать"           # 📥 Скачать
MENU_SUBSCRIPTIONS = "\U0001f4cc Мои подписки"  # 📌 Мои подписки
MENU_HELP = "\u2139\ufe0f Помощь"              # ℹ️ Помощь
