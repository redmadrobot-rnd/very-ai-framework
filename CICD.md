# CI/CD — универсальный пайплайн

Универсальный CI/CD для контейнерных проектов на GitHub Actions.
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
PR в main                     PR CI: unit + integration tests → авто Codex review (inline)
        │                     по запросу: «@codex review» / «@codex answer …» / «@claude fix»
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
| `.github/workflows/pr.yml` | PR CI: тесты + авто Codex review |
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

Codex-ревью (`pr.yml` → `codex-review`, `codex-command.yml`) выполняется на
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

## Развернуть такой же CICD в другом проекте

Процедура — в скилле [`skills/setup-framework/SKILL.md`](skills/setup-framework/SKILL.md):
агент изучает целевой репо, переносит файлы из шаблона по URL, настраивает GitHub и
сервер. SKILL.md самодостаточен — его можно просто дать агенту как инструкцию.

Как сделать скилл вызываемым (`/setup-framework`) — см. [README.md](README.md#установка-скиллов).
