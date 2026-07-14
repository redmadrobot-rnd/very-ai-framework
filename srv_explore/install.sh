#!/usr/bin/env bash
# Установка srv-explore на хост (systemd). Идемпотентно. Запускать от root.
#
# Модель: сервис — root (провижинит apt/docker, спавнит агента). Опасный код (bash
# агента) заперт в ПЕСОЧНИЦЕ под unprivileged-юзером srvx-agent (systemd-run +
# ProtectSystem=strict). Секрет CLAUDE_CODE_OAUTH_TOKEN дописывает деплой в env.
#
# Использование: sudo bash srv_explore/install.sh
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"   # каталог бандла (srv_explore/)
APP_DIR=/opt/srv-explore
CFG_DIR=/etc/srv-explore
STATE_DIR=/var/lib/srv-explore
AGENT_USER=srvx-agent     # песочный юзер агента (без прав)
TUNNEL_USER=srvx-tunnel   # транспорт (только проброс порта)

echo "==> srv-explore install (src=$SRC)"

# 1. песочный юзер агента: nologin, БЕЗ группы docker (сокет закрыт → escape невозможен),
# в группах чтения journal/логов (их применит systemd-run --uid).
if ! id "$AGENT_USER" >/dev/null 2>&1; then
  useradd --system --no-create-home --shell /usr/sbin/nologin "$AGENT_USER"
fi
for grp in systemd-journal adm; do
  getent group "$grp" >/dev/null 2>&1 && usermod -aG "$grp" "$AGENT_USER" || true
done

# 2. каталоги (сервис — root; агент только читает код/venv → a+rX)
install -d "$APP_DIR"
install -d -m 0750 "$CFG_DIR"
install -d -m 0750 "$STATE_DIR"

# 3. код бандла
rm -rf "$APP_DIR/srv_explore"
cp -r "$SRC" "$APP_DIR/srv_explore"
find "$APP_DIR/srv_explore" -type d -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null || true

# 4. venv + зависимости
if ! python3 -c "import ensurepip" >/dev/null 2>&1; then
  if command -v apt-get >/dev/null 2>&1; then
    echo "==> python3-venv отсутствует — доустанавливаю"
    DEBIAN_FRONTEND=noninteractive apt-get install -y python3-venv >/dev/null
  else
    echo "python3 venv/ensurepip недоступен и нет apt-get — установи вручную" >&2
    exit 1
  fi
fi
[ -x "$APP_DIR/venv/bin/python" ] || python3 -m venv "$APP_DIR/venv"
"$APP_DIR/venv/bin/pip" install --quiet --upgrade pip
"$APP_DIR/venv/bin/pip" install --quiet -r "$APP_DIR/srv_explore/requirements.txt"
# агент (srvx-agent) должен уметь читать/исполнять код и venv из песочницы
chmod -R a+rX "$APP_DIR"

# 5. env-файл (несекретные дефолты; CLAUDE_CODE_OAUTH_TOKEN дописывает деплой)
if [ ! -f "$CFG_DIR/env" ]; then
  cat > "$CFG_DIR/env" <<EOF
SRV_EXPLORE_HOST=127.0.0.1
SRV_EXPLORE_PORT=8765
SRV_EXPLORE_CWD=/
SRV_EXPLORE_PROMPT=$APP_DIR/srv_explore/agent_prompt.md
SRV_EXPLORE_TOKENS=$STATE_DIR/tokens.json
SRV_EXPLORE_PROFILE_STATE=$STATE_DIR/profiles.json
SRV_EXPLORE_PROXY=http://127.0.0.1:3128
# Доверенные адреса, куда агенту МОЖНО наружу (всё прочее egress обрублен):
#   *_DOMAINS — через прокси (список для tinyproxy), *_CIDRS — напрямую (firewall).
SRV_EXPLORE_TRUSTED_DOMAINS=
SRV_EXPLORE_TRUSTED_CIDRS=
# CLAUDE_CODE_OAUTH_TOKEN (авторизация модели) — дописывает деплой, не коммитить.
# SRV_EXPLORE_ADMIN_TOKEN (гейт /admin) — генерится ниже при первой установке.
EOF
  chmod 0640 "$CFG_DIR/env"
fi

ensure_env_kv() { grep -q "^$1=" "$CFG_DIR/env" || printf '%s=%s\n' "$1" "$2" >> "$CFG_DIR/env"; }
ensure_env_kv SRV_EXPLORE_PROFILE_STATE "$STATE_DIR/profiles.json"
ensure_env_kv SRV_EXPLORE_PUBLIC_HOST "$(hostname -I 2>/dev/null | awk '{print $1}')"
ensure_env_kv SRV_EXPLORE_PROXY "http://127.0.0.1:3128"
ensure_env_kv SRV_EXPLORE_TRUSTED_DOMAINS ""
ensure_env_kv SRV_EXPLORE_TRUSTED_CIDRS ""

# 5c. админ-токен /admin — генерим ОДИН раз
if ! grep -q "^SRV_EXPLORE_ADMIN_TOKEN=" "$CFG_DIR/env"; then
  ADMIN_TOKEN="adm_$("$APP_DIR/venv/bin/python" -c 'import secrets;print(secrets.token_urlsafe(32))')"
  printf 'SRV_EXPLORE_ADMIN_TOKEN=%s\n' "$ADMIN_TOKEN" >> "$CFG_DIR/env"
  echo "==> админ-токен /admin (сохрани, показывается один раз): $ADMIN_TOKEN"
fi

[ -f "$STATE_DIR/tokens.json" ] || echo '[]' > "$STATE_DIR/tokens.json"
chmod 0640 "$STATE_DIR/tokens.json"

# 6. туннельный SSH-юзер: транспорт до MCP без HTTPS и без shell.
if ! id "$TUNNEL_USER" >/dev/null 2>&1; then
  useradd --system --no-create-home --shell /usr/sbin/nologin "$TUNNEL_USER"
fi
[ -f "$STATE_DIR/tunnel_keys" ] || install -m 0640 /dev/null "$STATE_DIR/tunnel_keys"
cat > /etc/ssh/sshd_config.d/srv-explore-tunnel.conf <<EOF
Match User $TUNNEL_USER
  AuthorizedKeysFile none
  AuthorizedKeysCommand /bin/cat $STATE_DIR/tunnel_keys
  AuthorizedKeysCommandUser root
  AllowTcpForwarding yes
  PermitOpen 127.0.0.1:8765 localhost:8765
  X11Forwarding no
  AllowAgentForwarding no
  PermitTTY no
  ForceCommand /usr/sbin/nologin
EOF
if sshd -t 2>/dev/null; then
  systemctl reload ssh 2>/dev/null || systemctl reload sshd 2>/dev/null || true
else
  echo "!! sshd -t не прошёл — проверь drop-in вручную" >&2
fi

# 6b. egress форвард-прокси (tinyproxy): песочница агента рубит внешку (firewall), наружу
# (API модели + доверенные домены) агент ходит ТОЛЬКО через него. FilterDefaultDeny — всё,
# что не в allowlist, режется. Дефолтный сервис пакета глушим, крутим свой на 127.0.0.1:3128.
if ! command -v tinyproxy >/dev/null 2>&1; then
  if command -v apt-get >/dev/null 2>&1; then
    echo "==> ставлю tinyproxy (форвард-прокси egress)"
    DEBIAN_FRONTEND=noninteractive apt-get install -y tinyproxy >/dev/null
  else
    echo "!! tinyproxy не найден и нет apt-get — egress-прокси не поднять" >&2
  fi
fi
systemctl disable --now tinyproxy 2>/dev/null || true   # стоковый сервис на 8888 — не нужен

# allowlist доменов: api.anthropic.com (модель) + SRV_EXPLORE_TRUSTED_DOMAINS из env.
# Каждый домен — extended-regex по хосту: сам домен и его поддомены.
TRUSTED_DOMAINS="$(grep -E '^SRV_EXPLORE_TRUSTED_DOMAINS=' "$CFG_DIR/env" | cut -d= -f2- | tr ',' ' ' || true)"
{
  for d in api.anthropic.com $TRUSTED_DOMAINS; do
    [ -n "$d" ] || continue
    printf '(^|\\.)%s$\n' "$(printf '%s' "$d" | sed 's/\./\\./g')"
  done
} > "$CFG_DIR/proxy-allow"
chmod 0644 "$CFG_DIR/proxy-allow"

cat > "$CFG_DIR/tinyproxy.conf" <<EOF
Port 3128
Listen 127.0.0.1
Timeout 600
Allow 127.0.0.1
ConnectPort 443
FilterDefaultDeny Yes
FilterExtended On
FilterCaseSensitive Off
FilterURLs Off
Filter "$CFG_DIR/proxy-allow"
PidFile "/run/srv-explore-proxy/tinyproxy.pid"
LogLevel Warning
EOF
chmod 0644 "$CFG_DIR/tinyproxy.conf"

if command -v tinyproxy >/dev/null 2>&1; then
  install -m 0644 "$APP_DIR/srv_explore/systemd/srv-explore-proxy.service" \
    /etc/systemd/system/srv-explore-proxy.service
  systemctl daemon-reload
  systemctl enable srv-explore-proxy.service
  systemctl restart srv-explore-proxy.service
fi

# 7. systemd-юнит
install -m 0644 "$APP_DIR/srv_explore/systemd/srv-explore.service" /etc/systemd/system/srv-explore.service
systemctl daemon-reload
systemctl enable srv-explore.service
systemctl restart srv-explore.service

echo "==> готово. Статус: systemctl status srv-explore --no-pager"
echo "    Токены/профили: http://<host>:<port>/admin (админ-токен выше)."
echo "    CLI выдачи токена: $APP_DIR/venv/bin/python -m srv_explore.token_store \\"
echo "      --store $STATE_DIR/tokens.json issue --label <кто>"
