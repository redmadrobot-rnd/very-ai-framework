# Тестирование и CI

CI находит тесты сам — по `pyproject.toml`. Работает для одного проекта и для
монорепо с сервисами одинаково, без настройки пайплайна.

## Что нужно знать

1. Тестовое окружение = каталог с `pyproject.toml`, где есть `[project]` или
   секция `[tool.pytest.*]`. CI ставит его зависимости и запускает `pytest`.
2. Тесты лежат где угодно **под** этим `pyproject.toml` — pytest найдёт сам.
3. Медленные/дорогие тесты помечать `@pytest.mark.heavy`.

В монорепо: корневой `pyproject.toml` + по одному на каждый `services/<имя>/`.
Окружения тестируются параллельно и изолированно. Имена нигде не зашиты — новый
сервис подхватывается автоматически.

## Когда что гоняется

| событие | окружения | тесты |
|---|---|---|
| PR в `main` | только затронутые изменениями | `-m "not heavy"` |
| push в `main` (после мержа) | все | все, включая `heavy` |

На push в feature-ветку CI не запускается — до PR качество держит локальный pre-commit
(ruff + тесты + секреты). Так нет двойного прогона `push` + `pull_request`.

**Ручной запуск:** Actions → `Main CI` → Run workflow (на любой ветке). Поле `markers` —
любое pytest-выражение **без** `-m`: пусто = все (включая `heavy`), `heavy` — только
тяжёлые, `not heavy` — без них, `smoke or integration` и т.п. Значение кавычится и
передаётся как `-m "<выражение>"`, так что произвольный ввод безопасен.

## Пример `pyproject.toml`

```toml
[project]
name = "auth-service"
requires-python = ">=3.11"
dependencies = ["fastapi"]

[dependency-groups]
dev = ["pytest", "pytest-asyncio"]   # тест-зависимости держим здесь

[tool.pytest.ini_options]
testpaths = ["tests"]
markers = ["heavy: медленные/дорогие (e2e, load)"]
addopts = "--strict-markers"
```

`[project]` нужен, если тестам требуются зависимости проекта. Если их нет (только
конфиг pytest) — `[project]` можно опустить, CI поставит лишь pytest.

## Проверить локально

```bash
bash .github/scripts/discover-envs.sh   # какие окружения видит CI
```
