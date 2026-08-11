"""Плагин rabbitmq: monitoring-юзер management-API на локальной ноде.

Админ-доступ здесь локальный (rabbitmqctl от root), внешний admin-DSN не нужен.

Почему НЕ AMQP: в модели RabbitMQ `queue.purge`, `basic.get` и `basic.consume`
требуют только `read` на очередь — то есть кред с read='.*' вычерпывает и чистит
любую очередь. Поэтому все три AMQP-права выданы пустыми (^$), а смотреть агент
ходит в management HTTP API под тегом monitoring: очереди, глубины, подключения
и каналы видны, менять нельзя ничего.
"""

import json

from srv_explore.plugin_api import step

ID = "rabbitmq"
DESC = "RabbitMQ — monitoring через management API"
FIELDS = []  # ничего вводить не нужно
PACKAGES = ["curl"]
CREDS_ENV = "RABBITMQ_INSPECTOR_API"
RO_USER = "srvx_readonly"
VHOST = "/"
API_HOST = "127.0.0.1:15672"
# Никогда не совпадает. "^$" сюда НЕ годится: у дефолтного обменника имя пустое,
# и write="^$" разрешил бы publish в него, а оттуда — в любую очередь по ключу.
DENY = "(?!)"
PROBE_QUEUE = "srvx-probe"


def _ctl(ctx, args, pw=None):
    """rabbitmqctl. Пароль (если нужен) уходит в stdin: в argv он виден в `ps`."""
    return ctx.sh(["rabbitmqctl", *args], timeout=60, input_text=pw)


def _api(ctx, method: str, path: str, pw: str):
    """Вызов management API под создаваемым юзером. Возвращает (http_code, тело).

    Пароль уходит в конфиг curl (временный файл 0600), а не в `-u`: argv виден
    в `ps` любому на хосте.
    """
    conf = (
        f'user = "{RO_USER}:{pw}"\n'
        'silent\nshow-error\nwrite-out = "\\n%{http_code}"\n'
    )
    rc, out, err = ctx.sh_script(
        lambda f: [
            "curl",
            "-K",
            f,
            "-o",
            "/dev/stdout",
            "-X",
            method,
            f"http://{API_HOST}{path}",
        ],
        conf,
        suffix=".curl",
    )
    if rc != 0:
        return "000", err.strip()[:120]
    body, _, code = out.rpartition("\n")
    return code.strip(), body.strip()


def _set_password(ctx, cmd: str, pw: str):
    """add_user/change_password без пароля в argv: rabbitmqctl спрашивает его сам и
    в неинтерактивном режиме читает со stdin. Старые сборки так не умеют — тогда
    честно откатываемся на argv и говорим об этом в чеклисте."""
    rc, _, err = _ctl(ctx, [cmd, RO_USER], pw=f"{pw}\n{pw}\n")
    if rc == 0:
        return rc, err, "пароль через stdin"
    rc, _, err = _ctl(ctx, [cmd, RO_USER, pw])
    return rc, err, "пароль ушёл в argv: rabbitmqctl не взял его со stdin"


def install(ctx):
    # rabbitmqctl ставится вместе с брокером, apt тут докидывает только curl
    apt_ok, apt_detail = ctx.apt(PACKAGES)
    has_ctl = ctx.which("rabbitmqctl")
    yield step(
        "клиенты",
        apt_ok and has_ctl,
        "rabbitmqctl + curl"
        if apt_ok and has_ctl
        else (
            "rabbitmqctl не найден — RabbitMQ на хосте нет" if apt_ok else apt_detail
        ),
    )

    rc, _, err = _ctl(ctx, ["status"])
    yield step("нода обнаружена", rc == 0, err.strip()[:160] or "status ok")

    # Без management-плагина смотреть нечем: AMQP-права мы отдаём пустыми. Живой API
    # без кред отвечает 401 — этого достаточно, чтобы отличить его от выключенного.
    rc, code, _ = ctx.sh(
        [
            "curl",
            "-sS",
            "-o",
            "/dev/null",
            "-w",
            "%{http_code}",
            f"http://{API_HOST}/api/overview",
        ],
        timeout=20,
    )
    up = code.strip() in ("200", "401")
    yield step(
        "management API включён",
        up,
        API_HOST if up else "включи: rabbitmq-plugins enable rabbitmq_management",
    )

    pw = ctx.password()
    rc, err, how = _set_password(ctx, "add_user", pw)
    if rc != 0:  # юзер уже есть — ротируем пароль
        rc, err, how = _set_password(ctx, "change_password", pw)
    yield step("юзер создан", rc == 0, err.strip()[:160] or f"{RO_USER}, {how}")

    rc, _, err = _ctl(ctx, ["set_user_tags", RO_USER, "monitoring"])
    yield step("тег monitoring", rc == 0, err.strip()[:160] or "чтение метрик")

    # Все три пустые: ни publish, ни declare, ни consume/get/purge через AMQP.
    rc, _, err = _ctl(ctx, ["set_permissions", "-p", VHOST, RO_USER, DENY, DENY, DENY])
    yield step("AMQP-права сняты", rc == 0, err.strip()[:160] or f"все три = {DENY}")

    # Права на ОСТАЛЬНЫХ vhost снимаем совсем: остаточная строка с write/configure на
    # другом vhost (тот же пароль в management-URL) дала бы запись мимо проверки '/'.
    rc, vh_out, _ = _ctl(ctx, ["list_vhosts", "--formatter", "json"])
    cleared = []
    try:
        for v in json.loads(vh_out or "[]"):
            name = v.get("name")
            if name and name != VHOST:
                _ctl(ctx, ["clear_permissions", "-p", name, RO_USER])
                cleared.append(name)
    except ValueError:
        pass

    # Брокер должен подтвердить: единственная оставшаяся строка прав — '/' с тремя
    # never-match. Проверяем ВСЕ строки (не одну), иначе опечатка в шаблоне или
    # пропущенный vhost тихо вернули бы юзеру write.
    rc, out, _ = _ctl(ctx, ["list_user_permissions", RO_USER, "--formatter", "json"])
    try:
        rows = json.loads(out or "[]")
    except ValueError:
        rows = None
    applied = (
        rc == 0
        and rows is not None
        and len(rows) >= 1
        and all(
            r.get("configure") == DENY
            and r.get("write") == DENY
            and r.get("read") == DENY
            for r in rows
        )
    )
    extra = f"; очищено vhost: {', '.join(cleared)}" if cleared else ""
    yield step(
        "права подтверждены брокером",
        applied,
        (f"все vhost: configure/write/read = {DENY}{extra}")
        if applied
        else out.strip()[:160],
    )

    code, body = _api(ctx, "GET", "/api/queues", pw)
    yield step("API читает", code == "200", f"GET /api/queues → {code} {body[:100]}")

    # Проба-нарушитель: PUT создаёт очередь. Не зависит от того, есть ли очереди на
    # ноде (в отличие от purge, который на несуществующей вернул бы 404 — отказ не
    # по правам). Прошло — юзер не read-only, убираем за собой.
    code, body = _api(ctx, "PUT", f"/api/queues/%2F/{PROBE_QUEUE}", pw)
    denied = code in ("401", "403")
    if code in ("201", "204"):
        _api(ctx, "DELETE", f"/api/queues/%2F/{PROBE_QUEUE}", pw)
    yield step(
        "запись отбита",
        denied,
        f"создание очереди → {code}"
        if denied
        else f"создание очереди вернуло {code} — юзер не read-only",
    )

    ctx.creds = {CREDS_ENV: f"http://{RO_USER}:{pw}@{API_HOST}"}


def uninstall(ctx):
    _ctl(ctx, ["delete_user", RO_USER])
