"""Профиль postgres — конфиг подготовки, НЕ парсер команд.
Режим Б: сервис по admin-DSN создаёт read-only роль и выдаёт агенту её DSN.
"""

ID = "postgres"
DESC = "PostgreSQL — read-only роль"
KIND = "postgres"  # драйвер провизионера (как гонять SQL, как строить DSN)
COMMANDS = ["psql"]
PACKAGES = ["postgresql-client"]
CREDS_ENV = "PG_INSPECTOR_DSN"  # ro-DSN, который получит агент
RO_ROLE = "srvx_readonly"  # фиксированное имя — повторный enable не плодит сирот

# SETUP гоняется под admin-DSN. {role}/{pw}/{db} подставляет провизионер.
# Идемпотентно: роль есть — сменить пароль, нет — создать. pg_read_all_data = read.
SETUP = (
    "DO $$ BEGIN "
    "IF EXISTS (SELECT FROM pg_roles WHERE rolname='{role}') "
    "THEN ALTER ROLE {role} LOGIN PASSWORD '{pw}'; "
    "ELSE CREATE ROLE {role} LOGIN PASSWORD '{pw}'; END IF; END $$; "
    'GRANT CONNECT ON DATABASE "{db}" TO {role}; '
    "GRANT pg_read_all_data TO {role};"
)
# Проба барьера под ro-DSN: должна упасть permission denied (иначе роль не read-only).
VERIFY = "CREATE TABLE _srvx_probe (x int)"
