"""Плагин rabbitmq: заводит monitoring-юзера (read) на локальной ноде.

Админ-доступ здесь локальный (rabbitmqctl от root), внешний admin-DSN не нужен.
Read-only держат пустые config/write permissions: писать нечего, читать всё.
"""

from srv_explore.plugin_api import step

ID = "rabbitmq"
DESC = "RabbitMQ — monitoring-юзер (read)"
FIELDS = []  # ничего вводить не нужно
CREDS_ENV = "RABBITMQ_INSPECTOR_DSN"
RO_USER = "srvx_readonly"
VHOST = "/"
PORT = 5672


def install(ctx):
    yield step("клиент rabbitmqctl", ctx.which("rabbitmqctl"), "утилита ноды")

    rc, _, err = ctx.sh(["rabbitmqctl", "status"], timeout=60)
    yield step("нода обнаружена", rc == 0, err.strip()[:160] or "status ok")

    pw = ctx.password()
    rc, _, _ = ctx.sh(["rabbitmqctl", "add_user", RO_USER, pw], timeout=60)
    if rc != 0:  # юзер уже есть — ротируем пароль
        rc, _, err = ctx.sh(["rabbitmqctl", "change_password", RO_USER, pw], timeout=60)
    else:
        err = ""
    yield step("юзер создан", rc == 0, err.strip()[:160] or RO_USER)

    rc, _, err = ctx.sh(
        ["rabbitmqctl", "set_user_tags", RO_USER, "monitoring"], timeout=60
    )
    yield step("тег monitoring", rc == 0, err.strip()[:160] or "monitoring")

    # config='' write='' read='.*' — создавать/публиковать нельзя, читать можно всё
    rc, _, err = ctx.sh(
        ["rabbitmqctl", "set_permissions", "-p", VHOST, RO_USER, "^$", "^$", ".*"],
        timeout=60,
    )
    yield step("права read-only", rc == 0, err.strip()[:160] or "config/write пустые")

    rc, out, _ = ctx.sh(
        ["rabbitmqctl", "list_user_permissions", RO_USER, "--formatter", "json"],
        timeout=60,
    )
    locked = rc == 0 and '"^$"' in out.replace(" ", "")
    yield step("запись отбита", locked, "config/write = ^$ (пусто)")

    ctx.creds = {CREDS_ENV: f"amqp://{RO_USER}:{pw}@127.0.0.1:{PORT}/"}


def uninstall(ctx):
    ctx.sh(["rabbitmqctl", "delete_user", RO_USER], timeout=60)
