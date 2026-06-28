# План правок CI/CD (по ревью Codex + ручной разбор)

Источник: ревью Codex (gpt-5.5) CI/CD-слоя + ручной разбор. Группировка по приоритету.
Ветка: `feature/cicd-hardening`. Каждый пункт — отдельный логический коммит, в конце общий прогон `pre-commit run -a` и CI.

---

## P0 — supply-chain и целостность прода (делаем первыми)

### 1. Прод-деплой только с тега, указывающего на `main` (Codex #1)
**Файл:** `.github/workflows/release.yml`
**Проблема:** `deploy-prod` срабатывает на любой `v*`-тег, даже если он поставлен на произвольный коммит мимо `main`.
**Правка:**
- Новая job `verify-tag` (перед `build`/`deploy-prod`), `fetch-depth: 0`:
  ```bash
  git fetch origin main
  git merge-base --is-ancestor "$GITHUB_SHA" origin/main \
    || { echo "::error::tag $GITHUB_REF_NAME не на main"; exit 1; }
  ```
- `build` и `deploy-prod` получают `needs: [verify-tag, ...]`.
**Repo settings (вручную, не код):** включить protected tags / ruleset на `v*` + требовать signed tags. Зафиксировать в плане как чек-лист для владельца репо.
**Объём:** +1 job, 2 строки `needs`.

### 2. SHA-пин сторонних action'ов с доступом к секретам (Codex #2, #3)
**Файлы:** `release.yml`, `push-main.yml`, `manual.yml`, `claude.yml`
**Проблема:** `appleboy/scp-action`, `appleboy/ssh-action` (получают `SSH_KEY`), `anthropics/claude-code-action` (`contents: write`), `semgrep/semgrep-action` — на плавающих тегах. Несогласованно с уже запиненным trufflehog.
**Правка:** заменить `@vX` на `@<commit-sha>  # vX.Y.Z` (SHA резолвим через GitHub API на этапе реализации). Action'ы для пина:
- `appleboy/scp-action` (×3)
- `appleboy/ssh-action` (×3)
- `anthropics/claude-code-action` (×1)
- `semgrep/semgrep-action` (×1, в `feature.yml`)
**Объём:** механическая замена строк + комментарий с версией.

---

## P1 — детерминизм и надёжность деплоя

### 3. lowercase GHCR-имени (ручной разбор)
**Файлы:** `push-main.yml`, `release.yml`, `manual.yml`, `deploy.sh`, `docker-compose.example.yml`
**Проблема:** `ghcr.io/${{ github.repository }}` сохраняет регистр; у владельца с заглавными (`MyOrg/Repo`) сборка/пуш в GHCR падает. Для шаблона критично.
**Правка:**
- В workflow: шаг `prep`, выдающий `prefix=ghcr.io/${GITHUB_REPOSITORY,,}` в `$GITHUB_OUTPUT`; использовать его вместо `env.IMAGE_PREFIX`.
- `deploy.sh`: `IMAGE_PREFIX="ghcr.io/${REPO,,}"`, `export IMAGE_PREFIX`.
- `docker-compose.example.yml`: `image: ${IMAGE_PREFIX}/api:${TAG:-latest}` вместо `ghcr.io/${GITHUB_REPOSITORY}/...`.
**Объём:** +1 шаг на workflow, правка строк образов.

### 4. concurrency-lock на деплой окружения (Codex #7)
**Файлы:** `push-main.yml` (`deploy-dev`), `release.yml` (`deploy-prod`), `manual.yml` (`deploy`)
**Правка:** job-level `concurrency`:
```yaml
concurrency:
  group: deploy-dev            # / deploy-prod / deploy-${{ inputs.environment }}
  cancel-in-progress: false    # деплои не отменяем — в очередь
```
**Объём:** 3 блока по 3 строки.

### 5. health-wait вместо «зелёный сразу после up -d» (Codex #8)
**Файлы:** `deploy.sh`, `docker-compose.example.yml`
**Правка:**
- `deploy.sh:34`: `docker compose up -d --wait --wait-timeout 300 $SERVICES` (секунды).
- В пример compose добавить `healthcheck` для `api` (и комментарий, что без healthcheck `--wait` ждёт только старта).
**Объём:** 1 строка + healthcheck-блок в примере.

### 6. Сузить permissions на deploy-джобах (Codex #9)
**Файлы:** `push-main.yml`, `release.yml`, `manual.yml`
**Правка:** на deploy-джобы добавить
```yaml
permissions:
  contents: read
  packages: read
```
(build-джобы оставляем с `packages: write`).
**Объём:** 3 блока.

---

## P2 — защита и устойчивость пайплайна

### 7. environment как whitelist + guard от path traversal (Codex #4)
**Файлы:** `manual.yml`, `deploy.sh`
**Правка:**
- `manual.yml`: input `environment` → `type: choice`, `options: [dev, prod]`.
- `deploy.sh`: до `mkdir/cd` проверка `[[ "$ENVIRONMENT" =~ ^[a-z][a-z0-9_-]*$ ]]` (без хардкода имён — фреймворк-friendly), иначе `exit 1`.
**Объём:** правка input + 2 строки в скрипте.

### 8. ~~security как обязательный gate в PR (Codex #5)~~ — НЕ берём
Решено оставить как есть: security гоняется на каждый коммит в feature-ветке, отдельный
PR-гейт не вводим (избежать двойного прогона). Известный остаток риска (PR из не-`feature/**`
веток и `[skip security]`-bypass) принимаем осознанно.

### 9. deploy при изменении инфраструктуры, а не только `services/` (Codex #6)
**Файлы:** `services.sh` / `push-main.yml`
**Правка:** если в диффе затронуты `docker-compose.yml`, `.github/scripts/deploy.sh` — форсировать «deploy all» (re-pull без rebuild образов).
**Объём:** доработка `services.sh changed` или доп. шаг. *Обсуждаемо — может расширить объём, выношу в опциональные.*

### 10. `run()` не глотает exit code (Codex #11)
**Файлы:** `codex_review.py`, `codex_answer.py`
**Правка:** в `run()` проверять `returncode`/прокидывать stderr; при падении `codex exec` — фейлить job и класть stderr в комментарий (частично уже сделано в `codex_review.py` для timeout, надо для non-zero).
**Объём:** правка хелпера + места вызова.

---

## P3 — мелочи (низкий риск, высокий QoL)
- `fail-fast: false` в build-matrix (`push-main.yml`, `release.yml`) — видеть все упавшие сервисы.
- Docker build-cache `cache-from/to: type=gha` в `build-push-action` (и перевод `manual.yml` на `build-push-action`).
- `timeout-minutes` на джобы (деплой/тесты/codex) — против зависаний (особенно self-hosted codex).

---

## Статус
Всё (P0+P1+P2 без #8, P3, #9) реализовано в ветке `feature/cicd-hardening` одним PR.

## Repo-settings (вручную, не код)
Для полной силы P0 #1 включить в Settings → Rules/Branches: protected tags на `v*`,
обязательно signed tags, required-checks на PR = tests.
