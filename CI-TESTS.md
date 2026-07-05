# CI-TESTS — что тестировать в самом пайплайне

Спека поведения CI/CD-скриптов. Черновик самотестов пайплайна — пока не реализовано, план.

## Слои (по убыванию ROI)

| Слой | Чем | Покрывает |
|---|---|---|
| Bash-юниты | `bats-core` + временный git-репо как фикстура | `services.sh`, `discover-test-dirs.sh`, `changed-test-dirs.sh`, чистые куски `deploy.sh` |
| Python-юниты | `pytest` | парсинг вопроса в `codex_answer.py` |
| Статика | `actionlint` (+ shellcheck внутри `run:`) | синтаксис YAML, ошибки в `${{ }}`, баги в bash-шагах |
| Инварианты | grep-ассерты | напр. answer содержит `!contains('@codex review')` |
| Логика джобов | `act` | план build/deploy на синтетическом push (низкий ROI на Windows) |
| Гонки/деплой | только интеграция/ревью | race dev↔release, ssh, concurrency, digest — локально не воспроизвести |

## changed-test-dirs.sh

| Кейс | Ожидание |
|---|---|
| файл `services/auth/x.py` | `["services/auth"]` |
| корневой файл `main.py`, `.` есть в discover | `["."]` |
| корневой файл, `.` нет | `[]` (нет run-all fallback) |
| `docs/adr.md`, `README.md`, `docs/x.txt` | пропущены → `[]` |
| правка только `auth` | `bill` отсутствует |
| longest-prefix владелец при вложенных окружениях | глубочайший |
| два файла в одном сервисе | одна запись (дедуп) |
| `default` = несуществующая ветка (base не вычислить) | вернуть весь вход |
| `services/auth/NOTES.md` | `*.md` матчит на любой глубине → тесты сервиса НЕ бегут (зафиксировать решение) |
| `services/foo/x.py`, у `foo` нет тест-каталога | владельца нет → падает на корень `.` |

## services.sh

| Кейс | Ожидание |
|---|---|
| `list` | все `services/*/`; пусто → `[]` |
| `changed base head` — файл в сервисе | `[svc]`; незатронутые вне |
| `changed` — правка `docker-compose.yml`/`deploy.sh` | НЕ попадает (спец-кейс убран) |
| `changed` — `base` отсутствует (первый пуш, нули) | fallback `head~1`/пустое дерево, не падает |
| `select all` | все |
| `select "a,b"` | пересечение с реальными |
| `select "a;rm -rf /"` (инъекция из dispatch) | мусор отсеян → `[]` |
| `select` с дублями/пробелами | нормализовано |

## discover-test-dirs.sh

| Кейс | Ожидание |
|---|---|
| корень с `[project]` / с `[tool.pytest.*]` | включён `.` |
| корневой pyproject без обоих | `.` исключён |
| сервис без квалиф. pyproject | исключён |
| вложенный pyproject | берётся ближайший |
| pyproject внутри `.venv`/`node_modules` | игнор (prune) |
| пустой репо | `[]` |

## codex_answer.py

| Кейс | Ожидание |
|---|---|
| `@codex почему X` | вопрос = `почему X` |
| `@codex` (пусто) | fallback на «последний коммент» |
| `@codex` дважды в тексте | срезан только первый |

## deploy.sh (с фейковым docker в PATH)

| Кейс | Ожидание |
|---|---|
| `env` ∉ {dev,prod} | exit 1 |
| кривой project | exit 1 |
| валидный вход | верный `COMPOSE_PROJECT_NAME`, путь `/srv/deploy/<project>/<env>` |

## preflight-гарды (dev + prod)

| Кейс | Ожидание |
|---|---|
| пустой `SSH_HOST`/`USER`/`KEY` | exit 1 с внятным текстом |
| всё задано | проходит |

## Не тестируем юнитами (нужна интеграция/рантайм)

- Гонка dev↔release, digest-пин — реальный реестр + тайминг (максимум `registry:2` интеграцией).
- SSH-деплой, concurrency-группы, required reviewers — рантайм GitHub/сервера.

## Предлагаемая структура

```
.github/tests/
  bats/
    services.bats
    discover-test-dirs.bats
    changed-test-dirs.bats
    deploy.bats            # фейковый docker в PATH
    helpers.bash           # make_git_repo, mk_service, assert_json
  python/
    test_codex_answer.py
  run.sh                   # bats + pytest + actionlint
```
Плюс джоб `ci-selftest` (или шаг в `_checks.yml`), гоняющий `run.sh` — CI тестирует сам себя.
