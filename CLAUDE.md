# CLAUDE.md — паспорт проекта NeuronDownloader

Telegram-бот @NeuronDownloader_Bot: скачивает видео с YouTube/Instagram/VK/Rutube/TikTok и др. по ссылке с выбором качества (yt-dlp), доставляет файлом в Telegram с учётом лимитов (50 МБ стандарт / 2000 МБ локальный Bot API Server), перекодировкой под iPhone и кэшем file_id.

## Стек
- Python 3.10 (прод — 3.10.12 venv), pyTelegramBotAPI 4.14 (polling), yt-dlp (обновляется cron-ом), FFmpeg/ffprobe (системные), SQLite (data/bot.db), python-dotenv.
- Структура: `app/main.py` (точка входа, BotContext, сигналы, polling-цикл), `app/handlers/` (download — основной поток, admin, history, support), `app/downloader.py` (VideoDownloader: yt-dlp обёртка, ffmpeg-фиксы поворота/SAR/кодека), `app/storage.py` (SQLite, миграции в `_migrate_db`/`_init_db` — CREATE IF NOT EXISTS + ALTER), `app/download_queue.py` (DownloadManager: пул воркеров MAX_CONCURRENT_DOWNLOADS, 1 активная задача на юзера), `app/cookie_monitor.py`, `app/cleanup.py`, `app/config.py` (все env), `app/constants.py` (CB-префиксы, лимиты, эмодзи), `app/utils.py`, `app/keyboards.py`.

## Команды
- Запуск: `python -m app.main` (нужен BOT_TOKEN из .env).
- Зависимости: `pip install -r requirements.txt`.
- Self-smoke без запуска бота: `python -m compileall -q app`; `python -c "import app.main"`; `python tests/test_retry_logic.py` (storage+классификатор ретрая, standalone, tmp DATA_DIR).
- Код — Python 3.10-совместимый (без синтаксиса 3.11+).

## Данные и среда
- БД SQLite `data/bot.db` (DATA_DIR/DB_FILENAME из env): users, downloads (успехи и провалы для статистики), free_downloads, pending_cookie_downloads, failed_downloads (упавшие + статусная машина ретрая: failed → retrying → done | dismissed), file_cache, support_*, video_incidents, bot_settings, required_channels.
- `.env` и `cookies.txt` — СЕКРЕТНЫЕ, не коммитить, не публиковать, не читать без нужды.
- Ключевые env: BOT_TOKEN, ADMIN_IDS, MAX_CONCURRENT_DOWNLOADS=2, MAX_QUEUE_SIZE=20, MAX_ACTIVE_TASKS_PER_USER=1, FREE_DOWNLOAD_LIMIT/WINDOW, DOWNLOAD_TIMEOUT_SECONDS (пол таймаута скачивания, дефолт 600; адаптив: total/256000 Б/с, потолок 7200с), TELEGRAM_API_SERVER_URL (локальный Bot API → лимит 2000 МБ).

## Деплой
- Прод: jtesla1:/opt/NeuronDownloader (python 3.10.12 venv, systemd-юнит). Автодеплой: следит за origin/main, при новой ревизии подтягивает и рестартует юнит (≤5 мин). С jtesla1 push невозможен (read-only deploy key).
- Рабочая копия разработки: ai-linux /home/ai/NeuronDownloader (владелец user ai). Push — SSH-ключом nonlxyzsg-dev.
- Push в main = выкатка на прод в течение ~5 минут. Сломанное/непроверенное в main не пушить.

## Ограничения и гейты
- Живой смоук прода и рестарт боевого сервиса — только осознанно и по подтверждению владельца.
- Не трогать: .env, cookies.txt, scripts/ (yt-dlp-autoupdate cron), Dockerfile/docker-compose (инфра).
- Стиль: русские строки логов и сообщений, точечные правки (файлы целиком не переписывать), параметризованный SQL, callback_data ≤64 байт.
- Ретраи упавших: авторетрай разовый (60с, транзиентные классы timeout/403/429/format), лимит free-загрузок юзера НЕ расходует (наша вина — не его запрос). Админ-секция «🔁 Перекачка» в /admin.

## Грабли
- yt-dlp оборачивает исключения из progress_hook (TimeoutError наверх может прийти как DownloadError) — классификация по подстроке «превысила таймаут», не по типу.
- Экземпляры yt-dlp не потокобезопасны — на каждую операцию свой YoutubeDL (паттерн VideoDownloader).
- Instagram-карусели = плейлисты (noplaylist=False), карусельный поток отдельный от _do_download.
- Лимит free-загрузок списывается в handler'ах ДО очереди — любой обход handler'ов (ретрай, отложенные cookie-загрузки) лимит не тратит; это осознанное поведение.
