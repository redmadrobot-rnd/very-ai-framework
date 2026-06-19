---
name: setup-cicd
description: >
  Развернуть универсальный CI/CD из репозитория-шаблона в целевой проект:
  pre-commit, Feature/PR CI, авто Codex review, @claude-фиксы, деплой на dev по merge
  и на prod по тегу. Использовать, когда нужно поставить такой же пайплайн в новый/другой
  репозиторий на GitHub Actions с контейнерными сервисами (services/* + docker-compose).
---

# Setup CI/CD (универсальный пайплайн)

Ты переносишь пайплайн из **репозитория-шаблона** в **целевой репозиторий**:

- **Шаблон (источник файлов):** `https://github.com/virrius/ai-framework-cicd`
  — если на вход дан другой URL, используй его.
- **Целевой репозиторий:** проект, в котором ты сейчас работаешь и куда ставим CI/CD.

Контракт: каждый сервис — каталог `services/<имя>/` со своим `Dockerfile`; имена
сервисов и порты нигде не хардкодятся. Работай по шагам.

## Шаг 0. Что уточнить у пользователя (спроси ДО начала, не предполагай молча)

Сначала **изучи целевой репозиторий** и выведи из него всё очевидное (язык, тулчейн,
структуру сервисов, какие env-переменные читает приложение). Спрашивай **только то,
что из кода не выводится**: адреса/доступы, секретные значения, выбор Codex/@claude.

**Репозитории**
- Целевой репозиторий (URL или подтверждение «текущий»). Приватный? (для self-hosted
  Codex и приватных образов — обычно да).
- URL шаблона, если отличается от дефолтного.

**Стенды и серверы**
- Какие окружения нужны: `dev`, `prod`, `staging`, …?
- Адреса серверов: Для каждого — `SSH_HOST`, `SSH_USER`. 

**Доступы / креды**
- GitHub-токен с правами: repo, Actions, **secrets**, **environments** (нужно создавать
  окружения и секреты).
- Есть ли уже SSH-доступ к серверу и можно ли завести deploy-ключ (положить публичный
  в `authorized_keys`).
- На сервере есть/ставим Docker; доступен ли `docker login ghcr.io` (приватные образы).

**Codex-ревью**
- Нужно ли вообще. Если да — каким способом: **подписка** (есть self-hosted runner или
  аккаунт ChatGPT с Codex для `codex login`?) или **API** (`OPENAI_API_KEY`).

**@claude-фиксы (опционально)**
- Нужны ли. Если да — есть `CLAUDE_CODE_OAUTH_TOKEN` (`claude setup-token`) и установлен
  ли GitHub App «Claude» на репо.

**Конфиг приложения** (выводится из репо — спрашивай только неочевидное)
- Язык/тулчейн и набор env-переменных приложения **определи сам** из кода/конфигов
  целевого репо — не спрашивай.
- Спроси только то, что из репо не достать: **секретные значения** (пароли, ключи,
  токены) и какие переменные **различаются между стендами**.

Чего-то из этого не хватает — **спроси прямо**, не подставляй значения сам.

## Шаг 1. Перенести файлы пайплайна из шаблона

Из корня **целевого** репозитория:

```bash
TEMPLATE=https://github.com/virrius/ai-framework-cicd   # или переданный URL
git clone --depth 1 "$TEMPLATE" /tmp/cicd-template

mkdir -p .github/workflows .github/scripts scripts
cp /tmp/cicd-template/.github/workflows/{feature,pr,codex-command,claude,push-main,release,manual}.yml .github/workflows/
cp /tmp/cicd-template/.github/scripts/codex_review.py .github/scripts/
cp /tmp/cicd-template/scripts/{services.sh,deploy.sh} scripts/
cp /tmp/cicd-template/{.pre-commit-config.yaml,pyproject.toml} .
cp /tmp/cicd-template/docker-compose.yml .   # шаблон — переписать под свои сервисы
```

**Правила разработки** — в шаблоне они в `AGENTS.md`, но целевой репо может
использовать другой файл агент-инструкций. Перенеси правила в тот, что использует
целевой проект, не привязываясь к имени:
- репо уже имеет `AGENTS.md` / `CLAUDE.md` / иной (`.cursorrules`, `.github/copilot-instructions.md`, …) —
  **допиши** туда раздел правил этого CICD (ветки `feature/*`, pre-commit,
  `@pytest.mark.heavy`, секреты в Environments), **не затирая** существующее;
- такого файла нет — создай `AGENTS.md` (`cp /tmp/cicd-template/AGENTS.md .`).

`README.md` шаблона **не** копируй вслепую — у целевого проекта свой README.

## Шаг 2. Привести проект к контракту

- Каждый сервис — каталог `services/<имя>/` с `Dockerfile`.
- `docker-compose.yml` описывает эти сервисы; образы — `ghcr.io/<owner/repo>/<svc>`
  (префикс `ghcr.io/${GITHUB_REPOSITORY}` в compose должен совпадать с тем, что собирает CI).
- Тесты: `tests/unit` (быстрые), тяжёлые — пометить `@pytest.mark.heavy`.
- Если проект не на Python — заменить в `feature.yml`/`pr.yml` шаги ruff/pytest/pip-audit
  на тулинг проекта (структура джоб остаётся).
- Включить pre-commit:
  ```bash
  pip install pre-commit && pre-commit install
  ```
  Для local-хука `secret-scan` поставить `trufflehog`. Либо без хука — гонять
  `pre-commit run` вручную перед коммитом (агентный вариант).

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
   Разные стенды должны иметь разные ключи (изоляция)
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
