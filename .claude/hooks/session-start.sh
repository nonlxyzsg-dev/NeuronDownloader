#!/bin/bash
set -euo pipefail

# Запускаем только в удалённом окружении (Claude Code on the web)
if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

cd "$CLAUDE_PROJECT_DIR"

# Обновляем setuptools и wheel (нужно для сборки pyTelegramBotAPI)
pip install --break-system-packages --ignore-installed setuptools wheel 2>/dev/null || true

# Устанавливаем Python-зависимости
pip install --break-system-packages -r requirements.txt

# Устанавливаем ffmpeg/ffprobe для разделения видео
if ! command -v ffmpeg &> /dev/null; then
  apt-get update -qq && apt-get install -y -qq ffmpeg
fi
