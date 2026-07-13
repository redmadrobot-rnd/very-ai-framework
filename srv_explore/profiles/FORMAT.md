# Формат профиля srv-explore

Профиль = Python-модуль, описывающий одно семейство команд и то, что в нём считается
**только чтением**. Гард импортит `*.py` из каталогов профилей один раз при старте и
строит реестр: новый профиль = новый файл.

Ядро **облегчённое**: по умолчанию активен один профиль — `local.py` (базовая
read-оболочка поверх read-only FS). Остальное (`docker`, `postgres`, `mongo`, `redis`,
`rabbitmq`, `http`) лежит в `profiles/available/` и **не грузится**, пока не включишь:

- скопировать модуль из `available/` в `profiles/`, **или**
- добавить `available/` в `SRV_EXPLORE_PROFILES_DIR` (через `os.pathsep`).

## Контракт (каждый модуль)

```python
ID = "redis"                       # уникальный id, ключ тумблера в админке
COMMANDS = ["redis-cli"]           # бинарники профиля
DESC = "redis-cli (read-глаголы)"  # одна строка для админки
def check(argv, g) -> (ok, reason) # решение по команде
```

- Команду не забрал ни один профиль → fallback `local.py` (`FALLBACK = True`).
- Профиль **выключен или отсутствует** → все его `COMMANDS` → **deny**.
- Метасимволы (`>`/`;`/`&`/`$()`), пайпы, чтение `/dev/*` — инварианты ядра, до профилей.

## Примитивы `g` (class Toolkit, guard.py)

Ядро **не знает** про docker/SQL/curl — только универсальные кубики; домен собирает из них профиль.

| Примитив | Для чего |
|----------|----------|
| `g.verbs(argv, allow, subreads, value_flags, deny_flags, allow_flags, require_flag, allow_bare, no_follow)` | verb+flag движок; покрывает большинство read-CLI. |
| `g.subcommand(argv, value_flags)` | Первый позиционный токен + хвост. |
| `g.values(argv, flags, file_flags)` | Значения флагов (`-c`/`--eval`); `file_flag` → ошибка. |
| `g.forbid_words(text, words)` / `g.forbid_substr(text, subs)` | Поиск запрещённого по слову / подстроке. |
| `g.follows(argv)` | `-f`/`--follow` (стриминг). |
| `g.url_host(url)` / `g.internal_host(host)` | Хост из URL / внутренний ли он. |
| `g.recurse(argv)` | Прогнать вложенную команду через весь гард (`docker exec`). |
| `g.name(argv)` | Имя бинарника. |

Простое read-CLI — одна строка на `g.verbs`. Сложное (SQL/mongo/curl/docker) — своя
логика в `check` на этих примитивах; см. `available/*.py`.

## Пример

`profile.py.example` — рабочий шаблон. Скопируй в `<id>.py`, поправь метаданные и правила.

## Что даёт read-only гарантию по-настоящему

Гард — defense-in-depth. Фундамент — на стороне ресурса: read-only роль СУБД
(рецепт — в docstring профиля), read-only FS ядром (`ProtectSystem=strict`),
закрытый/контролируемый egress.
