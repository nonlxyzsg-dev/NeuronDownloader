#!/bin/bash
# Ежедневное автообновление yt-dlp со смоук-тестом и откатом.
# Cron: 03:40 (после штатного рестарта бота в 03:30).
# Лог: logs/yt-dlp-autoupdate.log, ротация — последние 500 строк.

cd /opt/NeuronDownloader || exit 1

LOG="logs/yt-dlp-autoupdate.log"
SMOKE_URL="https://www.youtube.com/watch?v=dQw4w9WgXcQ"
SMOKE_TIMEOUT=180
YTDLP="venv/bin/yt-dlp"
PIP="venv/bin/pip"

mkdir -p logs
# Ротация: оставляем последние 500 строк
tail -n 500 "$LOG" > "$LOG.tmp" 2>/dev/null && mv "$LOG.tmp" "$LOG"

OLD_VER=$($YTDLP --version 2>/dev/null)

# Обновляемся до свежего стабильного релиза с PyPI
$PIP install -U yt-dlp >> "$LOG" 2>&1

NEW_VER=$($YTDLP --version 2>/dev/null)

if [ "$OLD_VER" = "$NEW_VER" ]; then
    echo "$(date): yt-dlp $NEW_VER — обновлений нет." >> "$LOG"
    exit 0
fi

echo "$(date): yt-dlp $OLD_VER -> $NEW_VER, гоняем смоук..." >> "$LOG"

# Смоук: импорты библиотек, версия, метадата-тест стабильного YouTube-видео
SMOKE_OK=1
venv/bin/python -c "import yt_dlp, curl_cffi" >> "$LOG" 2>&1 || SMOKE_OK=0
$YTDLP --version >> "$LOG" 2>&1 || SMOKE_OK=0
timeout "$SMOKE_TIMEOUT" $YTDLP --skip-download --no-playlist "$SMOKE_URL" >> "$LOG" 2>&1 || SMOKE_OK=0

if [ "$SMOKE_OK" -eq 1 ]; then
    systemctl restart neuron_bot
    echo "$(date): OK — смоук прошёл, бот перезапущен на yt-dlp $NEW_VER." >> "$LOG"
else
    echo "$(date): FAIL — смоук не прошёл, откатываемся на yt-dlp $OLD_VER..." >> "$LOG"
    $PIP install "yt-dlp==$OLD_VER" >> "$LOG" 2>&1
    systemctl restart neuron_bot
    echo "$(date): Откат на $($YTDLP --version 2>/dev/null) выполнен, бот перезапущен." >> "$LOG"
fi
