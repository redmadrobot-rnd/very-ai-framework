"""Профиль psql — одна read-инструкция за запрос. Движок sql (g.sql).

Граница read-only — роль без write/DDL-грантов (НЕ флаг сессии):
    CREATE ROLE inspector LOGIN PASSWORD :'pw';
    GRANT CONNECT ON DATABASE app TO inspector;
    GRANT pg_read_all_data TO inspector;         -- PG14+
    ALTER ROLE inspector SET statement_timeout = '15s';
    -- НЕ давать SUPERUSER/CREATE EXTENSION/dblink — пишут side-effect'ом.
Пароль — Environment Secret PG_INSPECTOR_PASSWORD. Гард: один SELECT/WITH/EXPLAIN/…
на -c, без EXPLAIN ANALYZE, без write-функций; -f/файл-инструкция запрещены.
"""

ID = "postgres"
COMMANDS = ["psql"]
DESC = "psql (одна read-инструкция за запрос)"

_ALLOW_PREFIXES = ["select", "with", "explain", "show", "table", "values"]
_FORBID = [
    "insert",
    "update",
    "delete",
    "drop",
    "truncate",
    "alter",
    "create",
    "grant",
    "revoke",
    "copy",
    "merge",
    "call",
    "do",
    "vacuum",
    "analyze",
    "reindex",
    "cluster",
    "lock",
    "comment",
    "refresh",
    "prepare",
    "execute",
    "into",
    "pg_read_file",
    "pg_read_binary_file",
    "pg_ls_dir",
    "pg_stat_file",
    "pg_ls_logdir",
    "pg_ls_waldir",
    "lo_import",
    "lo_export",
    "pg_sleep",
    "pg_sleep_for",
    "pg_sleep_until",
    "set",
    "reset",
    "begin",
    "commit",
    "rollback",
    "set_config",
    "current_setting",
    "pg_terminate_backend",
    "pg_cancel_backend",
    "pg_reload_conf",
    "pg_rotate_logfile",
    "pg_switch_wal",
    "pg_switch_xlog",
    "pg_create_restore_point",
    "pg_logical_emit_message",
    "pg_stat_reset",
    "pg_advisory_lock",
    "pg_advisory_lock_shared",
    "pg_advisory_xact_lock",
    "pg_advisory_xact_lock_shared",
    "dblink",
    "dblink_exec",
    "setval",
    "nextval",
]


def check(argv, g):
    return g.sql(
        argv,
        cmd_flags=["-c", "--command"],
        file_flags=["-f", "--file"],
        allow_prefixes=_ALLOW_PREFIXES,
        forbid=_FORBID,
    )
