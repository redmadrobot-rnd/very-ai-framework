"""Профиль psql — одна read-инструкция на -c. Опциональный.
Граница read-only — роль с грантом pg_read_all_data (пароль в Secret
PG_INSPECTOR_PASSWORD). Гард: allow-префикс, forbid write/DDL; -f/файл — deny.
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
    vals, err = g.values(argv, ["-c", "--command"], file_flags=["-f", "--file"])
    if err:
        return False, err
    if not vals:
        return False, "psql: нужен -c с одной read-инструкцией"
    for sql in vals:
        s = sql.strip().lower()
        if not any(s.startswith(p) for p in _ALLOW_PREFIXES):
            return False, f"psql: начни с одного из: {', '.join(_ALLOW_PREFIXES)}"
        kw = g.forbid_words(sql, _FORBID)
        if kw:
            return False, f"psql: запрещённое слово {kw!r} (write/DDL/side-effect)"
    return True, "psql (read)"
