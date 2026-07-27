"""Плагин docker: read-only Docker API через socket-proxy.

Реальный сокет агенту недоступен (он вне группы docker) — ходит только в прокси,
где мутации (POST) выключены.
"""

import time

from srv_explore.plugin_api import step

ID = "docker"
DESC = "Docker — read-only через socket-proxy"
FIELDS = []  # ничего вводить не нужно

CONTAINER = "srvx-docker-proxy"
IMAGE = "tecnativa/docker-socket-proxy:latest"
BIND = "127.0.0.1:2375:2375"
DOCKER_HOST = "tcp://127.0.0.1:2375"
# read-эндпоинты on, любые мутации off
PROXY_ENV = {
    "CONTAINERS": "1",
    "IMAGES": "1",
    "NETWORKS": "1",
    "VOLUMES": "1",
    "INFO": "1",
    "PING": "1",
    "VERSION": "1",
    "POST": "0",
}


def install(ctx):
    yield step("клиент docker", ctx.which("docker"), "docker CLI на хосте")

    rc, _, _ = ctx.sh(["docker", "info", "--format", "{{.ServerVersion}}"])
    yield step("демон обнаружен", rc == 0, "docker.sock отвечает")

    ctx.sh(["docker", "rm", "-f", CONTAINER])
    envs = []
    for k, v in PROXY_ENV.items():
        envs += ["-e", f"{k}={v}"]
    rc, _, err = ctx.sh(
        [
            "docker",
            "run",
            "-d",
            "--restart",
            "unless-stopped",
            "--name",
            CONTAINER,
            "-p",
            BIND,
            "-v",
            "/var/run/docker.sock:/var/run/docker.sock:ro",
            *envs,
            IMAGE,
        ],
        timeout=180,
    )
    # stderr тут — прогресс скачивания образа, для детали он бесполезен
    yield step("socket-proxy поднят", rc == 0, BIND if rc == 0 else err.strip()[:160])

    probe_env = {"DOCKER_HOST": DOCKER_HOST}
    rc = 1
    for _ in range(20):  # контейнер поднимается не мгновенно
        rc, _, _ = ctx.sh(
            ["docker", "version", "--format", "{{.Server.Version}}"], env=probe_env
        )
        if rc == 0:
            break
        time.sleep(0.5)
    yield step("чтение работает", rc == 0, "docker version через прокси")

    probe = "srvx-install-probe"
    rc, _, _ = ctx.sh(["docker", "network", "create", probe], env=probe_env)
    if rc == 0:  # мутация прошла — прокси НЕ read-only
        ctx.sh(["docker", "network", "rm", probe], env=probe_env)
    yield step("запись отбита", rc != 0, "мутирующий вызов → 403")

    ctx.creds = {"DOCKER_HOST": DOCKER_HOST}


def toggle(ctx, enabled):
    """Тумблер гасит и поднимает сам прокси. Прятать DOCKER_HOST мало: прокси
    слушает loopback, а loopback песочнице разрешён — выключенный плагин иначе
    продолжал бы отдавать Docker API любому `curl 127.0.0.1:2375`.
    `--restart unless-stopped` остановленный вручную контейнер не поднимает."""
    verb = "start" if enabled else "stop"
    rc, _, err = ctx.sh(["docker", verb, CONTAINER], timeout=60)
    if rc != 0:
        raise RuntimeError(f"docker {verb} {CONTAINER}: {err.strip()[:120]}")


def uninstall(ctx):
    ctx.sh(["docker", "rm", "-f", CONTAINER])
