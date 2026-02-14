"""Shared constants used across the bot."""

# --- Telegram limits ---
TELEGRAM_MAX_FILE_SIZE = 50 * 1024 * 1024  # 50 MB for bot uploads
TELEGRAM_SPLIT_TARGET_SIZE = 45 * 1024 * 1024  # 45 MB target for split parts
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
BOT_SIGNATURE = "\u0421\u043a\u0430\u0447\u0430\u043b \u0434\u043b\u044f \u0412\u0430\u0441 @NeuronDownloader_Bot"

# --- Callback data prefixes ---
CB_DOWNLOAD = "dl"

# Large video split
CB_SPLIT_YES = "split_y"
CB_SPLIT_NO = "split_n"

# Admin panel
CB_ADMIN = "adm"
CB_ADMIN_STATS = "adm_st"
CB_ADMIN_STATS_PLATFORM = "adm_stp"
CB_ADMIN_STATS_DAILY = "adm_std"
CB_ADMIN_STATS_USERS = "adm_stu"
CB_ADMIN_USERS = "adm_usr"
CB_ADMIN_USER_BLOCK = "adm_ub"
CB_ADMIN_USER_UNBLOCK = "adm_uu"
CB_ADMIN_USERS_PAGE = "adm_up"
CB_ADMIN_SETTINGS = "adm_set"
CB_ADMIN_TICKETS = "adm_tik"
CB_ADMIN_RESTART = "adm_rst"
CB_ADMIN_RESTART_CONFIRM = "adm_rstc"
CB_ADMIN_BACK = "adm_bk"
CB_ADMIN_SET_LIMIT = "adm_sl"
CB_ADMIN_SET_WINDOW = "adm_sw"
CB_ADMIN_CHANNELS = "adm_ch"
CB_ADMIN_CHANNEL_ADD = "adm_cha"
CB_ADMIN_CHANNEL_DEL = "adm_chd"

# Support tickets
CB_TICKET_VIEW = "tik_v"
CB_TICKET_REPLY = "tik_r"
CB_TICKET_CLOSE = "tik_c"
CB_TICKET_LIST = "tik_l"

# Channel videos
CB_CHANNEL_VIDEOS = "chvid"

# --- Emojis ---
EMOJI_VIDEO = "\U0001f3ac"       # 🎬
EMOJI_BEST = "\U0001f680"        # 🚀
EMOJI_AUDIO = "\U0001f3a7"       # 🎧
EMOJI_DOWNLOAD = "\u2b07\ufe0f"  # ⬇️
EMOJI_DONE = "\u2705"            # ✅
EMOJI_ERROR = "\u274c"           # ❌
EMOJI_HOURGLASS = "\u23f3"       # ⏳
EMOJI_ZAP = "\u26a1\ufe0f"      # ⚡️
EMOJI_BACK = "\u2b05\ufe0f"     # ⬅️
EMOJI_WARNING = "\u26a0\ufe0f"  # ⚠️
EMOJI_SETTINGS = "\u2699\ufe0f" # ⚙️
EMOJI_STATS = "\U0001f4ca"      # 📊
EMOJI_USERS = "\U0001f465"      # 👥
EMOJI_TICKETS = "\U0001f4ec"    # 📬
EMOJI_REPORT = "\U0001f4dd"     # 📝
EMOJI_RESTART = "\U0001f504"    # 🔄
EMOJI_CHANNEL = "\U0001f4e2"    # 📢
EMOJI_ALERT = "\U0001f6a8"      # 🚨

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
MENU_HELP = "\u2139\ufe0f \u041f\u043e\u043c\u043e\u0449\u044c"  # ℹ️ Помощь
MENU_REPORT = "\U0001f4dd \u0421\u043e\u043e\u0431\u0449\u0438\u0442\u044c \u043e \u043f\u0440\u043e\u0431\u043b\u0435\u043c\u0435"  # 📝 Сообщить о проблеме

# --- User/admin states ---
STATE_AWAITING_REPORT = "awaiting_report"
STATE_AWAITING_LIMIT = "awaiting_limit"
STATE_AWAITING_WINDOW = "awaiting_window"
STATE_AWAITING_CHANNEL_ID = "awaiting_channel_id"
STATE_REPLYING_TICKET = "replying_ticket"
