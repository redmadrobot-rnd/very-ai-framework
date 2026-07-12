# srv-explore — серверный бандл (readonly-эксплорер за MCP)

Изолированный, самодостаточный сервис: на хосте живёт readonly-агент за одним
MCP-инструментом `srv_explore(task)`. Инженер из своего Claude Code шлёт задачу — агент
читает хост (файлы, логи, код, контейнеры, БД) **только на чтение** и возвращает
findings. Креды и enforcement живут на сервере, вне машины инженера. Ставится **отдельно**,
ни от чего в основном проекте не зависит. Как подключиться из проекта — скилл
`.claude/skills/srv-explore/`.

## Почему на хосте, а не в контейнере

Контейнер видит своё состояние (свой `df`, свой rootfs, свои версии), а нужен хост.
Пути для чтения заранее неизвестны — предмонтировать нечего. Поэтому systemd-юнит под
ro OS-пользователем; единственный компонент вне docker-пайплайна сервисов.

## Состав бандла (всё в этом каталоге)

- `mcp_server.py` — remote MCP (streamable HTTP): tools `srv_explore(task)` и
  `srv_explore_status(job_id)` (job-id + poll). Внутри — Claude Agent SDK headless;
  каждая Bash-команда агента проходит через `guard.py`. `curl`/`ssh` off
  (`SRV_EXPLORE_NO_NETWORK`), egress закрыт.
- `guard.py` + `profiles/` — PreToolUse-гард (default-deny allowlist) и профили
  (shell + БД: postgres/mongo/redis/rabbitmq). Единый источник правды политики «только чтение».
- `agent_prompt.md` — системный промпт readonly-агента (правила + формат отчёта).
- `token_store.py` (+ CLI) — bearer-токены: админ выдаёт/отзывает, на сервере только `sha256`.
- `install.sh` + `systemd/srv-explore.service` + `requirements.txt` — установка на хост.
- `docs/` — концепция и ресёрч.

Что держит «только чтение»: readonly-роль БД (фундамент) + `guard.py` server-side (граница,
не defense) + `permission_mode=dontAsk` + `ProtectSystem=strict` в юните + bearer-токен.

## Установка

Штатно — воркфлоу `Deploy srv-explore (host service)` (`workflow_dispatch`, выбор
окружения): копирует `srv_explore/`, зовёт `install.sh`, дописывает `CLAUDE_CODE_OAUTH_TOKEN`
из Environment Secret, рестартует юнит. Prod — под required reviewers Environment.

Авторизация к модели — `CLAUDE_CODE_OAUTH_TOKEN` (агент = `claude` CLI внутри Agent SDK).
Токен уже есть в репо для `@claude` — переиспользуем, новый секрет не заводим.

Вручную на хосте (от root), из каталога с бандлом:

```bash
sudo SRV_EXPLORE_ENV=dev bash srv_explore/install.sh    # аргументов не нужно, бандл самолокейтится
echo 'CLAUDE_CODE_OAUTH_TOKEN=...' | sudo tee -a /etc/srv-explore/env
sudo systemctl restart srv-explore
```

## Выдача доступа инженеру

Токен генерит и выдаёт **админ** (не джобы) — от root, сервис файл только читает:

```bash
cd /opt/srv-explore && sudo venv/bin/python -m srv_explore.token_store \
  --store /etc/srv-explore/tokens.json issue --label alice --env dev
# печатает токен ОДИН раз — отдать инженеру; отозвать: ... revoke <id>; список: ... list
```

Инженер подключает remote MCP у себя (см. скилл `srv-explore`):

```bash
claude mcp add --transport http srv-explore <URL>/mcp \
  --header "Authorization: Bearer srvx_..."
```

Сервер сверяет `sha256` токена и окружение на каждый запрос; нет совпадения/revoked → 401.

## Конфиг (`/etc/srv-explore/env`)

`SRV_EXPLORE_ENV` (dev|prod — идентичность инстанса, к ней привязан токен) ·
`SRV_EXPLORE_NO_NETWORK=1` (egress закрыт) · `SRV_EXPLORE_HOST`/`PORT` (bind) ·
`SRV_EXPLORE_GUARD`/`PROMPT`/`AUDIT`/`TOKENS`/`CWD` · `CLAUDE_CODE_OAUTH_TOKEN`
(авторизация модели, секрет, пишет деплой).

Как сервис доступен инженеру (TLS-прокси, VPN, локальный проброс, …) — задача
окружения, вне бандла. Бандлу нужен лишь достижимый `<URL>` + токен.

## Статус проверки

- **pytest:** `token_store`, bearer-авторизация, мост к `guard.py`, загрузка промпта, `NO_NETWORK`.
- **Smoke на dev-хосте (прогнано end-to-end):** `install.sh` (venv/deps/юзер/юнит) →
  сервис active; remote MCP на `127.0.0.1:8765`, `initialize`-handshake, bearer-auth
  (нет/неверный → 401, верный → 200); **реальный прогон агента** (read-задача → findings;
  write-задача → отказ + предложение человеку). Нашло и починило: порт 8080 занят
  docker-proxy → дефолт 8765; `tokens.json` 0600 ломал чтение сервисом → 0640; `python3-venv`
  доустанавливается в install.
- **Ещё НЕ прогнано:** деплой-воркфлоу (нужен мерж в main, `workflow_dispatch` виден только
  с дефолтной ветки); сетевая доступность сервиса инженеру и readonly-роль БД — под окружение.
