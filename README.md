# ai-framework-cicd

Универсальный минимальный CI/CD для контейнерных проектов на GitHub Actions.
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
        │                     по запросу: «@codex review» / «@claude fix» в комментах
        │ зелёный CI + аппрув человека
        ▼
merge в main                  собрать ТОЛЬКО изменённые образы → деплой на dev
        │
        ▼
release tag v*                собрать ВСЕ образы → деплой на prod
```

Схема на Miro: <https://miro.com/app/board/uXjVHHc75W4=/?moveToWidget=3458764675987159903>

## Что в репозитории

| Путь | Назначение |
|---|---|
| `.github/workflows/feature.yml` | Feature CI на push в `feature/**` |
| `.github/workflows/pr.yml` | PR CI: тесты + авто Codex review |
| `.github/workflows/codex-command.yml` | повторное Codex review по `@codex review` |
| `.github/workflows/claude.yml` | `@claude` — правки по запросу |
| `.github/workflows/push-main.yml` | merge в main → build изменённых → deploy dev |
| `.github/workflows/release.yml` | tag `v*` → build всех → deploy prod |
| `.github/workflows/manual.yml` | ручной build+deploy (`workflow_dispatch`) |
| `.github/scripts/codex_review.py` | Codex-ревьюер (JSON-находки → inline-review) |
| `scripts/services.sh` | динамическое обнаружение сервисов (`services/*`) |
| `scripts/deploy.sh` | деплой по SSH (login GHCR + docker compose) |
| `services/*/` | сервисы (пример: `api`, `worker`) — заменяются своими |
| `docker-compose.yml` | как поднимать сервисы на стенде |
| `pyproject.toml` | конфиг ruff + pytest (маркер `heavy`) |
| `AGENTS.md` | правила для кодового агента при разработке |
| `docs/PIPELINE.md` | подробный технический справочник |

## Быстрый старт (разработчик)

1. Ветка от `main`: `git checkout -b feature/<что-делаешь>`.
2. Перед коммитом — `pre-commit` (ruff, тесты, секреты). Падает шаг → не коммитим.
3. `git push` → Feature CI даёт быстрый сигнал.
4. Открой PR в `main` → прогоняются тесты и автоматический Codex review.
5. Поправь находки (можно `@codex review` для свежего ревью, `@claude fix ...` —
   чтобы Claude внёс правки сам). Зелёный CI + аппрув → merge.
6. Merge → автодеплой на **dev**. Релиз: тег `vX.Y.Z` → деплой на **prod**.

## Конфиг и секреты

Хранятся в **GitHub → Settings → Environments** (`dev`, `prod`, …), резолвятся
по окружению автоматически:

- **Variables** — несекретный конфиг: `SSH_HOST`, `SSH_USER` (видно, какой сервер
  к какому стенду), вся `.env` приложения — в одной переменной `APP_DOTENV`.
- **Secrets** — чувствительное: `SSH_KEY` (приватный ключ), прочие ключи/токены.

Деплой собирает `app.env` из этих значений и прокидывает его в контейнеры
(`env_file`). Подробности — в `docs/PIPELINE.md`.

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

См. скилл [`skills/setup-cicd/SKILL.md`](skills/setup-cicd/SKILL.md) — пошаговая
процедура для агента (какие файлы скопировать, что настроить в GitHub и на сервере).
