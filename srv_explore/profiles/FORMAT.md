# Формат профиля srv-explore

Профиль = **тонкий конфиг** подготовки инструмента (`profiles/<id>.py`). Он **не парсит
команды** — read-only держит ресурс-слой (read-only роль СУБД, docker-socket-proxy,
egress-firewall, RO-FS; см. `../DESIGN.md`). По конфигу провизионер ставит клиент,
создаёт read-only доступ и проверяет барьер.

Новый инструмент = новый файл. По умолчанию профиль **выключен**, включается в админке
(тумблер + «Установить»).

## Контракт

```python
ID = "postgres"                    # уникальный id, ключ тумблера
DESC = "PostgreSQL — read-only роль"
COMMANDS = ["psql"]                # что даёт инструмент (информативно)
PACKAGES = ["postgresql-client"]   # apt-пакеты клиента
CREDS_ENV = "PG_INSPECTOR_DSN"     # Secret с read-only коннекшеном для агента
SETUP = "CREATE ROLE :role ... GRANT pg_read_all_data ..."  # рецепт (или None)
VERIFY = "CREATE TABLE _srvx_probe (x int)"                  # проба барьера (или None)
```

`:role` / `:db` / `:pw` в `SETUP` подставляет провизионер (рандомный пароль). `docker`
вместо роли декларирует `PROXY` (socket-proxy).

## Что НЕ входит

Никаких `check()`, forbid-списков, allowlist. Регулировать команды не нужно — барьер на
ресурсе. Гард (`../guard.py`) отдельно и профили не грузит: он только гигиена
(метасимволы `>`/`;`/`$()`, спецфайлы `/dev/*`).

## Пример

`profile.py.example` — шаблон. Готовые: `postgres`/`mongo`/`redis`/`rabbitmq`/`docker`.
