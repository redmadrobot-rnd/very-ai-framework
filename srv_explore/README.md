# srv-explore — серверный бандл (readonly-эксплорер за MCP)

Изолированный самодостаточный сервис: на хосте живёт readonly-агент за одним
MCP-инструментом `srv_explore(task)`. Инженер из своего Claude Code шлёт задачу — агент
читает хост (файлы, логи, код, контейнеры, БД) **только на чтение** и возвращает
findings. Ставится **отдельно**, ни от чего в основном проекте не зависит. Использование
из проекта — скилл `.claude/skills/srv-explore/`.

## Почему на хосте, а не в контейнере

Контейнер видит своё состояние (свой `df`/rootfs/версии), а нужен хост; пути для чтения
заранее неизвестны. Поэтому systemd-юнит под unprivileged OS-юзером — единственный
компонент вне docker-пайплайна сервисов.

## Модель безопасности: read-only держит РЕСУРС-СЛОЙ

Границу задаёт не парсер команд, а физическая недоступность ресурса:

| Побег | Держит |
|---|---|
| запись в файлы | read-only FS (`ProtectSystem=strict`) |
| эксфильтрация | egress-firewall (приватные сети only) |
| docker-escape | юзер вне группы docker + docker-socket-proxy |
| запись в БД | read-only роль СУБД |
| privesc/reboot | unprivileged юзер |

`guard.py` — не барьер, а **гигиена**: режет метасимволы (`>`/`;`/`$()`) и спецфайлы
`/dev/*` (понятный deny + страховка, если харденинг не включили). Всё прочее — allow.

## Состав

- `mcp_server.py` — remote MCP (streamable HTTP): `srv_explore(task)` +
  `srv_explore_status(job_id)` + `/admin` (за админ-токеном). Внутри — Claude Agent SDK
  headless, каждая Bash-команда через `guard.py`.
- `guard.py` — PreToolUse-гигиена.
- `profiles/*.py` — тонкие конфиги инструментов (клиент + read-only доступ), default-off,
  включаются в админке. Гард их не грузит.
- `admin.html` — `/admin`: выпуск/отзыв токенов, добавление туннельных юзеров, профили,
  индикатор харденинга, история сессий.
- `agent_prompt.md` — системный промпт агента.
- `token_store.py`, `tunnel_keys.py`, `run_store.py`, `backstop.py` — токены, туннельные
  ключи, история прогонов, проба харденинга (кружок).
- `install.sh` + `systemd/` + `requirements.txt` — установка на хост.

## Установка

Штатно — воркфлоу `Deploy srv-explore (host service)` (`workflow_dispatch`, выбор
окружения): копирует `srv_explore/`, зовёт `install.sh`, дописывает
`CLAUDE_CODE_OAUTH_TOKEN` (авторизация модели, агент = `claude` CLI в Agent SDK; секрет
уже есть для `@claude`), рестартует юнит.

Вручную (от root, из каталога с бандлом):

```bash
sudo bash srv_explore/install.sh
echo 'CLAUDE_CODE_OAUTH_TOKEN=...' | sudo tee -a /etc/srv-explore/env
sudo systemctl restart srv-explore
```

`install.sh` при первой установке печатает одноразовый **админ-токен** (`adm_…`) для `/admin`.

## Подключение инженера (туннель + токен)

Сервис слушает только loopback; наружу — туннельный юзер `srvx-tunnel` (создаёт
`install.sh`): shell закрыт (`nologin`), sshd drop-in разрешает ключам ровно один проброс
(`PermitOpen 127.0.0.1:8765`). Доступ выдаётся из `/admin`: **«Добавить юзера»** = label +
публичный ключ → `srvx_`-токен + готовые команды. Удаление юзера снимает ключ и токены.

```bash
ssh-keygen -t ed25519 -f ~/.ssh/srvx -N ""      # публичную часть — админу
ssh -N -L 8765:localhost:8765 srvx-tunnel@<host> -i ~/.ssh/srvx   # держать открытым
claude mcp add --transport http srv-explore http://localhost:8765/mcp \
  --header "Authorization: Bearer srvx_..."
```

Ключ = транспорт, токен = личность (сверяется по `sha256`, привязан к инстансу). Иной
транспорт (TLS-прокси/VPN) — задача окружения; бандлу нужен лишь достижимый `<URL>` + токен.

## Конфиг (`/etc/srv-explore/env`)

`SRV_EXPLORE_HOST`/`PORT` (bind, дефолт `127.0.0.1:8765`) · `SRV_EXPLORE_TOKENS`/`RUNS`/
`PROFILE_STATE` (состояние в `/var/lib/srv-explore`, писатель = сервис-юзер) ·
`SRV_EXPLORE_HISTORY_PER_USER` (дефолт 15) · `CLAUDE_CODE_OAUTH_TOKEN` (секрет, пишет
деплой) · `SRV_EXPLORE_ADMIN_TOKEN` (гейт `/admin`, генерит `install.sh`).

## В работе (коммит 2)

Провизионер `srvx-provision` (установка клиентов + создание read-only ролей БД из
админки), docker-socket-proxy, egress-firewall и unprivileged-юзер вне группы docker в
`install.sh`, exec-timeout, verify-пробы профилей. Пока не поставлено — деплой ставит
базовый юнит.
