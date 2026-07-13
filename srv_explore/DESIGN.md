# srv-explore — модель безопасности и профили

Ридонли-эксплорер сервера: инженер подключается по remote MCP + bearer, ходит по
серверу (логи/файлы/код/контейнеры/БД) **только на чтение**, без SSH и без серверных
кредов на руках.

## Принцип: граница — на уровне ресурса, не в парсере команд

Перечислить все команды нельзя — ни разрешённые, ни опасные (всегда найдётся
`socat`/`busybox`/exotic-интерпретатор). Поэтому read-only держится **физической
недоступностью ресурса**, а не тем, что гард узнал бинарь:

| Побег | Чем заперт (не гард) | Гарантирует |
|---|---|---|
| запись в ФС | `ProtectSystem=strict` — FS read-only на уровне kernel | деплой (systemd unit) |
| эксфильтрация наружу | egress-firewall: `IPAddressAllow=` приватные сети + loopback, `IPAddressDeny=any` | деплой (systemd unit) |
| docker-escape | агент-юзер **не в группе docker** → сокет недоступен | деплой (юзер) |
| запись в remote БД | **read-only роль СУБД** | владелец БД (рецепт в профиле) |
| привилегии | unprivileged юзер, без sudo | деплой (юзер) |

Всё generic — ноль знаний про конкретный бинарь/сервер. `python -c`, `nc`, что угодно
новое — упирается в firewall/RO-FS/права автоматически.

## Гард схлопнут: барьер на ресурсе, не в парсере

Раз read-only держит ресурс-слой, парсить команды незачем. Гард (`guard.py`,
PreToolUse-хук) сжат до:
- **инварианты**: метасимволы `>`/`;`/`&`/`$()`, пайпы, спецфайлы `/dev/*`;
- **UX-гейт профиля**: команда, которой владеет **выключенный** профиль → deny с
  понятным «включи профиль X» (вместо криптованной ошибки коннекта).

Чего в гарде **больше НЕТ** (было — снято как дубль ресурс-слоя):
- SQL/mongo/redis forbid-списки — их заменяет **read-only роль БД**;
- docker-парсинг — заменяет **docker-socket-proxy**;
- curl-allowlist — заменяет **egress-firewall**;
- write-флаг-стражи файлов — заменяет **RO-FS**.

Анти-подвисание (`tail -f`) — не гард, а **exec-timeout** в MCP-раннере (каждая
команда под таймаутом; висящий стрим просто отваливается).

## Проверка барьера вместо парсинга каждого запроса

При включении профиля провизионер **один раз проверяет**, что ресурс правда read-only
(как зелёный кружок RO-FS): пробует безобидную запись → ждёт отказ →
**зелёный «роль read-only подтверждена» / красный**. Честный сигнал вместо ложной
уверенности, что forbid-список всё перечислил.

## Профили = тонкий конфиг, не парсеры

Профиль (`profiles/<id>.py`) больше **не парсит** команды — он декларирует, как
подключить инструмент и как его проверить:

```python
ID = "postgres"
COMMANDS = ["psql"]              # для UX-гейта (deny, пока профиль off)
DESC = "PostgreSQL (read-only роль)"
PACKAGES = ["postgresql-client"] # что ставит `srvx-provision install`
CREDS_ENV = "PG_INSPECTOR_DSN"   # какой Secret нужен агенту
SETUP = "CREATE ROLE inspector ...; GRANT pg_read_all_data TO inspector;"
VERIFY = "CREATE TABLE _srvx_probe(x int)"  # должно упасть permission denied
```

Никакого `check()` с forbid-списками. Роль/прокси/firewall — вот барьер.

- **База:** `shell` — **permissive**. RO-FS + firewall + unpriv-юзер делают безопасным.
- **Escape-инструменты** (docker/postgres/mongo/redis/rabbitmq/http) — профили,
  **default-off**, владеют своими командами (для UX-гейта):
  - **off** → `psql`/`docker` → **deny** («включи профиль»);
  - **on** → команда исполняется; read-only держит ресурс-слой (роль/прокси);
  - ничей → permissive shell.

## Как это ставится (провижининг)

Привилегированный шаг **изолирован от песочницы агента** — иначе агент под RO-FS
ничего не установит, а дать ему root = убить всю модель.

### Два процесса

- **agent-runner** — systemd-сервис, где крутятся MCP + query агента. Unprivileged
  юзер, RO-FS, firewall, вне группы docker. Сюда же admin-эндпоинт.
- **`srvx-provision`** — маленький root-скрипт с **фикс-набором действий**. Вызывается
  agent-runner'ом через `sudo` с точечным NOPASSWD-грантом (в sudoers — только эти
  команды, аргумент = только `profile_id`, ничего сырого):

  | Действие | Что делает |
  |---|---|
  | `install <profile_id>` | `apt-get install -y` **фикс-`PACKAGES` из модуля** профиля |
  | `create-role <profile_id>` | читает **admin-DSN со stdin** (не argv → не в ps/лог), гонит `SETUP`-рецепт клиентом, генерит рандомный пароль, печатает в stdout **только** read-only DSN |
  | `proxy-up docker` | поднимает **docker-socket-proxy** (read-only API: containers/logs/inspect on, POST off), биндит локальный порт; `DOCKER_HOST` агента → на прокси |
  | `verify <profile_id>` | пробная запись → ждёт отказ → ok/fail для кружка |

### Поток «включить postgres» в админке

1. **клиент**: `srvx-provision install postgres` → ставит `psql` системно. RO-FS
   сендбоксит только агента, не root → агент видит бинарь read-only.
2. **read-only роль** — выбор:
   - **(a) авто:** вставляешь **admin-DSN** → agent-runner передаёт его
     `srvx-provision create-role` по stdin → роль создана, в Secret-файл (StateDir,
     0600) ложится **только** read-only DSN. Admin-DSN — в памяти, обнуляется, никуда
     не пишется. Хранить superuser **запрещено**.
   - **(b) готовые креды:** вставляешь только read-only DSN → сразу в Secret-файл.
3. `srvx-provision verify postgres` → зелёный/красный кружок.
4. тумблер on → `profiles.json` (StateDir). Гард читает свежим, без рестарта.

Для docker шаг 1 = `proxy-up docker` (вместо apt), роли нет.

### Что ставит `install.sh` один раз (деплой)

- agent-runner + hardening: `ProtectSystem=strict`, `ProtectHome`, `PrivateTmp`;
- egress-firewall (приватные сети allow, остальное deny);
- unprivileged юзер вне группы docker, без sudo кроме грантов ниже;
- `srvx-provision` + sudoers-грант (только 4 действия выше);
- StateDir (`profiles.json`, Secret-файл 0600);
- SSH-туннель-юзер `srvx-tunnel`.

Read-only DSN агент читает из Secret-файла на запрос; admin-DSN не хранится нигде.
Кто харденинг не обеспечил — его проблемы: гард один read-only не гарантирует.

## Транспорт

Сервер без HTTPS → SSH-туннель через ограниченного юзера `srvx-tunnel` (nologin,
`PermitOpen 127.0.0.1:8765`, `ForceCommand nologin`). Сервис биндится на loopback.
Авторизация MCP — bearer-токен (выдаётся в админке по label+pubkey).
