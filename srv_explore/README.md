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
  `srv_explore_status(job_id)` (job-id + poll) + `/admin` (за админ-токеном). Внутри —
  Claude Agent SDK headless; каждая Bash-команда проходит через `guard.py`. `curl`/`ssh`
  off (`SRV_EXPLORE_NO_NETWORK`), egress закрыт.
- `guard.py` + `profiles/` — PreToolUse-гард (default-deny allowlist) и профили
  (shell + БД: postgres/mongo/redis/rabbitmq). Единый источник правды политики «только чтение».
- `admin.html` — self-contained страница `/admin`: выпуск/отзыв токенов (пользователи),
  история сессий + монитор задач. Гейт — `SRV_EXPLORE_ADMIN_TOKEN`.
- `agent_prompt.md` — системный промпт readonly-агента (правила + формат отчёта).
- `token_store.py` (+ CLI) — bearer-токены: выдаёт/отзывает админ (UI или CLI), на
  сервере только `sha256`. `run_store.py` — история прогонов: последние N сессий НА
  ЮЗЕРА (кап `SRV_EXPLORE_HISTORY_PER_USER`, дефолт 15), самоподрезается, без ротации.
- `install.sh` + `systemd/srv-explore.service` + `requirements.txt` — установка на хост.

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

Токены выдаёт/отзывает **админ**. `install.sh` при первой установке печатает
одноразовый **админ-токен** (`adm_…`) — им гейтится `/admin`.

Основной путь — веб-панель `/admin` (тем же `<URL>`, что и MCP): ввести админ-токен →
«Выпустить токен» (label = кому/зачем) → скопировать показанный **один раз**
`srvx_…` и отдать инженеру. Там же — отзыв, список, история сессий (задача → ответ).

CLI как fallback (от сервис-юзера, чтобы владение `tokens.json` не уезжало на root):

```bash
sudo -u srv-explore /opt/srv-explore/venv/bin/python -m srv_explore.token_store \
  --store /var/lib/srv-explore/tokens.json issue --label alice
# отозвать: ... revoke <id>; список: ... list
```

Инженер подключает remote MCP у себя (см. скилл `srv-explore`):

```bash
claude mcp add --transport http srv-explore <URL>/mcp \
  --header "Authorization: Bearer srvx_..."
```

Сервер сверяет `sha256` токена на каждый запрос; нет совпадения/revoked → 401. Токен
привязан к инстансу: его хэш есть только в этом `tokens.json`, на другом сервере не пройдёт.

## Конфиг (`/etc/srv-explore/env`)

`SRV_EXPLORE_NO_NETWORK=1` (egress закрыт) · `SRV_EXPLORE_HOST`/`PORT` (bind) ·
`SRV_EXPLORE_GUARD`/`PROMPT`/`CWD` · `SRV_EXPLORE_TOKENS`/`SRV_EXPLORE_RUNS`
(хэши токенов и история — в `/var/lib/srv-explore`, единственный писатель = сервис-юзер) ·
`SRV_EXPLORE_HISTORY_PER_USER` (сколько сессий на юзера хранить, дефолт 15) ·
`CLAUDE_CODE_OAUTH_TOKEN` (авторизация модели, секрет, пишет деплой) ·
`SRV_EXPLORE_ADMIN_TOKEN` (гейт `/admin`; генерит `install.sh` один раз).

Что пишем на диск: `tokens.json` (хэши) и `runs.json` (история сессий, кап на юзера) в
`/var/lib/srv-explore`. Роста нет — история подрезается капом; живая сессия в памяти,
в файл уходит по завершении.

## Транспорт: SSH-туннель без shell-доступа

Сервис слушает только loopback; наружу его выводит туннельный юзер `srvx-tunnel`
(создаёт `install.sh`): shell закрыт (`nologin`), sshd drop-in разрешает ключам
ровно один проброс (`PermitOpen 127.0.0.1:8765`, no shell/tty/agent). Ключи инженеров
живут в `/var/lib/srv-explore/tunnel_keys` (sshd читает `AuthorizedKeysCommand`'ом),
поэтому доступ выдаётся из админки: **«Добавить юзера»** = label + публичный ключ →
токен + готовая инструкция подключения. Отзыв токена снимает и ключ. Инструкция для
инженера — публичная страница `/` самого сервиса.

Инженер держит туннель и ходит в MCP через localhost:

```bash
ssh -N -L 8765:localhost:8765 srvx-tunnel@<host> -i <key>
claude mcp add --transport http srv-explore http://localhost:8765/mcp \
  --header "Authorization: Bearer srvx_..."
```

Ключ = транспорт (шифрование, вход в порт), токен = личность (история, отзыв) —
двухфакторно; компрометация ключа не даёт ни shell, ни MCP без токена. Иной
транспорт (TLS-прокси, VPN) — задача окружения, бандлу нужен лишь достижимый
`<URL>` + токен.

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
