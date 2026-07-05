---
name: srv-explore
description: >
  Безопасный readonly-эксплорер сервера субагентом srv-explore: как устроен
  allowlist/гард (shell + SQL + docker), как агент запрашивает провижининг
  readonly-роли БД, JIT-доступ (dev проще, prod с подтверждением человека), аудит,
  как добавить профиль под новую СУБД. Use при разборе «что происходит/как устроено
  на сервере» или «как безопасно посмотреть на бой, ничего не сломав».
---

# srv-explore — безопасная readonly-инспекция сервера

Инженер запускает исследование сервера агентом, который **физически не может
ничего изменить**: структура, файлы, код, логи, состояние контейнеров, readonly-
запросы к БД. Агент — субагент `.claude/agents/srv-explore.md` (readonly `tools:` +
PreToolUse-гард + `permissionMode: dontAsk`). Запуск — `/srv-explore <вопрос>`.

## Модель безопасности (6 эшелонов)

Readonly держим слоями — падение одного не открывает запись.

| L | Слой | Где |
|---|---|---|
| L1 | Read-only роль СУБД (нет write/DDL-грантов) + `statement_timeout` | сервер БД |
| L2 | `tools:` субагента без Write/Edit | `agents/srv-explore.md` |
| L3 | PreToolUse-гард: default-deny allowlist для shell/SQL/docker | `guard.py` + `profiles/` |
| L4 | JIT-доступ: dev проще, prod с подтверждением человека, TTL | GitHub Environments |
| L5 | Независимый аудит-лог (пишет `guard.py` до выполнения) | `audit/` |
| L6 | `permissionMode: dontAsk` (не bypass): неразрешённое авто-deny | frontmatter субагента |

**L1 — фундамент.** Readonly, реализованный промптом/флагом/логикой MCP, обходится
(референсный Anthropic Postgres-MCP убит SQL-инъекцией `COMMIT; DROP …`; CVE-2025-59333
обошёл проверку `startsWith("select")`). Надёжная граница — роль в самой СУБД: движок
сам не выполнит запись. Гард (L3) — defense-in-depth поверх и единственный барьер для
shell (docker/ssh/curl). В Postgres граница = **отсутствие write-грантов**, а НЕ флаг
`default_transaction_read_only` (его сессия снимает сама).

**L5 независим.** Урок Replit (июль 2025): агент удалил прод-БД и соврал про откат.
Поэтому лог выполненного пишется вне контроля агента — доверяем логу, не рассказу.

## 1. Провижининг readonly-роли БД (запрашивает агент, создаёт человек)

У агента нет прав создавать роль (это запись) — и не должно быть. Поток:

1. Агенту нужна БД. Проверяет наличие readonly-кред в окружении.
2. Нет → **выдаёт готовый рецепт** из `profiles/<db>.json` (`readonly_recipe`) + имя
   Environment Secret (`secret_env`) и останавливается. Не идёт дальше.
3. Инженер выполняет рецепт на сервере **один раз**, кладёт пароль в Secret,
   перезапускает `/srv-explore`.

Пример Postgres (PG14+): `GRANT pg_read_all_data TO inspector;` +
`ALTER ROLE inspector SET statement_timeout='15s';`. Роли **не** давать superuser,
`CREATE EXTENSION`, dblink/postgres_fdw — они пишут side-effect'ом даже в read-only.

## 2. JIT-доступ: dev vs prod

- **prod** — постоянного доступа у агента нет. Креды выдаются на сессию через штатные
  **required reviewers** окружения `prod` (тот же канал, что резолвит SSH в деплое):
  секрет не отдаётся, пока ревьюер не одобрил; короткий TTL + revoke.
- **dev** — допустим более простой доступ (без церемонии), риск ниже.
- Раскладка секретов фреймворка: host/user/имя роли → Environment **Variable**;
  пароль readonly-роли → Environment **Secret**. В код/доки креды не попадают
  (pre-commit secret-scan + trufflehog в CI).

## 3. Что агент может и не может

- **Может (read):** чтение файлов/кода (Read/Grep/Glob); `cat/tail/grep/ss/lsof/du/
  journalctl` (без `-f`); `docker ps/inspect/logs --tail/top/stats --no-stream`,
  `docker compose ps/logs/config`, `docker exec <c> <read-команда>`; `systemctl
  status/list-units/cat`; `curl` GET; `ssh <host> <read-команда>`; `SELECT` из
  allowlist профиля.
- **Не может:** мутации БД (DML/DDL), restart/rm/write в shell, POST/PUT/DELETE,
  `docker run/stop/rm/build/cp/exec-write/exec --privileged`, follow/stream
  (`-f`, `docker stats` без `--no-stream`), интерактивные сессии, обход гарда.

Гард — default-deny: чего нет в `profiles/shell.json` и что не проходит SQL/docker-
проверку — блокируется, причина возвращается агенту в stderr.

## 4. Docker (подробно)

`docker` = почти root на хосте, поэтому строгий allowlist. `docker exec` разрешён,
но **вложенная команда рекурсивно проверяется тем же allowlist** (`sh -c`/`bash -c`
не в allowlist → блок; метасимволы режутся до разбора). `docker logs -f` и `docker
stats` без `--no-stream` блокируются как зависание. Полный список — `profiles/shell.json`
(`docker_read_subcommands`, `docker_compose_read_subcommands`).

## 5. Аудит

`guard.py` пишет каждую проверенную команду в `audit/explore-<дата>.log` (JSON-строки:
время, сессия, команда, решение, причина) **до** выполнения и независимо от агента.
Путь переопределяется env `SRV_EXPLORE_AUDIT`. Логи в git не коммитятся (`.gitignore`).

## 6. Как добавить профиль под новую СУБД

DB-agnostic: ядро не знает СУБД. Новый профиль = один JSON `profiles/<db>.json`:

- `client` — бинарь клиента (`mysql`, `mongosh`, …); `kind` — `sql` (общий SELECT-гард)
  либо иной (свой allowlist read-команд);
- `readonly_recipe` — как выдать read-only роль в этой СУБД (L1); `secret_env` — имя Secret;
- для `sql`: `allow_prefixes` (разрешённые начала стейтмента), `forbid_keywords`;
- `limits` — таймаут и дефолтный LIMIT.

`guard.py` подхватит профиль по совпадению `client`. Ядро править не нужно. Для NoSQL
(Mongo/Redis) allowlist = список read-глаголов вместо SELECT.

Рецепты L1 по СУБД (из ресёрча): MySQL — `GRANT SELECT` без FILE; ClickHouse — профиль
`readonly=1`+`allow_ddl=0` (в сессии не снимается); MongoDB — роль `read` (резать
`$out`/`$merge`); Redis — ACL `on >pass ~* +@read` (без `@scripting`/`@dangerous`).

## Принципы

- Читаем, не меняем. Нужна мутация — человек делает сам, агент только предлагает.
- L1 (роль СУБД) обязателен — гард и tools его не заменяют, а дополняют.
- Узкие запросы под гипотезу, не широкие сканы прода.
- Доверяем аудит-логу, не рассказу агента.
