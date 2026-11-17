#!/usr/bin/env bash
# Установка SiftGuard на пустой сервер Ubuntu/Debian одной командой (от root):
#   curl -fsSL https://raw.githubusercontent.com/Eras3rX/Suzgec/main/deploy/server/setup.sh | bash
# Скрипт спросит ключ AI и (по желанию) домен, всё поставит, запустит как службу и напечатает адрес и токен панели.
# Повторный запуск безопасен: он обновит код и перезапустит сайт, не трогая ваш .env.
set -euo pipefail

REPO="https://github.com/Eras3rX/Suzgec.git"
BASE="/opt/siftguard"
APP="$BASE/app"
VENV="$BASE/venv"
USER_NAME="siftguard"

say() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
ask() { local prompt="$1" default="${2:-}" reply; read -r -p "$prompt" reply </dev/tty || true; echo "${reply:-$default}"; }
ask_secret() { local reply; read -r -s -p "$1" reply </dev/tty || true; echo >&2; echo "$reply"; }

[ "$(id -u)" -eq 0 ] || { echo "Запустите от root: sudo bash или зайдите на сервер под root."; exit 1; }
command -v apt-get >/dev/null || { echo "Нужен Ubuntu или Debian (есть apt-get)."; exit 1; }

say "1/6 Системные пакеты"
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y python3 python3-venv python3-pip git curl openssl libgl1 libglib2.0-0

# Распознаванию текста нужно около 1.5 ГБ памяти: если её мало и подкачки нет, добавляем файл подкачки на 2 ГБ
MEM_MB=$(awk '/MemTotal/ {print int($2/1024)}' /proc/meminfo)
SWAP_MB=$(awk '/SwapTotal/ {print int($2/1024)}' /proc/meminfo)
if [ "$MEM_MB" -lt 3000 ] && [ "$SWAP_MB" -lt 1000 ]; then
  say "Памяти мало (${MEM_MB} МБ): добавляю подкачку 2 ГБ"
  fallocate -l 2G /swapfile 2>/dev/null || dd if=/dev/zero of=/swapfile bs=1M count=2048
  chmod 600 /swapfile && mkswap /swapfile >/dev/null && swapon /swapfile
  grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

say "2/6 Код проекта"
id "$USER_NAME" >/dev/null 2>&1 || useradd --system --create-home --home-dir "$BASE" --shell /usr/sbin/nologin "$USER_NAME"
if [ -d "$APP/.git" ]; then
  git -C "$APP" fetch --depth 1 origin main && git -C "$APP" reset --hard origin/main
else
  rm -rf "$APP"; git clone --depth 1 "$REPO" "$APP"
fi

say "3/6 Python и зависимости (несколько минут)"
[ -d "$VENV" ] || python3 -m venv "$VENV"
"$VENV/bin/pip" install --upgrade pip >/dev/null
"$VENV/bin/pip" install -r "$APP/requirements.txt" -r "$APP/requirements-vision.txt"

say "4/6 Настройки (.env)"
if [ -f "$APP/.env" ]; then
  echo ".env уже есть: оставляю как есть."
else
  LLM_API_KEY=$(ask_secret "Ключ AI (LLM_API_KEY, например от Groq; можно пусто, тогда встроенный AI будет выключен): ")
  DOMAIN=$(ask "Домен для https (например demo.example.com), или Enter, если домена нет: " "")
  TOKEN=$(openssl rand -base64 18 | tr -d '+/=')
  {
    echo "ADMIN_TOKEN=$TOKEN"
    echo "PII_MODEL_ENABLED=0"
    echo "OCR_THREADS=2"
    if [ -n "$LLM_API_KEY" ]; then
      echo "LLM_API_KEY=$LLM_API_KEY"
      echo "LLM_BASE_URL=https://api.groq.com/openai/v1"
      echo "LLM_MODEL=openai/gpt-oss-120b"
      echo "LLM_VISION_MODEL=qwen/qwen3.8-27b"
    fi
    echo "SITE_DOMAIN=$DOMAIN"
  } > "$APP/.env"
fi
chmod 600 "$APP/.env"
chown -R "$USER_NAME":"$USER_NAME" "$BASE"
set -a; . "$APP/.env"; set +a
DOMAIN="${SITE_DOMAIN:-}"

say "5/6 Служба"
if [ -n "$DOMAIN" ]; then
  # За Caddy: сайт слушает только localhost, https выдаёт Caddy автоматически
  BIND="--host 127.0.0.1 --port 8000 --proxy-headers --forwarded-allow-ips=127.0.0.1"; PORT_CAP=""; CHECK_PORT=8000
  apt-get install -y debian-keyring debian-archive-keyring apt-transport-https gnupg >/dev/null
  if ! command -v caddy >/dev/null; then
    curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
    curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | tee /etc/apt/sources.list.d/caddy-stable.list >/dev/null
    apt-get update -y && apt-get install -y caddy
  fi
  printf '%s {\n    reverse_proxy 127.0.0.1:8000\n}\n' "$DOMAIN" > /etc/caddy/Caddyfile
  systemctl enable caddy >/dev/null 2>&1 && systemctl restart caddy
else
  # Без домена: сайт слушает порт 80 напрямую (заголовкам прокси не доверяем)
  BIND="--host 0.0.0.0 --port 80"; PORT_CAP="AmbientCapabilities=CAP_NET_BIND_SERVICE"; CHECK_PORT=80
fi

cat > /etc/systemd/system/siftguard.service <<EOF
[Unit]
Description=SiftGuard
After=network-online.target
Wants=network-online.target

[Service]
User=$USER_NAME
WorkingDirectory=$APP
ExecStart=$VENV/bin/uvicorn app.main:app $BIND
Restart=always
RestartSec=3
$PORT_CAP
NoNewPrivileges=true

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable siftguard >/dev/null 2>&1
systemctl restart siftguard

# Скрипт обновления: siftguard-update (подтянуть новый код с GitHub и перезапустить)
cat > /usr/local/bin/siftguard-update <<EOF
#!/usr/bin/env bash
set -e
git -C $APP fetch --depth 1 origin main && git -C $APP reset --hard origin/main
$VENV/bin/pip install -r $APP/requirements.txt -r $APP/requirements-vision.txt
chown -R $USER_NAME:$USER_NAME $BASE
systemctl restart siftguard
echo "Обновлено."
EOF
chmod +x /usr/local/bin/siftguard-update

command -v ufw >/dev/null && ufw status | grep -q active && { ufw allow 80/tcp >/dev/null; ufw allow 443/tcp >/dev/null; } || true

say "6/6 Проверка"
for i in $(seq 1 30); do
  if curl -fs -m 3 "http://127.0.0.1:${CHECK_PORT}/api/config" >/dev/null 2>&1; then OK=1; break; fi
  sleep 2
done
IP=$(curl -fs -m 5 https://api.ipify.org || hostname -I | awk '{print $1}')
echo
if [ "${OK:-0}" = "1" ]; then
  echo "ГОТОВО. Сайт запущен."
  [ -n "$DOMAIN" ] && echo "Адрес для жюри:    https://$DOMAIN" || echo "Адрес для жюри:    http://$IP"
  echo "Панель управления: добавьте /admin к адресу, пароль (ADMIN_TOKEN):"
  echo "  $ADMIN_TOKEN"
  echo "Обновить сайт позже: siftguard-update"
else
  echo "Сайт не ответил за минуту. Смотрите журнал:  journalctl -u siftguard -n 50 --no-pager"
  exit 1
fi
