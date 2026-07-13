# Формат профиля srv-explore

Профиль = Python-модуль `profiles/<id>.py`, описывающий одно семейство команд и то,
что в нём считается **только чтением**. Гард импортит `profiles/*.py` один раз при
старте и строит реестр: новый профиль = новый файл.

Профиль ≠ «плагин» — в сервисе нет плагинов, только профили.

## Контракт (каждый модуль)

```python
ID = "redis"                       # уникальный id, ключ тумблера в админке
COMMANDS = ["redis-cli"]           # бинарники, которыми владеет профиль
DESC = "redis-cli (read-глаголы)"  # одна строка для админки
def check(argv, g) -> (ok, reason) # решение по конкретной команде
```

- Команду не забрал ни один профиль → падает в fallback-профиль (`local.py`,
  `FALLBACK = True`) — базовая read-оболочка.
- Профиль **выключен в админке или отсутствует** → все его `COMMANDS` → **deny**.
- Метасимволы (`>`/`;`/`&`/`$()`), пайпы, чтение `/dev/*` — инварианты ядра, до профилей.

## Тулкит `g` (хелперы ядра, class Toolkit в guard.py)

| Хелпер | Для чего |
|--------|----------|
| `g.verbs(argv, allow, subreads, value_flags, deny_flags, allow_flags, require_flag, allow_bare, no_follow)` | verb+flag движок. Покрывает большинство read-CLI (redis, rabbitmq, systemctl, серверные бинари). |
| `g.sql(argv, cmd_flags, file_flags, allow_prefixes, forbid)` | Один SQL-стейтмент из `-c`; без EXPLAIN ANALYZE/мутаций; файл-инструкция deny. |
| `g.mongo(argv, eval_flags, file_flags, forbid)` | `mongosh --eval`; запрещённые методы/стадии подстрокой; `--file` deny. |
| `g.curl(argv, internal_only, external_allow)` | Только GET/HEAD-флаги; назначение — внутренняя сеть + allowlist. |
| `g.docker(argv, reads, noun_reads, compose_reads)` | verb + noun-формы + `exec` (рекурсия) + `compose`; стриминг deny. |
| `g.read_util(argv, read_commands)` | Базовые read-утилиты + write-флаг-стражи (`sort -o`, `find -exec`, `tail -f`…). |
| `g.recurse(argv)` | Прогнать вложенную команду через весь гард (для `docker exec`). |
| `g.name(argv)`, `g.subcommand(argv, value_flags)` | Мелкие примитивы. |

`g.verbs` покрывает почти всё, что добавит юзер. Спец-хелперы (`sql`/`mongo`/`curl`/
`docker`) — там, где read-only нельзя выразить verb+flag правилами. Нужно совсем своё —
пиши логику в `check` на голом Python.

## Пример

`profile.py.example` — рабочий шаблон (простой случай на `g.verbs` + комментарий про
сложный). Скопируй в `<id>.py`, поправь метаданные и правила.

## Что даёт read-only гарантию по-настоящему

Гард — defense-in-depth. Фундамент — на стороне ресурса: read-only роль СУБД
(`postgres`/`mongo`/`redis`/`rabbitmq` профили в docstring несут рецепт роли),
read-only FS ядром (`ProtectSystem=strict`), закрытый/контролируемый egress.
