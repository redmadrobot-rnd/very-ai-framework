#!/usr/bin/env bash
# Установка server-side srv-explore на хост (systemd, ro OS-user). Идемпотентна.
# Запускать от root на целевом сервере (dev/prod). Секреты (ANTHROPIC_API_KEY) сюда
# НЕ передаём аргументами — их пишет деплой в /etc/srv-explore/env (см. README).
#
# Использование:
#   sudo SRV_EXPLORE_ENV=dev bash install.sh /path/to/repo
#
# repo — каталог с исходниками (srv_explore/, .claude/skills/srv-explore/,
# .claude/agents/srv-explore.md). По умолчанию — родитель этого скрипта.
set -euo pipefail

REPO="${1:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
ENV_NAME="${SRV_EXPLORE_ENV:-dev}"
APP_DIR=/opt/srv-explore
CFG_DIR=/etc/srv-explore
LOG_DIR=/var/log/srv-explore
USER_NAME=srv-explore

echo "==> srv-explore install (env=$ENV_NAME, repo=$REPO)"

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

# 3. код (только нужное; guard/profiles/agent — источник правды из репо)
cp -r "$REPO/srv_explore" "$APP_DIR/"
install -d "$APP_DIR/.claude/skills" "$APP_DIR/.claude/agents"
cp -r "$REPO/.claude/skills/srv-explore" "$APP_DIR/.claude/skills/"
cp "$REPO/.claude/agents/srv-explore.md" "$APP_DIR/.claude/agents/"
chown -R "$USER_NAME:$USER_NAME" "$APP_DIR"

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

# 5. env-файл (несекретные дефолты; ANTHROPIC_API_KEY дописывает деплой)
if [ ! -f "$CFG_DIR/env" ]; then
  cat > "$CFG_DIR/env" <<EOF
SRV_EXPLORE_ENV=$ENV_NAME
SRV_EXPLORE_NO_NETWORK=1
SRV_EXPLORE_HOST=127.0.0.1
SRV_EXPLORE_PORT=8765
SRV_EXPLORE_CWD=/
SRV_EXPLORE_GUARD=$APP_DIR/.claude/skills/srv-explore/guard.py
SRV_EXPLORE_AGENT_MD=$APP_DIR/.claude/agents/srv-explore.md
SRV_EXPLORE_AUDIT=$LOG_DIR/explore.log
SRV_EXPLORE_TOKENS=$CFG_DIR/tokens.json
# CLAUDE_CODE_OAUTH_TOKEN (авторизация модели для агента) — дописывает деплой, не коммитить.
EOF
  chmod 0640 "$CFG_DIR/env"
  chown root:"$USER_NAME" "$CFG_DIR/env"
fi
# tokens.json — читает сервис, пишет админ через CLI (issue/revoke)
[ -f "$CFG_DIR/tokens.json" ] || { echo '[]' > "$CFG_DIR/tokens.json"; chmod 0640 "$CFG_DIR/tokens.json"; chown root:"$USER_NAME" "$CFG_DIR/tokens.json"; }

# 6. systemd-юнит
install -m 0644 "$REPO/srv_explore/systemd/srv-explore.service" /etc/systemd/system/srv-explore.service
systemctl daemon-reload
systemctl enable srv-explore.service
systemctl restart srv-explore.service

echo "==> готово. Статус: systemctl status srv-explore --no-pager"
echo "    Выдать токен (от root — сервис файл только читает):"
echo "    sudo $APP_DIR/venv/bin/python -m srv_explore.token_store --store $CFG_DIR/tokens.json issue --label <кто> --env $ENV_NAME"
