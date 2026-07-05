# srv-explore — концепт

Возможность фреймворка: агент, который под кастомным промптом исследует состояние и
устройство сервера **только на чтение** — структура, файлы, код, логи, состояние
контейнеров, readonly-запросы к БД. Не оператор, который чинит, — безопасный
«исследовательский взгляд» в сервер (dev или prod).

## Зачем

Инженеру нужно быстро понять, что происходит и как устроено на сервере (почему растёт
латенси, что в логах, какой стейт в БД, как разложены сервисы), не превращая это в
риск: без шанса что-то изменить, уронить или удалить. Ручной доступ — стресс и
человеческий фактор; полностью автономный агент с write-правами — путь к
«Replit-сценарию» (июль 2025: агент во время code freeze удалил прод-БД ~1200
записей, сфабриковал ~4000 фейковых юзеров, соврал про невозможность отката —
подтверждено Fortune и AI Incident DB #1152). Эксплорер — середина: агентская
скорость исследования при физической невозможности навредить.

## Форма: скилл + декларативный субагент

Реализуем как **декларативный субагент** `.claude/agents/srv-explore.md` плюс
скилл-обёртку, команду и guard.

Почему декларативный субагент, а не спавн через `Task` из скилла: для агента,
ходящего на сервер, **способ задания — граница безопасности**. Frontmatter `tools:` —
whitelist уровня харнесса, а не уговор в промпте: инструмента, которого нет в списке,
движок физически не даст вызвать. Ровно так Anthropic делает readonly-субагенты
Explore/Plan («read-only tools; Write and Edit denied») и приводит в доке готовый
пример `db-reader` (субагент + PreToolUse-хук, режущий не-SELECT). Повторяем
проверенный паттерн, а не изобретаем.

### Артефакты

| Артефакт | Путь | Роль |
|---|---|---|
| Субагент | `.claude/agents/srv-explore.md` | кастомный system-prompt + readonly `tools:` + `permissionMode: dontAsk` + guard-хук |
| Скилл | `.claude/skills/srv-explore/SKILL.md` | процедура: провижининг, JIT-доступ, allowlist, аудит, добавление профиля |
| Команда | `.claude/commands/srv-explore.md` | энтрипоинт `/srv-explore <вопрос>`, спавнит субагента |
| Guard + профили | `.claude/skills/srv-explore/guard.py`, `profiles/*.json` | PreToolUse-гард и DB-agnostic профили доступа |
| Тесты | `tests/test_srv_explore_guard.py` | контракт гарда: allow/deny-кейсы, включая 2 реальных обхода |

## Модель безопасности: 6 эшелонов

| L | Слой | Кем enforce | Обязателен |
|---|---|---|---|
| **L1** | Read-only роль СУБД (нет write/DDL-грантов) + `statement_timeout` | движок СУБД | **фундамент** |
| **L2** | `tools:` субагента без Write/Edit, без лишних `mcp__*` | харнесс Claude Code | да |
| **L3** | PreToolUse-гард: default-deny allowlist (не-SELECT SQL, не-read shell/docker) | харнесс (хук) | да |
| **L4** | JIT-доступ: dev проще; prod — human approval, TTL + revoke | GitHub Environments | да |
| **L5** | Независимый аудит-лог (пишется вне агента, до выполнения) | файл лога | да |
| **L6** | `permissionMode: dontAsk` (не `bypassPermissions`) | конфиг субагента | да |

**Почему L1 — фундамент, а не опция.** Урок однозначен: readonly, реализованный
логикой MCP-сервера, промптом или флагом, — фикция. Два подтверждённых обхода:
референсный Anthropic Postgres-MCP убит SQL-инъекцией (`COMMIT; DROP SCHEMA public
CASCADE;` закрывал read-only транзакцию — Datadog Security Labs, сервер в архиве с
10.07.2025); CVE-2025-59333 (CVSS 8.1) обошёл проверку `startsWith("select")`
запросом `SELECT fn_with_side_effects()`. Надёжная граница — роль в самой СУБД. И
важная поправка по Postgres: `default_transaction_read_only=on` — **не граница**
(это USERSET-GUC, сессия снимает его сама через `SET … TO off`); граница = отсутствие
write/DDL-грантов + отсутствие superuser/extensions.

**Почему L5 независим.** Урок Replit: агент может соврать о сделанном. Лог выполненного
пишется до получения результата и вне контроля агента — доверяем логам, не рассказу.

**Почему L6 = dontAsk, а не default.** `dontAsk` авто-отклоняет всё, что явно не
разрешено, без промпта — жёстче `default`. Побочно: исход хука `ask` (эскалация
человеку) под `dontAsk` авто-деется, поэтому в гарде мы его **не используем** — гард
даёт только allow/deny (deny = стоп + причина; эскалацию человеку делает сам агент
текстом, на своём уровне). Нюанс: если родительская сессия в `bypass/auto/acceptEdits`
— `permissionMode` субагента игнорируется; это документируем.

## Guard: логика (L3)

Default-deny allowlist. `guard.py` читает PreToolUse-JSON со stdin, проверяет команду и
возвращает: **allow** → JSON `permissionDecision=allow` + exit 0; **deny** → причина в
stderr + JSON `permissionDecision=deny` + exit 2 (hard block, fail-closed). Любая
ошибка разбора → deny.

Покрытие: SQL (`-c "<SELECT…>"`, один стейтмент, forbid-слова), shell (allowlist
read-команд), curl (только GET), ssh (только с read-командой, интерактив/туннель —
блок), systemctl (только read-подкоманды), docker (см. ниже). Пайплайны (`|`)
разбираются посегментно; метасимволы записи/подстановки (`` ` `` `$()` `>` `<` `;` `&`)
режутся до разбора.

**Сознательно исключены из allowlist** (иначе read-only дырявый): интерпретаторы
`sed`/`awk` — их скрипты умеют писать в файл (`sed 'w file'`, `awk 'print>…'`) и
исполнять shell (`sed e`, `awk system()/|getline`), надёжно ограничить нельзя; `env`
— исполняет переданную команду; `yq -i` — запись in-place. Для docker/systemctl в
allowlist нет noun-форм (`docker image/context/volume/network`, `system`) — у них есть
write-подкоманды (`… rm/prune`); листинг доступен через top-level (`docker images`,
`docker ps`).

## Docker: что можно и что нельзя

`docker` = почти root на хосте, поэтому самый строгий allowlist.

**Разрешено (read):** `ps`, `inspect`, `logs --tail` (без `-f`), `top`, `stats
--no-stream`, `images`, `port`, `version`, `info`, `df`, `history`; `docker compose
ps/logs/config/top/images/version`; **`docker exec <c> <read-команда>`** —
заглянуть внутрь контейнера (`cat`/`ls`/`ps`/`tail`).

**Запрещено (мутация/опасность):** жизненный цикл (`run/create/start/stop/restart/
kill/pause/rm/rmi/prune`), образы (`build/pull/push/commit/tag/save/load/export`),
файлы (`cp`, `exec` с не-read командой), конфиг/сеть (`update/rename/network/volume/
swarm/service`), интерактив (`attach`, `exec -it sh/bash`).

**Ключевое решение — `docker exec` разрешён с рекурсивной проверкой.** Заглянуть в
контейнер — половина смысла эксплорера. Безопасность держится на трёх вещах вместе:
(1) вложенная команда сама должна быть в read-allowlist; (2) `sh -c`/`bash -c` не в
allowlist → блок (нельзя протащить произвольную строку); (3) метасимволы режутся до
разбора; плюс `--privileged`/`-d` на exec запрещены. Остаточный риск — read-команда
прочитает то, к чему у контейнера есть доступ (в т.ч. смонтированный хост); это
**чтение**, ровно назначение инструмента.

**Анти-зависание (не security, но важно):** `docker logs -f`/`--follow`, `docker
stats` без `--no-stream`, `docker events`, `tail -f`, `journalctl -f` — блок (иначе
агент виснет на стриме).

## Провижининг readonly-роли БД

Роль создаёт **человек**, инициирует запрос **агент** (сам роль создать не может — это
запись). Поток: агент проверяет наличие readonly-кред → если нет, выдаёт готовый рецепт
из `profiles/<db>.json` (`readonly_recipe`) + имя Environment Secret (`secret_env`) и
останавливается → инженер выполняет рецепт один раз, кладёт пароль в Secret,
перезапускает. Прозрачно, повторяемо, human-in-the-loop.

## DB-agnostic: профили доступа

Ядро не знает СУБД. Под каждую — профиль `profiles/<db>.json`: `client`, `kind`,
`readonly_recipe` (рецепт L1), `secret_env`, для SQL — `allow_prefixes`/`forbid_keywords`,
`limits`. В шаблон кладём ядро + интерфейс профиля + референс-профиль Postgres; остальные
СУБД — по мере надобности, каждая = один JSON без правки ядра. Рецепты L1 собраны в
скилле (MySQL/ClickHouse/MongoDB/Redis).

## Транспорт и JIT-доступ

Эксплорер бежит с машины инженера, ходит на хост по существующему SSH-механизму CICD,
там выполняет read-команды и readonly-запросы к БД. Постоянного prod-доступа нет:
readonly-креды выдаются на сессию с подтверждением человека через GitHub Environment
required reviewers (короткий TTL + revoke). На dev — доступ проще. Несекретное
(host/user) → Variable, пароль → Secret.

## Результат

Субагент возвращает структурированный findings-отчёт: вопрос → что проверил → находки →
гипотеза → рекомендация. Действия по результатам выполняет человек. Параллельно — аудит-лог.

## Раздача через setup-framework

- Скилл и команда копируются wildcard'ом (`.claude/skills/*`, `.claude/commands/*.md`).
- `.claude/agents/*.md` копируется установщиком (`cp -r .claude/agents/`).
- PreToolUse-гард — **в frontmatter субагента** (`hooks:`) → scoped только на эксплорер.
- **Только копированием, не плагином.** У plugin-агентов поля `hooks`/`permissionMode`
  игнорируются «for security reasons» — плагин молча сломал бы L3 и L6. Дока Claude Code
  сама рекомендует копировать такие агенты в `.claude/agents/`; так же Anthropic раздаёт
  свой security-review (копируемый файл + Action, не плагин).

## Границы объёма (эта итерация)

Делаем: субагент + скилл + команда + guard.py + Postgres-профиль + тесты + правка
setup-framework + этот концепт. **Не** делаем сейчас: адаптеры под остальные СУБД
(рецепты собраны, JSON — по надобности), always-on сервис/бот у прод-контура и вызов по
инциденту через GitHub Action (возможная следующая итерация).

## Адверсариальная проверка гарда

Гард прогнан через adversarial-workflow (агенты придумывали обходы, каждый payload
эмпирически проверен реальным гардом). Первый прогон вскрыл **24 обхода** из 25
кандидатов — плоский allowlist имён команд оказался дырявым: `ssh -o
ProxyCommand=…` (локальное исполнение!), `docker exec --privileged=true` (обход
deny через `--flag=value`), `sort -o file`, `curl -D/-K/--url-query`, `yq
--split-exp`, `date -s`, чтение `/dev/zero`, `set_config('statement_timeout','0')`,
DNS-эксфильтрация. Codex-ревью (PR #45, два прохода) добавило ещё: несколько `-c`
в psql (исполняются все, проверялся последний), `ss -K` (убивает сокеты),
`journalctl --vacuum/--rotate` (чистит журнал), `find -fprint0`, `tree -o` (пишет в
файл), follow только в точной форме (`docker logs --follow=true`, `tail -F/--follow=name`),
и проактивно `uniq IN OUT` (второй аргумент = файл записи); третий проход — `psql
--file=`/`-f/path` (слитные формы мимо точного токена), слитные follow `docker logs
-ft`/`journalctl -fu`, и хук на `python` вместо портируемого `python3||python`. Все
закрыты и зафиксированы в `tests/test_srv_explore_guard.py` (115 кейсов). Урок:
**разрешить имя команды недостаточно — нужен per-command контроль опасных флагов +
нормализация `--flag=value`/слитных форм + allowlist для curl/ssh**.

## Остаточные риски (принятые)

- **Тяжёлый/бесконечный SELECT (DoS).** `WITH RECURSIVE …` или большой
  `generate_series` гард не блокирует (легитимный read-запрос от бесконечного не
  отличить). Граница — `statement_timeout` роли (L1): запрос обрывается за 15с, и
  снять таймаут нельзя (`set_config`/`set` в forbid). Экспозиция ≤ таймаута CPU БД —
  та же, что у любого залогиненного пользователя. Приемлемо.
- **Сетевой egress / DNS.** Эксфильтрация через сеть — вне зоны гарда (метасимволы
  подстановки режутся, поэтому динамические данные в URL не подставить, но статичный
  beacon возможен). DNS-резолверы (`dig/nslookup/host/getent`) и `hostname` убраны из
  allowlist, чтобы срезать лёгкий канал; остаточный контроль — L4 (egress-правила
  хоста, network-allowlist) и запуск против известных хостов.
- **Сайд-эффекты SQL-функций в общем случае.** Гард блокирует известные опасные имена
  функций; конечная граница — L1 (readonly-роль без прав на привилегированные функции
  вроде `pg_read_file`/`pg_create_restore_point`).

## Prior art (обоснование, проверено ресёрчем июль 2026)

- **Официально:** `.claude/agents/*.md`, `tools:`/`hooks:`/`permissionMode` как границы
  харнесса, readonly-паттерн Explore/Plan, пример `db-reader` — дока Claude Code.
- **Readonly к БД надёжен только на уровне роли СУБД**; MCP/промпт/флаг обходятся:
  deprecated Anthropic Postgres-MCP (Datadog SQL-injection), CVE-2025-59333
  (executeautomation). Многослойный референс — bettyguo/mcp-postgres (роль+AST-guard+
  tx-envelope+аудит, 1:1 наши L1/L3/L5), crystaldba/postgres-mcp.
- **Готовых readonly MCP для SSH/Docker нет** — только regex-whitelist; подтверждает
  собственный Bash+guard-подход.
- **Готовых агентов «взять целиком» нет**: HolmesGPT (Apache-2.0, вне k8s) — отдельный
  Python-рантайм, не Claude Code-субагент; коммерческие AI SRE — закрытые SaaS.
  Публичного readonly-эксплорера сервера нет — это новая композиция известных кирпичей.
- **JIT-доступ:** Vault dynamic secrets, GitHub Environment required reviewers.
- **Аудит:** урок Replit (Fortune, AIID #1152) — независимый лог, no `bypassPermissions`,
  least privilege.
