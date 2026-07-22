"""Провижининг профиля. Сервис привилегированный → зовёт эти функции В ПРОЦЕССЕ
(apt/docker напрямую, без sudo). Опасное (bash агента) заперто отдельно — в песочнице
(sandbox.py). Действует строго по profile_id из profiles/*.py.
"""

from __future__ import annotations

import os
import secrets
import shutil
import subprocess
from urllib.parse import quote, unquote, urlsplit, urlunsplit

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
    """install + proxy → env-добавка агенту (для proxy-профилей, напр. docker)."""
    install(pid)
    return proxy(pid)


# --- БД-профили: создание read-only роли из admin-DSN (режим Б) -----------------
# Секреты (admin/ro пароли) НЕ на argv (ps их видит) — только через env клиента.


def _dsn_db(dsn: str) -> str:
    return urlsplit(dsn).path.lstrip("/")


def _dsn_with_creds(dsn: str, user: str, pw: str) -> str:
    """Тот же DSN, но с новым user:pw (хост/порт/база/query сохраняются)."""
    p = urlsplit(dsn)
    netloc = f"{quote(user)}:{quote(pw)}@{p.hostname or ''}"
    if p.port:
        netloc += f":{p.port}"
    return urlunsplit((p.scheme, netloc, p.path, p.query, p.fragment))


def _pg_env(dsn: str) -> dict:
    p = urlsplit(dsn)
    env = {"PGCONNECT_TIMEOUT": "10"}
    if p.hostname:
        env["PGHOST"] = p.hostname
    if p.port:
        env["PGPORT"] = str(p.port)
    if p.username:
        env["PGUSER"] = unquote(p.username)
    if p.password:
        env["PGPASSWORD"] = unquote(p.password)
    db = _dsn_db(dsn)
    if db:
        env["PGDATABASE"] = db
    return env


def _pg_run(dsn: str, sql: str):
    p = subprocess.run(
        ["psql", "-v", "ON_ERROR_STOP=1", "-tAc", sql],
        env={**os.environ, **_pg_env(dsn)},
        capture_output=True,
        text=True,
        timeout=30,
    )
    return p.returncode, p.stdout, p.stderr


_RUNNERS = {"postgres": _pg_run}


def _run_stmt(mod, dsn: str, sql: str):
    runner = _RUNNERS.get(getattr(mod, "KIND", None))
    if runner is None:
        raise KeyError(f"нет драйвера для KIND={getattr(mod, 'KIND', None)}")
    return runner(dsn, sql)


def create_role(pid: str, admin_dsn: str) -> str:
    """По admin-DSN создать read-only роль профиля, вернуть её ro-DSN.
    admin-DSN нигде не сохраняется (живёт только в этом вызове)."""
    mod = _profile(pid)
    role = getattr(mod, "RO_ROLE", None)
    setup = getattr(mod, "SETUP", None)
    if not role or not setup:
        raise KeyError(f"{pid}: нет RO_ROLE/SETUP")
    pw = secrets.token_hex(24)
    sql = setup.format(role=role, pw=pw, db=_dsn_db(admin_dsn))
    rc, out, err = _run_stmt(mod, admin_dsn, sql)
    if rc != 0:
        raise RuntimeError(f"SETUP не прошёл: {(err or out).strip()[:300]}")
    return _dsn_with_creds(admin_dsn, role, pw)


def verify_dsn(pid: str, dsn: str) -> str:
    """VERIFY = проба-нарушитель под dsn. Отклонена → 'ok', прошла → 'broken'."""
    mod = _profile(pid)
    v = getattr(mod, "VERIFY", None)
    if not v:
        return "ok"
    try:
        rc, _, _ = _run_stmt(mod, dsn, v)
    except (OSError, subprocess.SubprocessError, KeyError):
        return "error"
    return "broken" if rc == 0 else "ok"


def _docker_health(pid: str, px: dict) -> dict:
    name = _proxy_name(pid)
    r = subprocess.run(
        ["docker", "inspect", "-f", "{{.State.Running}}", name],
        capture_output=True,
        text=True,
    )
    if r.returncode != 0 or r.stdout.strip() != "true":
        return {"state": "broken", "detail": "socket-proxy не запущен"}
    host = px.get("sets", {}).get("DOCKER_HOST")
    env = {**os.environ, "DOCKER_HOST": host} if host else dict(os.environ)
    probe = "srvx-verify-probe"
    m = subprocess.run(
        ["docker", "network", "create", probe],
        env=env,
        capture_output=True,
        text=True,
    )
    if m.returncode == 0:  # мутация прошла — прокси НЕ read-only
        subprocess.run(
            ["docker", "network", "rm", probe],
            env=env,
            capture_output=True,
            text=True,
        )
        return {"state": "broken", "detail": "мутация прошла — прокси пускает запись"}
    return {"state": "ok", "detail": "read-only через socket-proxy"}


_HEALTH_DETAIL = {
    "ok": "read-only подтверждён",
    "broken": "ЗАПИСЬ ПРОШЛА — DSN не read-only",
    "error": "проба не выполнилась",
}


def health(pid: str) -> dict:
    """Состояние профиля: off | ok | setup | broken | error (+ detail)."""
    mod = _profile(pid)
    if not profile_store.load().get(pid):
        return {"state": "off", "detail": ""}
    px = getattr(mod, "PROXY", None)
    if px:
        return _docker_health(pid, px)
    ce = getattr(mod, "CREDS_ENV", None)
    if ce:
        dsn = profile_store.provisioned().get(ce)
        if not dsn:
            return {"state": "setup", "detail": "креды не выданы"}
        st = verify_dsn(pid, dsn)
        return {"state": st, "detail": _HEALTH_DETAIL.get(st, "")}
    cmd = (getattr(mod, "COMMANDS", []) or [None])[0]
    ok = cmd is not None and shutil.which(cmd) is not None
    return {
        "state": "ok" if ok else "setup",
        "detail": "клиент установлен" if ok else "клиент не установлен",
    }
