"""Общие константы, используемые во всём боте."""

# --- Лимиты Telegram ---
TELEGRAM_MAX_FILE_SIZE = 50 * 1024 * 1024  # 50 МБ — лимит загрузки через бот
TELEGRAM_SPLIT_TARGET_SIZE = 45 * 1024 * 1024  # 45 МБ — целевой размер частей при разделении

# --- Лимиты локального Telegram Bot API Server ---
TELEGRAM_LOCAL_API_MAX_FILE_SIZE = 2000 * 1024 * 1024  # 2000 МБ — лимит через локальный Bot API
TELEGRAM_LOCAL_API_SPLIT_TARGET_SIZE = 1900 * 1024 * 1024  # 1900 МБ — целевой размер частей
TELEGRAM_CAPTION_MAX_LENGTH = 1024
TELEGRAM_CALLBACK_DATA_MAX_BYTES = 64
TELEGRAM_MAX_BUTTONS_PER_KEYBOARD = 100

# --- Настройки повторных попыток загрузки ---
UPLOAD_MAX_RETRIES = 3
UPLOAD_RETRY_DELAYS = (2, 5, 10)  # секунды между попытками

# --- Таймаут скачивания (секунды) ---
DOWNLOAD_TIMEOUT_SECONDS = 600  # 10 минут
DOWNLOAD_MAX_TIMEOUT_SECONDS = 7200
DOWNLOAD_TIMEOUT_MIN_SPEED_BPS = 256_000
DOWNLOAD_RETRY_DELAY_SECONDS = 60

# --- Упавшие загрузки ---
FAILED_DOWNLOADS_WINDOW_DAYS = 7
FAILED_DOWNLOADS_PER_PAGE = 8

# --- TTL кэша подписок (секунды) ---
MEMBERSHIP_CACHE_TTL = 300  # 5 минут

# --- Предпочтительный формат видео (избегаем проблем с webm в Telegram) ---
PREFERRED_VIDEO_FORMAT = "mp4"

# --- Экстракторы, для которых прямые ссылки не работают ---
# YouTube, Instagram, TikTok и другие платформы защищают CDN-ссылки
# (привязка к IP, подписи, cookies, короткий TTL), поэтому Telegram Bot API
# не может скачать файл по прямому URL — всегда получим "failed to get HTTP URL content".
DIRECT_URL_SKIP_EXTRACTORS = frozenset({
    "Youtube",
    "Instagram",
    "TikTok",
    "Twitter",
    "Facebook",
    "Vk",
    "VKPlay",
    "VKPlayLive",
    "Twitch",
    "TwitchStream",
    "TwitchVod",
    "BiliBili",
    "Rutube",
    "OK",
    "Pikabu",
    "Dzen",
    "YandexDisk",
})

# --- Подпись бота для подписей к медиа ---
BOT_SIGNATURE = (
    "Скачал для Вас @NeuronDownloader_Bot\n"
    '<a href="https://t.me/Windows_VPN_bot?start=JaxTesla">Помощь с доступом к нейросетям</a>'
)

# --- Префиксы callback-данных ---
CB_DOWNLOAD = "dl"

# Разделение больших видео
CB_SPLIT_YES = "split_y"
CB_SPLIT_NO = "split_n"

# Админ-панель
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
CB_ADMIN_LOGS = "adm_log"
CB_ADMIN_RETRY = "adm_rtry"
CB_ADMIN_RETRY_PAGE = "adm_rtrp"
CB_ADMIN_RETRY_ONE = "adm_rt1"
CB_ADMIN_RETRY_ALL = "adm_rta"

# Обращения (тикеты поддержки)
CB_TICKET_VIEW = "tik_v"
CB_TICKET_REPLY = "tik_r"
CB_TICKET_CLOSE = "tik_c"
CB_TICKET_LIST = "tik_l"

# Видео канала
CB_CHANNEL_VIDEOS = "chvid"

# Инциденты воспроизведения видео
CB_VIDEO_REPORT = "vrpt"
CB_ADMIN_INCIDENTS = "adm_inc"
CB_INCIDENT_VIEW = "inc_v"
CB_INCIDENT_STATUS = "inc_ss"
CB_INCIDENT_LIST = "inc_l"

# Массовая рассылка
CB_ADMIN_BROADCAST = "adm_bc"
CB_BROADCAST_ALL = "bc_all"
CB_BROADCAST_AFFECTED = "bc_aff"

# Выбор устройства
CB_DEVICE_ANDROID = "dev_a"
CB_DEVICE_IPHONE = "dev_i"

# Перекодирование по запросу
CB_REENCODE = "reenc"

# История загрузок — пользователь
CB_MY_HISTORY = "mh"           # главное меню истории
CB_MY_HIST_ALL = "mha"         # все загрузки (пагинация): mha|page
CB_MY_HIST_PLATFORMS = "mhp"   # список площадок
CB_MY_HIST_PLAT_VIEW = "mhpv"  # загрузки по площадке: mhpv|platform|page
CB_MY_HIST_SEND = "mhs"        # отправить видео: mhs|download_id
CB_MY_HIST_DATES = "mhd"       # тогл разбивки по датам

# История загрузок — админ
CB_ADMIN_HISTORY = "ahst"        # главное меню истории в админке
CB_ADMIN_HIST_ALL = "aha"        # все загрузки: aha|page
CB_ADMIN_HIST_PLATFORMS = "ahp"  # список площадок
CB_ADMIN_HIST_PLAT_VIEW = "ahpv" # по площадке: ahpv|platform|page
CB_ADMIN_HIST_USERS = "ahu"      # список пользователей: ahu|page
CB_ADMIN_HIST_USER_VIEW = "ahuv" # загрузки пользователя: ahuv|user_id|page
CB_ADMIN_HIST_SEND = "ahs"       # отправить видео админу: ahs|download_id

# Тогл перекодирования в клавиатуре выбора качества
CB_TOGGLE_REENCODE = "tgre"

# Смена устройства из клавиатуры выбора качества
CB_DEVICE_INLINE = "devi"

# Мгновенная отправка из кэша
CB_CACHED_SEND = "csnd"

# Типы устройств
DEVICE_ANDROID = "android"
DEVICE_IPHONE = "iphone"

# Статусы инцидентов
INCIDENT_REPORTED = "reported"
INCIDENT_IN_PROGRESS = "in_progress"
INCIDENT_FIXED = "fixed"
INCIDENT_WONT_FIX = "wont_fix"

# --- Эмодзи ---
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
EMOJI_LOGS = "\U0001f4cb"       # 📋
EMOJI_INCIDENT = "\U0001f6a7"   # 🚧
EMOJI_HISTORY = "\U0001f4c2"    # 📂
EMOJI_LINK = "\U0001f517"       # 🔗
EMOJI_RETRY = "\U0001f501"      # 🔁

# --- Типы действий в чате ---
ACTION_UPLOAD_VIDEO = "upload_video"
ACTION_UPLOAD_AUDIO = "upload_audio"
ACTION_TYPING = "typing"

# --- Статусы загрузки ---
STATUS_SUCCESS = "success"
STATUS_FAILED = "failed"
STATUS_RETRYING = "retrying"
STATUS_DONE = "done"
STATUS_DISMISSED = "dismissed"

# --- Идентификаторы форматов ---
FORMAT_BEST = "best"
FORMAT_AUDIO = "audio"

# --- Ссылка на канал ---
CHANNEL_LINK = "https://t.me/+PG6Vj_CWU7xmYTM6"

# --- Надписи на кнопках меню ---
MENU_REPORT = "\U0001f4dd \u0421\u043e\u043e\u0431\u0449\u0438\u0442\u044c \u043e \u043f\u0440\u043e\u0431\u043b\u0435\u043c\u0435"  # 📝 Сообщить о проблеме
MENU_CHANNEL = "\U0001f4e2 \u0411\u0430\u043d\u043a\u0430 \u0441 \u043d\u0435\u0439\u0440\u043e\u043d\u0430\u043c\u0438"  # 📢 Банка с нейронами
MENU_ADMIN = "\u2699\ufe0f \u0410\u0434\u043c\u0438\u043d-\u043f\u0430\u043d\u0435\u043b\u044c"  # ⚙️ Админ-панель
MENU_HISTORY = "\U0001f4c2 \u041c\u043e\u0438 \u0437\u0430\u0433\u0440\u0443\u0437\u043a\u0438"  # 📂 Мои загрузки

# --- Состояния пользователя/админа ---
STATE_AWAITING_REPORT = "awaiting_report"
STATE_AWAITING_LIMIT = "awaiting_limit"
STATE_AWAITING_WINDOW = "awaiting_window"
STATE_AWAITING_CHANNEL_ID = "awaiting_channel_id"
STATE_REPLYING_TICKET = "replying_ticket"
STATE_AWAITING_LOG_LINES = "awaiting_log_lines"
STATE_AWAITING_BROADCAST_ALL = "awaiting_broadcast_all"
STATE_AWAITING_BROADCAST_AFFECTED = "awaiting_broadcast_affected"
