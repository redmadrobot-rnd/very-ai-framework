# srv-explore (server-side) — readonly-эксплорер сервера за MCP

На хосте живёт readonly-агент за одним MCP-инструментом `srv_explore(task)`. Инженер из
своего Claude Code шлёт задачу — агент читает хост (файлы, логи, контейнеры, БД) только
на чтение и возвращает findings. Креды и enforcement живут на сервере, вне машины
инженера. Концепция целиком — `docs/srv-explore-service-concept.md`.

## Почему на хосте, а не в контейнере

Контейнер видит своё состояние (свой `df`, свой rootfs, свои версии), а нужен хост.
Пути для чтения заранее неизвестны — предмонтировать нечего. Поэтому systemd-юнит под
ro OS-пользователем; это единственный компонент, живущий вне docker-пайплайна сервисов.

## Состав

- `mcp_server.py` — remote MCP (streamable HTTP, Python). Tools `srv_explore(task)` и
  `srv_explore_status(job_id)`. Внутри — Claude Agent SDK headless; каждая Bash-команда
  агента проходит через `guard.py` (тот же, что у локального субагента — единый источник
  правды). `curl`/`ssh` отключены (`SRV_EXPLORE_NO_NETWORK`), egress закрыт.
- `token_store.py` — bearer-токены: админ выдаёт/отзывает, на сервере только `sha256`.
- `install.sh` + `systemd/srv-explore.service` — установка на хост.
- `requirements.txt` — рантайм-зависимости (ставятся в venv на хосте, не в тест-окружение).

Что держит «только чтение»: readonly-роль БД (фундамент) + `guard.py` server-side (граница,
не defense) + `permission_mode=dontAsk` + узкий `allowed_tools` + `ProtectSystem=strict` в
юните + bearer-токен на входе.

## Установка (обычно через деплой)

Штатно — воркфлоу `Deploy srv-explore (host service)` (`workflow_dispatch`, выбор
окружения). Он копирует исходники, зовёт `install.sh`, дописывает авторизацию к модели из
Environment Secret и рестартует юнит. Prod — под required reviewers самого Environment.

Авторизация к модели — `CLAUDE_CODE_OAUTH_TOKEN` (агент = `claude` CLI внутри Agent SDK,
им и авторизуется). Токен уже есть в репо для `@claude` — переиспользуем, новый секрет не
заводим. Деплой пишет его в env.

Вручную на хосте (от root):

```bash
SRV_EXPLORE_ENV=dev bash srv_explore/install.sh /path/to/repo
# затем один раз положить авторизацию модели:
echo 'CLAUDE_CODE_OAUTH_TOKEN=...' >> /etc/srv-explore/env
systemctl restart srv-explore
```

## Выдача доступа инженеру

Токен генерит и выдаёт **админ** (не джобы) — от root, сервис файл только читает:

```bash
sudo /opt/srv-explore/venv/bin/python -m srv_explore.token_store \
  --store /etc/srv-explore/tokens.json issue --label alice --env dev
# печатает токен ОДИН раз — отдать инженеру; отозвать: ... revoke <id>; список: ... list
```

Инженер подключает remote MCP у себя:

```bash
claude mcp add --transport http srv-explore https://<host>/mcp \
  --header "Authorization: Bearer srvx_..."
```

Сервер сверяет `sha256` токена и его окружение на каждый запрос; нет совпадения/revoked → 401.

## Конфиг (`/etc/srv-explore/env`)

`SRV_EXPLORE_ENV` (dev|prod — идентичность инстанса, к ней привязан токен) ·
`SRV_EXPLORE_NO_NETWORK=1` (egress закрыт) · `SRV_EXPLORE_HOST`/`PORT` (bind, не в
открытый интернет — TLS за reverse-proxy окружения) · `SRV_EXPLORE_GUARD`/`AGENT_MD`/
`AUDIT`/`TOKENS`/`CWD` · `CLAUDE_CODE_OAUTH_TOKEN` (авторизация модели, секрет, пишет деплой).

## Статус проверки

Оттестировано локально (pytest): `token_store`, bearer-авторизация, мост к `guard.py`,
загрузка промпта, `NO_NETWORK`. **Не** прогонялось на живом хосте: `install.sh`, systemd-
юнит, деплой-воркфлоу, реальный запуск Agent SDK (нужен ключ + сервер) — требуют
smoke-теста на dev перед боем.
