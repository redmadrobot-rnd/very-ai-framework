"""Профиль postgres — конфиг подготовки, НЕ парсер команд.
Провизионер по нему ставит клиент, создаёт read-only роль и проверяет её.
"""

ID = "postgres"
DESC = "PostgreSQL — read-only роль"
COMMANDS = ["psql"]
PACKAGES = ["postgresql-client"]
CREDS_ENV = "PG_INSPECTOR_DSN"

# Рецепт read-only роли (режим авто-создания). :role/:db/:pw подставляет провизионер.
SETUP = (
    "CREATE ROLE :role LOGIN PASSWORD :'pw'; "
    "GRANT CONNECT ON DATABASE :db TO :role; "
    "GRANT pg_read_all_data TO :role;"
)
# Проба барьера: при read-only роли должна упасть permission denied.
VERIFY = "CREATE TABLE _srvx_probe (x int)"
