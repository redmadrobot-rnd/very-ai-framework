#!/usr/bin/env bash
# Установка изолированного server-side srv-explore на хост (systemd, ro OS-user).
# Бандл самодостаточен: всё (mcp_server, guard, profiles, prompt, token_store) — в
# каталоге этого скрипта. Идемпотентно. Запускать от root на целевом сервере.
# Секреты (CLAUDE_CODE_OAUTH_TOKEN) сюда НЕ передаём — их пишет деплой в
# /etc/srv-explore/env (см. README).
#
# Использование:
#   sudo SRV_EXPLORE_ENV=dev bash srv_explore/install.sh
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"   # каталог бандла (srv_explore/)
ENV_NAME="${SRV_EXPLORE_ENV:-dev}"
APP_DIR=/opt/srv-explore
CFG_DIR=/etc/srv-explore
LOG_DIR=/var/log/srv-explore
STATE_DIR=/var/lib/srv-explore   # writable: tokens.json + runs.jsonl (админ-UI пишет их)
USER_NAME=srv-explore

echo "==> srv-explore install (env=$ENV_NAME, src=$SRC)"

# 1. ro OS-пользователь (без логина), в группах для чтения docker/journal/логов
if ! id "$USER_NAME" >/dev/null 2>&1; then
  useradd --system --no-create-home --shell /usr/sbin/nologin "$USER_NAME"
fi
for grp in docker systemd-journal adm; do
  getent group "$grp" >/dev/null 2>&1 && usermod -aG "$grp" "$USER_NAME" || true
done

# 2. каталоги
install -d -o "$USER_NAME" -g "$USER_NAME" "$APP_DIR" "$LOG_DIR"
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
SRV_EXPLORE_ENV=$ENV_NAME
SRV_EXPLORE_NO_NETWORK=1
SRV_EXPLORE_HOST=127.0.0.1
SRV_EXPLORE_PORT=8765
SRV_EXPLORE_CWD=/
SRV_EXPLORE_GUARD=$APP_DIR/srv_explore/guard.py
SRV_EXPLORE_PROMPT=$APP_DIR/srv_explore/agent_prompt.md
SRV_EXPLORE_TOKENS=$STATE_DIR/tokens.json
SRV_EXPLORE_RUNS=$STATE_DIR/runs.json
SRV_EXPLORE_HISTORY_PER_USER=15
# CLAUDE_CODE_OAUTH_TOKEN (авторизация модели для агента) — дописывает деплой, не коммитить.
# SRV_EXPLORE_ADMIN_TOKEN (гейт /admin) — генерится ниже при первой установке.
# SRV_EXPLORE_AUDIT=$LOG_DIR/explore.log — аудит команд ОПЦИОНАЛЕН: раскомментируй,
#   чтобы гард писал allow/deny в лог (для комплаенса). По дефолту off — не пухнет.
EOF
  chmod 0640 "$CFG_DIR/env"
  chown root:"$USER_NAME" "$CFG_DIR/env"
fi

# 5b. ключи, которых может не быть в уже существующем env (идемпотентно, без перезаписи)
ensure_env_kv() { grep -q "^$1=" "$CFG_DIR/env" || printf '%s=%s\n' "$1" "$2" >> "$CFG_DIR/env"; }
ensure_env_kv SRV_EXPLORE_RUNS "$STATE_DIR/runs.json"
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

# 6. systemd-юнит
install -m 0644 "$APP_DIR/srv_explore/systemd/srv-explore.service" /etc/systemd/system/srv-explore.service
systemctl daemon-reload
systemctl enable srv-explore.service
systemctl restart srv-explore.service

echo "==> готово. Статус: systemctl status srv-explore --no-pager"
echo "    Управление токенами: открой http://<host>:<port>/admin (админ-токен выше)."
echo "    Либо CLI от сервис-юзера:"
echo "    sudo -u $USER_NAME $APP_DIR/venv/bin/python -m srv_explore.token_store --store $STATE_DIR/tokens.json issue --label <кто> --env $ENV_NAME"
