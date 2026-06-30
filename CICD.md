# CI/CD — базовый пайплайн

Базовый CI/CD для контейнерных проектов на GitHub Actions.
Подходит любому репозиторию, где сервисы лежат в `services/<имя>/` (каждый со своим
`Dockerfile`) и собираются через `docker-compose.yml`. Имена сервисов, порты и
конфиг нигде не захардкожены — пайплайн обнаруживает сервисы сам.

Модель ветвления: **GitHub Flow + release-теги**.

## Как это работает (одним взглядом)

```
pre-commit (локально)         ruff + тесты + скан секретов; падение → не коммитим
        │ commit + push
        ▼
push в feature/*              Feature CI: static ‖ security ‖ light tests (быстро, без сборки)
        │ открыть PR
        ▼
PR в main                     PR CI: unit + integration tests (GitHub-hosted)
        │                     по запросу: «@codex review» / «@codex answer …» / «@claude fix»
        │                     (авто-ревью Codex при открытии PR — по умолчанию выкл; вкл variable CODEX_AUTO_REVIEW=true)
        │ зелёный CI + аппрув человека
        ▼
merge в main                  собрать ТОЛЬКО изменённые образы → деплой на dev
        │
        ▼
release tag v*                собрать ВСЕ образы → деплой на prod
```

Схема на Miro: <https://miro.com/app/board/uXjVHHc75W4=/?moveToWidget=3458764675987159903>

## Что в репозитории (CI/CD-часть)

| Путь | Назначение |
|---|---|
| `.github/workflows/feature.yml` | Feature CI на push в `feature/**` |
| `.github/workflows/pr.yml` | PR CI: unit + integration тесты; авто-ревью Codex — по умолчанию выкл (variable `CODEX_AUTO_REVIEW`) |
| `.github/workflows/codex-command.yml` | Codex-команды в PR: `@codex review` / `@codex answer …` |
| `.github/workflows/claude.yml` | `@claude` — правки по запросу |
| `.github/workflows/push-main.yml` | merge в main → build изменённых → deploy dev |
| `.github/workflows/release.yml` | tag `v*` → build всех → deploy prod |
| `.github/workflows/manual.yml` | ручной build+deploy (`workflow_dispatch`) |
| `.github/scripts/codex_review.py` | Codex-ревьюер (JSON-находки → inline-review) |
| `.github/scripts/services.sh` | динамическое обнаружение сервисов (`services/*`) |
| `.github/scripts/deploy.sh` | деплой по SSH (login GHCR + docker compose) |
| `services/<имя>/` | твои сервисы (каждый — со своим `Dockerfile`); в шаблоне их нет |
| `docker-compose.example.yml` | образец compose — скопируй в `docker-compose.yml` под свои сервисы |
| `pyproject.toml` | конфиг ruff + pytest (маркер `heavy`) |
| `skills/setup-framework/` | скилл: развернуть этот CICD в другом репозитории |

## Конфиг и секреты

Хранятся в **GitHub → Settings → Environments** (`dev`, `prod`, …), резолвятся
по окружению автоматически:

- **Variables** — несекретный конфиг: `SSH_HOST`, `SSH_USER` (видно, какой сервер
  к какому стенду), вся `.env` приложения — в одной переменной `APP_DOTENV`.
- **Secrets** — чувствительное: `SSH_KEY` (приватный ключ), прочие ключи/токены.

Деплой собирает `.env` из этих значений и прокидывает его в контейнеры через `env_file`.

### Флаг авто-ревью Codex — `CODEX_AUTO_REVIEW`

`CODEX_AUTO_REVIEW=true` включает авто-ревью Codex при **открытии** PR (джоб
`codex-review` в `pr.yml`). По умолчанию переменной нет → условие ложно → джоб не бежит.
On-demand `@codex review` в комментарии работает всегда, независимо от флага.

Это **repo/org-уровня variable** (Settings → Secrets and variables → Actions → Variables),
намеренно **не environment**: environment не резолвится в job-level `if:`, и флаг там был
бы не виден. Прочие слои защиты остаются: джоб бежит только для PR из самого репозитория
(`head.repo == github.repository`), форк-PR его не запускают.

## Зачем Codex-ревью и `@codex answer`

Ревьюер по умолчанию — **Codex (OpenAI)**, а правки по запросу делает **`@claude`
(Anthropic)**. Это не случайность: автоматический код-ревью качественнее всего работает,
когда **модели из разных семейств ревьюят друг друга**. Модель одного семейства склонна
повторять «слепые зоны» автора-модели того же семейства — одинаковые паттерны рассуждений,
одинаковые упускаемые ошибки. Кросс-семейный ревью (один вендор пишет — другой проверяет)
ловит то, что однородная пара пропускает: чужая модель смотрит на код под другим углом,
и её замечания дополняют, а не дублируют.

Поэтому в пайплайне роли разведены: Codex проверяет PR (`pr.yml` → авто inline-review),
а Claude вносит исправления (`@claude fix`). Так автор и ревьюер всегда из разных семейств.

`@codex answer …` — это диалог с ревьюером прямо в PR: можно спросить, почему находка
важна, попросить альтернативу или уточнить контекст, не уходя из обсуждения. Ревью
перестаёт быть односторонним вердиктом и становится разговором, где замечание можно
оспорить или углубить.

## Self-hosted runner для Codex-ревью (развёртывание)

Codex-ревью (`codex-command.yml` — по запросу `@codex …`; авто-проход
в `pr.yml` по умолчанию выкл, см. флаг `CODEX_AUTO_REVIEW`) выполняется на
self-hosted runner'е, авторизованном **подпиской ChatGPT** (а не API-ключом).
Раннер разворачивается **один раз** на доверенном сервере и может обслуживать
несколько репозиториев (через регистрацию на организацию).

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

После этого джобы с `runs-on: [self-hosted, codex]` сами подхватят раннер.
Если подписка не нужна — можно переписать ревью на `openai/codex-action` +
`OPENAI_API_KEY` (биллинг по API).

## Claude runner (`@claude`) — авторизация и настройка

Правки по запросу (`@claude …`, в т.ч. `@claude fix …`) выполняет
**`anthropics/claude-code-action`** в `claude.yml`. В отличие от Codex он крутится на
**GitHub-hosted `ubuntu-latest`**.

В PR Claude коммитит правки **прямо в head-ветку этого PR**; в обычном issue (ветки нет) —
заводит новую ветку и открывает PR.

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

