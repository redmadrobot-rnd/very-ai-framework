# CI/CD — базовый пайплайн

CI/CD для контейнерных проектов на GitHub Actions.
Подходит репозиторию, где сервисы лежат в `services/<имя>/` (каталог настраивается —
`SERVICES_DIR`; каждый со своим `Dockerfile`) и собираются через `docker-compose.yml`.
Имена сервисов, порты, конфиг нигде не захардкожены — пайплайн обнаруживает сервисы сам.

Модель ветвления: **GitHub Flow + release-теги**.

## Как это работает (одним взглядом)

```
pre-commit (локально)         ruff (--fix) + ruff-format + detect-secrets + KB-lint; падение → не коммитим
        │ commit + push
        ▼
push в feature/*              CI НЕ запускается — гейт до PR держит локальный pre-commit
        │ открыть PR
        ▼
PR в main                     PR CI: checks (ruff + security) ‖ tests (затронутые тест-каталоги, -m "not heavy")
        │                     по запросу: «@codex review» / «@codex …» (вопрос) / «@claude …»
        │                     (авто-ревью Codex при открытии PR — по умолчанию выкл; вкл variable CODEX_AUTO_REVIEW=true)
        │ зелёный CI + аппрув человека
        ▼
merge в main                  Build changed & deploy dev: собрать ТОЛЬКО изменённые образы → деплой на dev
        │                     (тесты/чеки на мерж не гоняются; полный прогон — вручную: Full tests)
        ▼
release tag v*                полный прогон тестов → промоут готовых образов (каждый по :<sha> последнего коммита main, менявшего сервис → :vX по digest) → deploy prod
                              откат: деплой предыдущего :vX (manual-deploy, build=off)
```


## Что в репозитории (CI/CD-часть)

| Путь | Назначение |
|---|---|
| `.github/workflows/pr.yml` | PR CI: `checks` + `tests` (затронутые тест-каталоги, без heavy); авто-ревью Codex — по умолчанию выкл (variable `CODEX_AUTO_REVIEW`) |
| `.github/workflows/manual-tests.yml` | `Full tests (manual)` — ручной полный прогон CI (`workflow_dispatch`, ветка выбирается в «Use workflow from»): `markers`; `checks` + `tests` вкл heavy |
| `.github/workflows/_checks.yml` | Reusable: lint (ruff check + format) + security (trufflehog, semgrep, pip-audit) |
| `.github/workflows/_tests.yml` | Reusable: discover тест-окружений по `pyproject` → матрица pytest на `uv` |
| `.github/workflows/_build.yml` | Reusable: build+push образов в GHCR (матрица по сервисам, gha-cache) |
| `.github/workflows/_deploy.yml` | Reusable: deploy на хост окружения (preflight → .env → scp → ssh `deploy.sh`) |
| `.github/workflows/codex-command.yml` | Codex-команды в PR: `@codex review` / `@codex …` (вопрос) |
| `.github/workflows/claude.yml` | `@claude` — правки по запросу |
| `.github/workflows/deploy-dev.yml` | push в `main` → build изменённых → deploy dev |
| `.github/workflows/release.yml` | tag `v*` → тесты → промоут образов (каждый пинится по `:<sha>` последнего коммита main, менявшего его каталог; нет образа → релиз падает, не откатываясь на `:latest`; digest→`:vX`, без сборки) → deploy prod; откат — деплой предыдущего `:vX` |
| `.github/workflows/manual-deploy.yml` | ручной build и/или deploy матрицей (`workflow_dispatch`); deploy-only по тегу (`build=off`) |
| `.github/scripts/codex_review.py` | Codex-ревьюер (JSON-находки → inline-review) |
| `.github/scripts/codex_answer.py` | Codex-ответчик на `@codex …` (PR-тред / inline-тред) |
| `.github/scripts/services.sh` | динамическое обнаружение сервисов (`services/*`, `list` / `changed` / `select`) |
| `.github/scripts/discover-test-dirs.sh` | обнаружение тест-каталогов по `pyproject` |
| `.github/scripts/changed-test-dirs.sh` | вычисление затронутых тест-каталогов для PR (`changed_only`) |
| `.github/scripts/deploy.sh` | деплой по SSH (login GHCR + docker compose) |
| `.pre-commit-config.yaml` | локальный гейт: ruff (--fix) + ruff-format, detect-secrets, KB-lint |
| `services/<имя>/` | твои сервисы (каждый — со своим `Dockerfile`); в шаблоне их нет |
| `docker-compose.example.yml` | образец compose — скопируй в `docker-compose.yml` под свои сервисы |
| `pyproject.toml` | конфиг ruff + pytest (маркер `heavy`); `[project]` — сигнал тест-окружения |
| `skills/setup-framework/` | скилл: развернуть этот CICD в другом репозитории |

## Конфиг и секреты

Хранятся в **GitHub → Settings → Environments** (`dev`, `prod`, …), резолвятся
по окружению автоматически:

- **Variables** — несекретный конфиг: `SSH_HOST`, `SSH_USER` (видно, какой сервер
  к какому стенду), несекретная `.env` приложения — в одной переменной `APP_DOTENV`.
  Опционально `COMPOSE_PROFILES` — какой поднабор сервисов поднимать на окружении
  ([compose profiles](https://docs.docker.com/compose/how-tos/profiles/)): напр. на
  `prod` — `monitoring`, на `dev` — пусто. Пусто/не задано = все дефолтные сервисы
  (профильные не стартуют). Управляет составом стека, а не значениями — потому это
  отдельная переменная, а не строка в `APP_DOTENV`.
- **Secrets** — чувствительное: `SSH_KEY` (приватный ключ), `APP_SECRET` (секретная
  часть `.env`, дописывается в конец строкой `APP_SECRET=…`), прочие ключи/токены.

Деплой собирает `.env` из `APP_DOTENV` + `APP_SECRET` и прокидывает его в контейнеры
через `env_file`.
Каталог деплоя на хосте — `/srv/deploy/<project>/<env>` (неймспейс по имени репо), стек
изолирован по `COMPOSE_PROJECT_NAME=<project>-<env>`, поэтому на одном сервере уживается
несколько проектов и стендов.

Обычный деплой пересоздаёт только затронутые сервисы. Но если изменился
`docker-compose.yml` (`deploy.sh` хэширует его в `.stack.sha`) — реконсилится весь стек,
чтобы инфра/БД/роутеры подхватили новый конфиг, а не остались на старом контейнере.

### Каталог сервисов — `SERVICES_DIR`

По умолчанию сервисы ищутся в `services/`. Другую раскладку задаёт **repo/org-уровня
variable** `SERVICES_DIR` (Settings → Secrets and variables → Actions → Variables) —
её подхватывают и скрипты (`services.sh`, `*-test-dirs.sh`), и пути build-контекста в
workflow'ах. Можно вложенный путь (напр. `apps/services`). Не задана → `services`.

### Флаг авто-ревью Codex — `CODEX_AUTO_REVIEW`

`CODEX_AUTO_REVIEW=true` включает авто-ревью Codex при **открытии** PR (джоб
`codex-review` в `pr.yml`). По умолчанию переменной нет → условие ложно → джоб не бежит.
On-demand `@codex review` в комментарии работает всегда, независимо от флага.

Это **repo/org-уровня variable** (Settings → Secrets and variables → Actions → Variables),
намеренно **не environment**: environment не резолвится в job-level `if:`, и флаг там был
бы не виден. Прочие слои защиты остаются: джоб бежит только для PR из самого репозитория
(`head.repo == github.repository`), форк-PR его не запускают.

## Зачем Codex-ревью и `@codex …`

Ревьюер по умолчанию — **Codex (OpenAI)**, правки по запросу — **`@claude` (Anthropic)**.
Намеренно: авто-ревью работает лучше всего, когда **модели из разных семейств ревьюят
друг друга**. Модель одного семейства повторяет «слепые зоны» автора того же семейства —
те же паттерны рассуждений, те же упускаемые ошибки. Кросс-семейный ревью ловит то, что
однородная пара пропускает.

Роли разведены: Codex проверяет PR (`pr.yml` → авто inline-review), Claude вносит правки
(`@claude fix`) — автор и ревьюер всегда из разных семейств.

`@codex …` (кроме `@codex review`) — диалог с ревьюером прямо в PR: спросить, почему
находка важна, попросить альтернативу, уточнить контекст. Ревью становится разговором,
а не односторонним вердиктом.

## Self-hosted runner для Codex-ревью (развёртывание)

Codex-ревью (`codex-command.yml` — по запросу `@codex …`; авто-проход в `pr.yml` по
умолчанию выкл, см. флаг `CODEX_AUTO_REVIEW`) выполняется на self-hosted runner'е,
авторизованном **подпиской ChatGPT** (не API-ключом). Раннер разворачивается **один раз**
на доверенном сервере, обслуживает несколько репозиториев (регистрация на организацию).

1. Поставить на сервер: `docker`, `gh`, `python3`, Node (для Codex CLI и раннера).
2. Вход Codex подпиской:
   ```bash
   npm i -g @openai/codex
   codex login --device-auth      # открыть ссылку, ввести код, войти ChatGPT-аккаунтом
   ```
   Появится `~/.codex/auth.json` (`"auth_mode": "chatgpt"`). Обращаться как с паролем;
   токен сам рефрешится; один раннер — задачи последовательно (не шарить файл между
   параллельными джобами/машинами).
3. Зарегистрировать GitHub Actions runner с лейблами **`self-hosted,codex`**
   (Settings → Actions → Runners → New self-hosted runner) и запустить как сервис
   (`./svc.sh install && ./svc.sh start`).
4. Убедиться, что доступен `docker login ghcr.io` (приватные образы).

Джобы с `runs-on: [self-hosted, codex]` сами подхватят раннер.
Не нужна подписка — переписать ревью на `openai/codex-action` + `OPENAI_API_KEY`
(биллинг по API).

## Claude runner (`@claude`) — авторизация и настройка

Правки по запросу (`@claude …`, в т.ч. `@claude fix …`) выполняет
**`anthropics/claude-code-action`** в `claude.yml`. В отличие от Codex — на
**GitHub-hosted `ubuntu-latest`**.

В PR Claude коммитит правки **прямо в head-ветку PR**; в issue без ветки — заводит новую
ветку и открывает PR.

Аутентификация

1. **GitHub App «claude» установить на репозиторий** — <https://github.com/apps/claude>
   → Install → выбрать аккаунт/организацию → отметить нужный репо. Через installation-token
   этого App экшен делает все GitHub-операции (читать тред, постить комменты, **пушить
   коммит в ветку PR**); он же держит право `Contents: write`. Настройки потом —
   <https://github.com/settings/installations> → Claude → Configure.
2. **OAuth-токен подписки в секретах репо.** Сгенерировать в своём терминале
   `claude setup-token` (вход в браузере → строка `sk-ant-oat01-…`, печатается только в TTY)
   и положить как **Repository secret** `CLAUDE_CODE_OAUTH_TOKEN` (Settings → Secrets and
   variables → Actions → Secrets).

> Без секрета (п.2) job завершается **зелёным, но молча**: шаг Claude скипается по
> `if: env.CLAUDE_CODE_OAUTH_TOKEN != ''`.
