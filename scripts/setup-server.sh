#!/bin/bash
# Идемпотентный бутстрап сервера для NeuronDownloader.
# Запуск от root: bash scripts/setup-server.sh (из любого места, путь репо определяется сам).
# Что делает: системные пакеты (ffmpeg, python3-venv), Node.js 22 (NodeSource),
# venv + зависимости (requirements.txt + "yt-dlp[default]"), systemd-юнит
# из deploy/neuron_bot.service, три cron-записи, финальный чеклист.
# Повторный запуск безопасен: каждый шаг проверяет состояние перед изменением.

set -e
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

echo "== 1/6. Системные пакеты (ffmpeg, python3-venv) =="
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq ffmpeg python3-venv curl >/dev/null

echo "== 2/6. Node.js 22 (NodeSource; нужен для JS-челленджа YouTube) =="
NODE_MAJOR=$(node -p "process.versions.node.split('.')[0]" 2>/dev/null || echo 0)
if [ "$NODE_MAJOR" -lt 22 ]; then
    curl -fsSL https://deb.nodesource.com/setup_22.x | bash - >/dev/null
    apt-get install -y -qq nodejs >/dev/null
fi

echo "== 3/6. venv + зависимости =="
[ -d venv ] || python3 -m venv venv
venv/bin/pip install --quiet --upgrade pip
venv/bin/pip install --quiet -r requirements.txt
# Единый шаг зависимостей yt-dlp (унифицирован с scripts/yt-dlp-autoupdate.sh):
# [default] ставит yt-dlp-ejs ТОЧНОЙ версии, требуемой установленным yt-dlp,
# иначе yt-dlp молча игнорирует устаревший ejs и JS-челлендж YouTube не решается.
venv/bin/pip install --quiet -U "yt-dlp[default]"

echo "== 4/6. systemd-юнит =="
if [ ! -f /etc/systemd/system/neuron_bot.service ]; then
    cp deploy/neuron_bot.service /etc/systemd/system/neuron_bot.service
fi
systemctl daemon-reload
systemctl enable neuron_bot >/dev/null 2>&1
systemctl is-active --quiet neuron_bot || systemctl restart neuron_bot

echo "== 5/6. Cron (автообновление кода */5, рестарт бота 03:30, автoupdate yt-dlp 03:40) =="
CRON_KEEP=$(crontab -l 2>/dev/null | grep -v 'autoupdate.sh' | grep -v 'systemctl restart neuron_bot' || true)
( [ -n "$CRON_KEEP" ] && echo "$CRON_KEEP"
  echo "*/5 * * * * $REPO_ROOT/autoupdate.sh >> /var/log/bot_update.log 2>&1"
  echo "30 3 * * * systemctl restart neuron_bot"
  echo "40 3 * * * $REPO_ROOT/scripts/yt-dlp-autoupdate.sh >> $REPO_ROOT/logs/yt-dlp-autoupdate.log 2>&1" ) | crontab -

echo "== 6/6. Чеклист =="
echo "repo:       $REPO_ROOT ($(git rev-parse --short HEAD 2>/dev/null))"
echo "python:     $(venv/bin/python --version 2>&1)"
echo "yt-dlp:     $(venv/bin/yt-dlp --version 2>/dev/null)"
echo "yt-dlp-ejs: $(venv/bin/pip show yt-dlp-ejs 2>/dev/null | awk '/^Version/{print $2}')"
echo "node:       $(node --version 2>/dev/null) ($(command -v node))"
echo "ffmpeg:     $(ffmpeg -version 2>/dev/null | head -1 | cut -d' ' -f3)"
echo "unit:       $(systemctl is-active neuron_bot.service) ($(systemctl is-enabled neuron_bot.service 2>/dev/null))"
echo "crontab:"; crontab -l | sed 's/^/  /'
echo "Готово. Вручную из бэкапа: .env и data/ (bot.db, cookies.txt) — их в репо нет."
