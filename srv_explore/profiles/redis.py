"""Плагин redis: из одноразового admin-DSN заводит read-only ACL-юзера для агента.

Пароль отдаём redis-cli через REDISCLI_AUTH (env), не через argv.
Нужен Redis ≥ 6 (ACL).
"""

from srv_explore.plugin_api import (
    dsn_host,
    dsn_user,
    dsn_with_creds,
    field,
    step,
    tcp_open,
)

ID = "redis"
DESC = "Redis — read-only ACL-юзер"
FIELDS = [
    field(
        "admin_dsn",
        "Админский DSN",
        "redis://:пароль@host:6379",
        secret=True,
        hint="одноразово: из него создаётся ACL-юзер, сам DSN не сохраняется",
    )
]
PACKAGES = ["redis-tools"]
CREDS_ENV = "REDIS_INSPECTOR_DSN"
RO_USER = "srvx_readonly"


def _cli(ctx, dsn: str, args: list[str], stdin_cmd: str | None = None):
    """redis-cli: пароль админа — через REDISCLI_AUTH, команда с секретом внутри —
    через stdin (argv виден в ps любому на хосте)."""
    host, port = dsn_host(dsn)
    user, pw = dsn_user(dsn)
    argv = [
        "redis-cli",
        "--no-auth-warning",
        "-h",
        host or "127.0.0.1",
        "-p",
        str(port or 6379),
    ]
    if user:
        argv += ["--user", user]
    return ctx.sh(
        argv + args,
        env={"REDISCLI_AUTH": pw} if pw else None,
        input_text=stdin_cmd,
    )


def install(ctx):
    admin_dsn = ctx.field("admin_dsn")

    ok, detail = ctx.apt(PACKAGES)
    yield step(
        "клиент redis-cli",
        ok and ctx.which("redis-cli"),
        detail if not ok else "redis-cli готов",
    )

    host, port = dsn_host(admin_dsn)
    port = port or 6379
    yield step("ресурс обнаружен", tcp_open(host, port), f"{host}:{port}")

    rc, out, err = _cli(ctx, admin_dsn, ["PING"])
    yield step("админ-доступ", rc == 0 and "PONG" in out, (err or out).strip()[:160])

    rc, out, err = _cli(ctx, admin_dsn, ["ACL", "WHOAMI"])
    yield step("ACL поддерживается", rc == 0, (err or out).strip()[:160] or "Redis ≥ 6")

    pw = ctx.password()
    # команда с паролем идёт через stdin, а не argv
    setuser = f"ACL SETUSER {RO_USER} on >{pw} ~* &* +@read -@dangerous resetchannels\n"
    rc, out, err = _cli(ctx, admin_dsn, [], stdin_cmd=setuser)
    created = rc == 0 and "ERR" not in (out + err).upper()
    yield step("RO-юзер создан", created, (err or out).strip()[:160] or RO_USER)

    # ACL живёт в памяти: без SAVE юзер исчезнет при рестарте (нужен aclfile/CONFIG).
    rc, out, _ = _cli(ctx, admin_dsn, ["ACL", "SAVE"])
    yield step(
        "ACL сохранён",
        True,
        "сохранён" if rc == 0 else "aclfile не настроен — юзер до рестарта",
    )

    ro_dsn = dsn_with_creds(admin_dsn, RO_USER, pw)
    rc, out, err = _cli(ctx, ro_dsn, ["DBSIZE"])
    yield step("RO читает", rc == 0, (err or out).strip()[:160] or "DBSIZE ok")

    rc, out, err = _cli(ctx, ro_dsn, ["SET", "_srvx_probe", "1"])
    text = (err + out).lower()
    denied = "noperm" in text or "no permissions" in text
    yield step(
        "запись отбита",
        denied,
        "SET → NOPERM" if denied else "ЗАПИСЬ ПРОШЛА — юзер не RO",
    )

    ctx.creds = {CREDS_ENV: ro_dsn}
