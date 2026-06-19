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
2. **На каждый environment:**
   - Variables: `SSH_HOST`, `SSH_USER` (адрес/юзер — не секрет; видно, какой сервер к стенду);
   - Secret: `SSH_KEY` — приватный deploy-ключ.

   **Сгенерировать deploy-ключ** (отдельная пара под деплой, НЕ переиспользуй личный):
   ```bash
   ssh-keygen -t ed25519 -f deploy_key -N "" -C "gh-deploy"   # без пароля — CI unattended
   # публичный — на сервер (под пользователя SSH_USER):
   ssh-copy-id -i deploy_key.pub <SSH_USER>@<SSH_HOST>
   #   (или вручную добавить строку из deploy_key.pub в ~/.ssh/authorized_keys)
   # приватный — содержимое файла deploy_key целиком — в Secret SSH_KEY
   ```
   Локальные файлы `deploy_key*` после этого можно удалить: приватный живёт в Secret
   (он **write-only**, обратно не читается — если понадобится снова, перегенерируй пару).
   Разные стенды могут иметь разные ключи (изоляция) или один общий — на твой выбор.
3. **Конфиг приложения:** Variable `APP_DOTENV` (многострочный `.env`) на каждый
   environment; секретные значения — отдельными Environment Secrets (напр. `APP_SECRET`).
4. **(опц.) @claude:** Secret `CLAUDE_CODE_OAUTH_TOKEN` (`claude setup-token`) +
   установить GitHub App «Claude» на репо.
5. Включить GitHub Actions; убедиться, что `GITHUB_TOKEN` имеет `packages: write` (для GHCR).

## Шаг 4. Codex-ревью: подключить к runner'у

Codex-ревью (`pr.yml` → `codex-review`, `codex-command.yml`) выполняется на
self-hosted runner'е с лейблами `self-hosted,codex` (вход — подпиской ChatGPT).

- **Если такой runner уже развёрнут** (общий / в другой репозиторий организации) —
  просто сделай его доступным этому репо: зарегистрируй на репозиторий или на
  организацию с доступом к нему. Воркфлоу уже targeted на `runs-on: [self-hosted, codex]` —
  больше ничего не нужно.
- **Если готового runner'а нет** — разверни один раз по инструкции в
  [`README.md`](../../README.md) → «Self-hosted runner для Codex-ревью (развёртывание)».
- **Не нужна подписка** — альтернатива: переписать ревью на `openai/codex-action` +
  `OPENAI_API_KEY` (биллинг по API), тогда self-hosted runner не требуется.

## Шаг 5. Сервер(ы) для деплоя

- Установить Docker; убедиться, что `docker compose` доступен.
- Публичный deploy-ключ в `authorized_keys`.
- Каталоги создаются автоматически (`/srv/deploy/<env>` в `deploy.sh`).
- Для приватных образов GHCR деплой логинится `GHCR_USER`/`GHCR_TOKEN` (= `GITHUB_TOKEN`).
