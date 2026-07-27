"""Плагин mongo: из одноразового admin-DSN заводит read-юзера для агента.

mongosh не умеет брать креды из env, поэтому DSN уходит в JS-файл 0600 (`sh_script`),
а не в argv — иначе пароль виден в `ps`.
"""

import json
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from srv_explore.plugin_api import (
    dsn_db,
    dsn_host,
    dsn_with_creds,
    field,
    step,
    tcp_open,
)

ID = "mongo"
DESC = "MongoDB — read-роль"
FIELDS = [
    field(
        "admin_dsn",
        "Админский DSN",
        "mongodb://admin:пароль@host:27017/база?authSource=admin",
        secret=True,
        hint="одноразово: из него создаётся read-юзер, сам DSN не сохраняется",
    )
]
PACKAGES = ["mongodb-mongosh"]  # есть только в репозитории MongoDB, не в базовой Ubuntu
CREDS_ENV = "MONGO_INSPECTOR_DSN"
RO_USER = "srvx_readonly"
PROBE_COLL = "_srvx_probe"
MONGOSH_VERSION = "2.3.8"  # фолбэк-установка, если пакета в apt нет
MONGOSH_BIN = "/usr/local/bin/mongosh"


def _with_auth_source(dsn: str, db: str) -> str:
    p = urlsplit(dsn)
    q = [(k, v) for k, v in parse_qsl(p.query) if k != "authSource"]
    q.append(("authSource", db))
    return urlunsplit((p.scheme, p.netloc, p.path, urlencode(q), p.fragment))


def _eval(ctx, dsn: str, body: str):
    """Выполнить JS против dsn. Возвращает (rc, stdout, stderr)."""
    script = f"const db = connect({json.dumps(dsn)});\n{body}\n"
    return ctx.sh_script(
        lambda p: ["mongosh", "--nodb", "--quiet", "--file", p], script, suffix=".js"
    )


def _mongosh_works(ctx) -> bool:
    """Именно работоспособность, а не наличие файла: полуустановленный tarball
    оставляет бинарь, который не запускается."""
    rc, _, _ = ctx.sh(["mongosh", "--version"], timeout=20)
    return rc == 0


def _ensure_mongosh(ctx) -> tuple[bool, str]:
    """mongosh нет в стандартных репах Ubuntu: сначала apt, иначе — официальный
    tarball в /usr/local (без правки apt-источников хоста)."""
    if _mongosh_works(ctx):
        return True, "уже установлен"
    ok, _ = ctx.apt(PACKAGES)
    if ok and _mongosh_works(ctx):
        return True, "установлен из apt"

    url = (
        f"https://downloads.mongodb.com/compass/mongosh-{MONGOSH_VERSION}-linux-x64.tgz"
    )
    rc, _, err = ctx.sh(
        [
            "bash",
            "-c",
            f"set -e; tmp=$(mktemp -d); curl -fsSL {url} -o $tmp/m.tgz; "
            f"tar xzf $tmp/m.tgz -C $tmp; "
            f"install -m 0755 $tmp/mongosh-*/bin/mongosh {MONGOSH_BIN}; "
            f"install -m 0755 $tmp/mongosh-*/lib/* /usr/local/lib/ 2>/dev/null "
            f"|| true; "
            f"rm -rf $tmp",
        ],
        timeout=300,
    )
    if rc == 0 and _mongosh_works(ctx):
        return True, f"tarball {MONGOSH_VERSION} → {MONGOSH_BIN}"
    return False, (err.strip()[:180] or "не удалось поставить mongosh")


def install(ctx):
    admin_dsn = ctx.field("admin_dsn")

    ok, detail = _ensure_mongosh(ctx)
    yield step("клиент mongosh", ok, detail)

    host, port = dsn_host(admin_dsn)
    port = port or 27017
    yield step("БД обнаружена", tcp_open(host, port), f"{host}:{port}")

    rc, out, err = _eval(ctx, admin_dsn, "print(db.runCommand({ping: 1}).ok)")
    yield step("админ-доступ", rc == 0, err.strip()[:160] or "ping ok")

    pw = ctx.password()
    db = dsn_db(admin_dsn) or "admin"
    # Идемпотентно: юзер есть — обновить роль и пароль, нет — создать.
    body = (
        f"const u = {json.dumps(RO_USER)}, pw = {json.dumps(pw)};\n"
        f"const roles = [{{role: 'read', db: {json.dumps(db)}}}];\n"
        "if (db.getUser(u)) { db.updateUser(u, {pwd: pw, roles: roles}); }\n"
        "else { db.createUser({user: u, pwd: pw, roles: roles}); }\n"
    )
    rc, _, err = _eval(ctx, admin_dsn, body)
    yield step("read-юзер создан", rc == 0, err.strip()[:160] or RO_USER)

    # юзер заведён в базе из DSN → authSource должен указывать на неё, а не на admin
    ro_dsn = _with_auth_source(dsn_with_creds(admin_dsn, RO_USER, pw), db)
    rc, _, err = _eval(ctx, ro_dsn, "print(db.getCollectionNames().length)")
    yield step("RO читает", rc == 0, err.strip()[:160] or "чтение ok")

    # Отказ засчитывается только по явному сигналу авторизации: просто ненулевой
    # код мог бы прийти от обрыва связи или ошибки в самом скрипте, и тогда
    # «не смогли записать» означало бы «не смогли дотянуться».
    # getCollection, а не db.<имя>: имя с подчёркиванием как свойство не резолвится,
    # и проба падала бы на TypeError вместо отказа по правам.
    rc, out, err = _eval(
        ctx, ro_dsn, f'db.getCollection("{PROBE_COLL}").insertOne({{x: 1}})'
    )
    text = (err + out).lower()
    denied = rc != 0 and ("not authorized" in text or "unauthorized" in text)
    if rc == 0:
        detail = "ЗАПИСЬ ПРОШЛА — юзер не RO"
    elif denied:
        detail = "insertOne → not authorized"
    else:
        detail = f"проба не дошла, отказ не подтверждён: {(err or out).strip()[:110]}"
    yield step("запись отбита", denied, detail)

    ctx.creds = {CREDS_ENV: ro_dsn}
