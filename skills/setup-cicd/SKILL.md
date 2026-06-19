---
name: setup-cicd
description: >
  Развернуть универсальный CI/CD (этот репозиторий как шаблон) в целевом проекте:
  pre-commit, Feature/PR CI, авто Codex review, @claude-фиксы, деплой на dev по merge
  и на prod по тегу. Использовать, когда нужно поставить такой же пайплайн в новый/другой
  репозиторий на GitHub Actions с контейнерными сервисами (services/* + docker-compose).
---

# Setup CI/CD (универсальный пайплайн) в целевом репозитории

Цель — воспроизвести пайплайн из этого репозитория-шаблона. Имена сервисов/порты не
хардкодятся; контракт — каждый сервис это каталог `services/<имя>/` со своим `Dockerfile`.

Работай по шагам, не пропуская проверку в конце.

## Предусловия (уточни у пользователя, если неизвестно)

- Целевой репозиторий на GitHub (приватный — для self-hosted Codex это обязательно).
- Сервер(ы) для стендов с Docker и доступом по SSH (можно один, эмулирующий dev/prod).
- Доступ: токен GitHub с правами на репо/секреты/environments; SSH-доступ к серверу.
- Для Codex-ревью по подписке — аккаунт ChatGPT с Codex (для `codex login` на раннере).

## Шаг 1. Скопировать файлы пайплайна

Из репозитория-шаблона перенести как есть:

- `.github/workflows/`: `feature.yml`, `pr.yml`, `codex-command.yml`, `claude.yml`,
  `push-main.yml`, `release.yml`, `manual.yml`
- `.github/scripts/codex_review.py`
- `scripts/services.sh`, `scripts/deploy.sh`
- `.pre-commit-config.yaml`, `pyproject.toml` (конфиг ruff + pytest-маркер `heavy`)
- `docker-compose.yml` (как шаблон — переписать под сервисы проекта)
- `AGENTS.md`, `README.md` (адаптировать)

Не переноси папку `services/*` шаблона — это пример; у проекта свои сервисы.

## Шаг 2. Привести проект к контракту

- Каждый сервис — каталог `services/<имя>/` с `Dockerfile`.
- `docker-compose.yml` описывает эти сервисы; образы — `ghcr.io/<owner/repo>/<svc>`
  (префикс `ghcr.io/${GITHUB_REPOSITORY}` в compose должен совпадать с тем, что собирает CI).
- Тесты: `tests/unit` (быстрые), тяжёлые — пометить `@pytest.mark.heavy`.
- Если проект не на Python — заменить в `feature.yml`/`pr.yml` шаги ruff/pytest/pip-audit
  на тулинг проекта (структура джоб остаётся).

## Шаг 3. Настроить GitHub

1. **Environments** (Settings → Environments): создать `dev` и `prod` (и др. при нужде).
2. **Secrets на каждый environment:** `SSH_HOST`, `SSH_USER`, `SSH_KEY` (приватный
   deploy-ключ; публичный — в `~/.ssh/authorized_keys` сервера).
3. **Конфиг приложения:** Variable `APP_DOTENV` (многострочный `.env`) на каждый
   environment; секретные значения — отдельными Environment Secrets (напр. `APP_SECRET`).
4. **(опц.) @claude:** Secret `CLAUDE_CODE_OAUTH_TOKEN` (`claude setup-token`) +
   установить GitHub App «Claude» на репо.
5. Включить GitHub Actions; убедиться, что `GITHUB_TOKEN` имеет `packages: write` (для GHCR).

## Шаг 4. Self-hosted runner для Codex-ревью (по подписке)

1. На доверенном сервере: `codex login --device-auth` → `~/.codex/auth.json`
   (`"auth_mode": "chatgpt"`). Обращаться с ним как с паролем; один runner — последовательно.
2. Зарегистрировать runner на репозиторий с лейблами `self-hosted,codex`
   (Settings → Actions → Runners → New self-hosted runner), запустить как сервис.
3. На сервере должны быть: `docker`, `gh`, `python3`, доступ `docker login ghcr.io`.

> Без этого Codex-ревью (`pr.yml` job `codex-review`, `codex-command.yml`) не на чем
> выполняться. Альтернатива — переписать на `openai/codex-action` + `OPENAI_API_KEY`
> (API-биллинг вместо подписки).

## Шаг 5. Сервер(ы) для деплоя

- Установить Docker; убедиться, что `docker compose` доступен.
- Публичный deploy-ключ в `authorized_keys`.
- Каталоги создаются автоматически (`/srv/deploy/<env>` в `deploy.sh`).
- Для приватных образов GHCR деплой логинится `GHCR_USER`/`GHCR_TOKEN` (= `GITHUB_TOKEN`).

## Шаг 6. Проверка (прогнать сценарии по очереди)

1. pre-commit локально на тестовом изменении.
2. push в `feature/test` → Feature CI зелёный.
3. PR в `main` → тесты + авто Codex review.
4. `@codex review` и `@claude fix` в комментах PR.
5. merge → деплой dev (проверить контейнеры/эндпоинт).
6. тег `v0.0.1` → деплой prod (проверить).
7. Manual `workflow_dispatch` → build + deploy на выбранный стенд.

## Частые грабли

- **ruff не запинен** → CI и локаль форматируют по-разному. Держи одну версию в
  `pyproject.toml` / `.pre-commit-config.yaml` / CI.
- **Имена образов** build ≠ compose → деплой не находит образ. Префикс один:
  `ghcr.io/<owner/repo>/<svc>`.
- **Один сервер на dev+prod** → конфликт портов (универсальный compose не разводит порты).
  В реале — разные хосты; на одном сервере стенды не должны делить порт.
- **`GITHUB_TOKEN` не читает Environments API** — не делай через него валидацию env.
