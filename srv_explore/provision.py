"""Провижининг профиля. Сервис привилегированный → зовёт эти функции В ПРОЦЕССЕ
(apt/docker напрямую, без sudo). Опасное (bash агента) заперто отдельно — в песочнице
(sandbox.py). Действует строго по profile_id из profiles/*.py.
"""

from __future__ import annotations

import subprocess

from srv_explore import profile_store


def _profile(pid: str):
    mod = profile_store.modules().get(pid)
    if mod is None:
        raise KeyError(pid)
    return mod


def _proxy_name(pid: str) -> str:
    return f"srvx-{pid}-proxy"


def install(pid: str) -> dict:
    """Поставить клиент профиля (apt). Вернуть env-добавку (пусто)."""
    pkgs = getattr(_profile(pid), "PACKAGES", [])
    if pkgs:
        subprocess.run(["apt-get", "install", "-y", *pkgs], check=True)
    return {}


def proxy(pid: str) -> dict:
    """Поднять socket-proxy профиля (если есть). Вернуть env агенту (DOCKER_HOST)."""
    px = getattr(_profile(pid), "PROXY", None)
    if not px:
        return {}
    name = _proxy_name(pid)
    subprocess.run(
        ["docker", "rm", "-f", name],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    envs = []
    for k, v in px["env"].items():
        envs += ["-e", f"{k}={v}"]
    subprocess.run(
        [
            "docker",
            "run",
            "-d",
            "--restart",
            "unless-stopped",
            "--name",
            name,
            "-p",
            px["port"],
            "-v",
            "/var/run/docker.sock:/var/run/docker.sock:ro",
            *envs,
            px["image"],
        ],
        check=True,
    )
    return dict(px.get("sets", {}))


def down(pid: str) -> list[str]:
    """Снести proxy профиля. Вернуть env-ключи, которые надо убрать у агента."""
    mod = _profile(pid)
    px = getattr(mod, "PROXY", None)
    if px:
        subprocess.run(
            ["docker", "rm", "-f", _proxy_name(pid)],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    keys = list((px or {}).get("sets", {}))
    ce = getattr(mod, "CREDS_ENV", None)
    if ce:
        keys.append(ce)
    return keys


def enable(pid: str) -> dict:
    """install + proxy → env-добавка агенту."""
    install(pid)
    return proxy(pid)
