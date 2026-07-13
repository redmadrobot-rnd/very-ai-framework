#!/usr/bin/env bash
# Установка изолированного server-side srv-explore на хост (systemd, ro OS-user).
# Бандл самодостаточен: всё (mcp_server, guard, profiles, prompt, token_store) — в
# каталоге этого скрипта. Идемпотентно. Запускать от root на целевом сервере.
# Секреты (CLAUDE_CODE_OAUTH_TOKEN) сюда НЕ передаём — их пишет деплой в
# /etc/srv-explore/env (см. README).
#
# Использование:
#   sudo bash srv_explore/install.sh
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"   # каталог бандла (srv_explore/)
APP_DIR=/opt/srv-explore
CFG_DIR=/etc/srv-explore
STATE_DIR=/var/lib/srv-explore   # writable: tokens.json + runs.json
USER_NAME=srv-explore

echo "==> srv-explore install (src=$SRC)"

# 1. unprivileged OS-пользователь (без логина), в группах для чтения journal/логов.
# БЕЗ группы docker — доступ к сокету = escape; docker-чтение через socket-proxy (коммит 2).
if ! id "$USER_NAME" >/dev/null 2>&1; then
  useradd --system --no-create-home --shell /usr/sbin/nologin "$USER_NAME"
fi
for grp in systemd-journal adm; do
  getent group "$grp" >/dev/null 2>&1 && usermod -aG "$grp" "$USER_NAME" || true
done

# 2. каталоги
install -d -o "$USER_NAME" -g "$USER_NAME" "$APP_DIR"
install -d -m 0750 -o "$USER_NAME" -g "$USER_NAME" "$CFG_DIR"
# state: сервис-юзер пишет сюда (токены/история); StateDirectory в юните тоже создаёт
install -d -m 0750 -o "$USER_NAME" -g "$USER_NAME" "$STATE_DIR"

# 3. код бандла целиком (guard/profiles/prompt/сервер — всё внутри srv_explore/)
rm -rf "$APP_DIR/srv_explore"
cp -r "$SRC" "$APP_DIR/srv_explore"
find "$APP_DIR/srv_explore" -type d -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null || true
chown -R "$USER_NAME:$USER_NAME" "$APP_DIR/srv_explore"

# 4. venv + зависимости (на голой Ubuntu ensurepip нет — ставим python3-venv)
if ! python3 -c "import ensurepip" >/dev/null 2>&1; then
  if command -v apt-get >/dev/null 2>&1; then
    echo "==> python3-venv отсутствует — доустанавливаю"
    DEBIAN_FRONTEND=noninteractive apt-get install -y python3-venv >/dev/null
  else
    echo "python3 venv/ensurepip недоступен и нет apt-get — установи python3-venv вручную" >&2
    exit 1
  fi
fi
if [ ! -x "$APP_DIR/venv/bin/python" ]; then
  python3 -m venv "$APP_DIR/venv"
fi
"$APP_DIR/venv/bin/pip" install --quiet --upgrade pip
"$APP_DIR/venv/bin/pip" install --quiet -r "$APP_DIR/srv_explore/requirements.txt"
chown -R "$USER_NAME:$USER_NAME" "$APP_DIR/venv"

# 5. env-файл (несекретные дефолты; CLAUDE_CODE_OAUTH_TOKEN дописывает деплой)
if [ ! -f "$CFG_DIR/env" ]; then
  cat > "$CFG_DIR/env" <<EOF
SRV_EXPLORE_HOST=127.0.0.1
SRV_EXPLORE_PORT=8765
SRV_EXPLORE_CWD=/
SRV_EXPLORE_PROMPT=$APP_DIR/srv_explore/agent_prompt.md
SRV_EXPLORE_TOKENS=$STATE_DIR/tokens.json
SRV_EXPLORE_RUNS=$STATE_DIR/runs.json
SRV_EXPLORE_PROFILE_STATE=$STATE_DIR/profiles.json
SRV_EXPLORE_HISTORY_PER_USER=15
# CLAUDE_CODE_OAUTH_TOKEN (авторизация модели для агента) — дописывает деплой, не коммитить.
# SRV_EXPLORE_ADMIN_TOKEN (гейт /admin) — генерится ниже при первой установке.
EOF
  chmod 0640 "$CFG_DIR/env"
  chown root:"$USER_NAME" "$CFG_DIR/env"
fi

# 5b. ключи, которых может не быть в уже существующем env (идемпотентно)
ensure_env_kv() { grep -q "^$1=" "$CFG_DIR/env" || printf '%s=%s\n' "$1" "$2" >> "$CFG_DIR/env"; }
ensure_env_kv SRV_EXPLORE_RUNS "$STATE_DIR/runs.json"
ensure_env_kv SRV_EXPLORE_PROFILE_STATE "$STATE_DIR/profiles.json"
ensure_env_kv SRV_EXPLORE_HISTORY_PER_USER 15

# 5c. админ-токен /admin — генерим ОДИН раз (переустановка не трогает выданный)
if ! grep -q "^SRV_EXPLORE_ADMIN_TOKEN=" "$CFG_DIR/env"; then
  ADMIN_TOKEN="adm_$("$APP_DIR/venv/bin/python" -c 'import secrets;print(secrets.token_urlsafe(32))')"
  printf 'SRV_EXPLORE_ADMIN_TOKEN=%s\n' "$ADMIN_TOKEN" >> "$CFG_DIR/env"
  echo "==> админ-токен /admin сгенерирован (сохрани, показывается один раз):"
  echo "    $ADMIN_TOKEN"
fi

# tokens.json — единственный владелец сервис-юзер (пишет и админ-UI, и CLI от sudo -u)
[ -f "$STATE_DIR/tokens.json" ] || { echo '[]' > "$STATE_DIR/tokens.json"; }
chmod 0640 "$STATE_DIR/tokens.json"
chown -R "$USER_NAME:$USER_NAME" "$STATE_DIR"

# 5d. туннельный SSH-юзер: транспорт до MCP без HTTPS и без shell-доступа.
# Ключи живут в StateDir (пишет админка), sshd читает их AuthorizedKeysCommand'ом;
# ограничения (только проброс 8765, no shell/tty) — в drop-in, ключи чистые.
TUNNEL_USER=srvx-tunnel
if ! id "$TUNNEL_USER" >/dev/null 2>&1; then
  useradd --system --no-create-home --shell /usr/sbin/nologin "$TUNNEL_USER"
fi
[ -f "$STATE_DIR/tunnel_keys" ] || install -m 0640 -o "$USER_NAME" -g "$USER_NAME" /dev/null "$STATE_DIR/tunnel_keys"
cat > /etc/ssh/sshd_config.d/srv-explore-tunnel.conf <<EOF
Match User $TUNNEL_USER
  AuthorizedKeysFile none
  AuthorizedKeysCommand /bin/cat $STATE_DIR/tunnel_keys
  AuthorizedKeysCommandUser $USER_NAME
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
  echo "!! sshd -t не прошёл — drop-in /etc/ssh/sshd_config.d/srv-explore-tunnel.conf проверь вручную" >&2
fi

ensure_env_kv SRV_EXPLORE_PUBLIC_HOST "$(hostname -I 2>/dev/null | awk '{print $1}')"

# 6. systemd-юнит
install -m 0644 "$APP_DIR/srv_explore/systemd/srv-explore.service" /etc/systemd/system/srv-explore.service
systemctl daemon-reload
systemctl enable srv-explore.service
systemctl restart srv-explore.service

echo "==> готово. Статус: systemctl status srv-explore --no-pager"
echo "    Управление токенами: открой http://<host>:<port>/admin (админ-токен выше)."
echo "    Либо CLI от сервис-юзера:"
echo "    sudo -u $USER_NAME $APP_DIR/venv/bin/python -m srv_explore.token_store --store $STATE_DIR/tokens.json issue --label <кто>"
echo "    Доступ инженера: ключ в /home/$TUNNEL_USER/.ssh/authorized_keys (см. README),"
echo "    подключение: ssh -N -L 8765:localhost:8765 $TUNNEL_USER@<host> -i <key>"
