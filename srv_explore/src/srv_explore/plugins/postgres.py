"""Плагин postgres: из одноразового admin-DSN заводит read-only роль для агента."""

from srv_explore.plugin_api import (
    dsn_db,
    dsn_host,
    dsn_user,
    dsn_with_creds,
    field,
    step,
    tcp_open,
)

ID = "postgres"
DESC = "PostgreSQL — read-only роль"
PACKAGES = ["postgresql-client"]
FIELDS = [
    field(
        "admin_dsn",
        "Админский DSN",
        "postgresql://admin:пароль@host:5432/база",
        secret=True,
        hint="одноразово: из него создаётся read-only роль, сам DSN не сохраняется",
    )
]
CREDS_ENV = "PG_INSPECTOR_DSN"
RO_ROLE = "srvx_readonly"  # фиксированное имя — повторный Install не плодит юзеров

# Идемпотентно = сброс до нуля, не «сменить пароль». Роль могла жить раньше с
# write-доступом, и его надо снять ДВУМЯ путями:
#   - DROP OWNED — прямые гранты (ручной GRANT INSERT на таблицу) в этой БД;
#   - цикл REVOKE — членства в ролях (pg_write_all_data или прикладная writer-роль):
#     их DROP OWNED НЕ трогает, а унаследованный через них DML пережил бы ротацию
#     пароля, и проба на CREATE TABLE могла бы его не поймать.
# Только после сброса выдаём единственное право — pg_read_all_data (чтение).
SETUP = (
    "DO $$ BEGIN "
    "IF EXISTS (SELECT FROM pg_roles WHERE rolname='{role}') "
    "THEN ALTER ROLE {role} LOGIN PASSWORD '{pw}'; "
    "ELSE CREATE ROLE {role} LOGIN PASSWORD '{pw}'; END IF; END $$; "
    "DO $$ DECLARE r record; BEGIN "
    "FOR r IN SELECT roleid::regrole AS rn FROM pg_auth_members "
    "WHERE member = '{role}'::regrole LOOP "
    "EXECUTE format('REVOKE %s FROM {role}', r.rn); END LOOP; END $$; "
    "DROP OWNED BY {role}; "
    'GRANT CONNECT ON DATABASE "{db}" TO {role}; '
    "GRANT pg_read_all_data TO {role};"
)
PROBE_WRITE = "DROP TABLE IF EXISTS _srvx_probe; CREATE TABLE _srvx_probe (x int)"


def _env(dsn: str) -> dict:
    """Креды — через PG*-переменные, не через argv (пароль не светится в ps)."""
    host, port = dsn_host(dsn)
    user, pw = dsn_user(dsn)
    env = {"PGCONNECT_TIMEOUT": "10"}
    if host:
        env["PGHOST"] = host
    if port:
        env["PGPORT"] = str(port)
    if user:
        env["PGUSER"] = user
    if pw:
        env["PGPASSWORD"] = pw
    db = dsn_db(dsn)
    if db:
        env["PGDATABASE"] = db
    return env


def _psql(ctx, dsn: str, sql: str):
    return ctx.sh(["psql", "-v", "ON_ERROR_STOP=1", "-tAc", sql], env=_env(dsn))


def install(ctx):
    admin_dsn = ctx.field("admin_dsn")

    ok, detail = ctx.apt(PACKAGES)
    yield step("клиент psql", ok and ctx.which("psql"), detail)

    host, port = dsn_host(admin_dsn)
    yield step("БД обнаружена", tcp_open(host, port or 5432), f"{host}:{port or 5432}")

    rc, _, err = _psql(ctx, admin_dsn, "SELECT 1")
    yield step("админ-доступ", rc == 0, err.strip()[:160] or "SELECT 1 ok")

    pw = ctx.password()
    sql = SETUP.format(role=RO_ROLE, pw=pw, db=dsn_db(admin_dsn))
    rc, _, err = _psql(ctx, admin_dsn, sql)
    yield step("RO-роль создана", rc == 0, err.strip()[:160] or RO_ROLE)

    ro_dsn = dsn_with_creds(admin_dsn, RO_ROLE, pw)
    rc, _, err = _psql(ctx, ro_dsn, "SELECT 1")
    yield step("RO читает", rc == 0, err.strip()[:160] or "SELECT 1 ok")

    rc, _, err = _psql(ctx, ro_dsn, PROBE_WRITE)
    denied = rc != 0 and "denied" in err.lower()
    yield step(
        "запись отбита",
        denied,
        "CREATE TABLE → permission denied" if denied else "ЗАПИСЬ ПРОШЛА — роль не RO",
    )

    ctx.creds = {CREDS_ENV: ro_dsn}
