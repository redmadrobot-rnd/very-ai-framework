"""Одноразовая песочница для опасного кода (bash агента).

Сервис привилегированный, но код агента запускаем БЕЗ прав: systemd-run под
unprivileged-юзером srvx-agent + read-only FS + no-new-privileges + кап по времени +
EGRESS-FIREWALL. Внешняя сеть обрублена (IPAddressDeny=any), разрешены только loopback,
приватные сети и доверенные CIDR; в остальной интернет (в т.ч. API модели) агент ходит
ТОЛЬКО через форвард-прокси с доменным allowlist. Требует root у сервиса и cgroup v2.
Нет systemd-run/не root → available()=False.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

AGENT_USER = os.environ.get("SRV_EXPLORE_AGENT_USER", "srvx-agent")
MAX_SEC = os.environ.get("SRV_EXPLORE_AGENT_MAX_SEC", "300")  # анти-подвисание
PROXY = os.environ.get("SRV_EXPLORE_PROXY", "http://127.0.0.1:3128")
# каталог-родитель пакета srv_explore — чтобы `python -m srv_explore.*` в песочнице
# нашёл пакет независимо от cwd (RO-FS читать не мешает).
_PKG_PARENT = str(Path(__file__).resolve().parent.parent)

# Прямой egress: только loopback + link-local + приватные сети + доверенные CIDR (env).
# Явные префиксы, не именованные токены (старый systemd их не парсит).
_ALLOW_BASE = (
    "127.0.0.0/8 ::1/128 169.254.0.0/16 fe80::/10 "
    "10.0.0.0/8 172.16.0.0/12 192.168.0.0/16"
)
_NO_PROXY = (
    "localhost,127.0.0.1,::1,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,169.254.0.0/16"
)


def _ip_allow() -> str:
    trusted = os.environ.get("SRV_EXPLORE_TRUSTED_CIDRS", "").strip()
    return _ALLOW_BASE + (" " + trusted if trusted else "")


_PROPS = [
    "ProtectSystem=strict",
    "ProtectHome=read-only",
    "PrivateTmp=yes",
    "NoNewPrivileges=yes",
    f"RuntimeMaxSec={MAX_SEC}",
    "IPAddressDeny=any",  # внешка обрублена; ниже — что разрешено
]

# Прокси для внешки (API модели + доверенные домены); внутреннее — напрямую (NO_PROXY).
_PROXY_ENV = {
    "HTTP_PROXY": PROXY,
    "HTTPS_PROXY": PROXY,
    "http_proxy": PROXY,
    "https_proxy": PROXY,
    "NO_PROXY": _NO_PROXY,
    "no_proxy": _NO_PROXY,
}


def available() -> bool:
    return shutil.which("systemd-run") is not None and os.geteuid() == 0


def run(args, input_text: str | None = None, extra_env: dict | None = None):
    """Запустить args в песочнице. Вернуть (returncode, stdout, stderr)."""
    env = {"HOME": "/tmp", "PYTHONPATH": _PKG_PARENT, **_PROXY_ENV, **(extra_env or {})}
    cmd = [
        "systemd-run",
        "--pipe",
        "--quiet",
        "--collect",
        "--wait",
        f"--uid={AGENT_USER}",
    ]
    for p in _PROPS:
        cmd += ["-p", p]
    cmd += ["-p", f"IPAddressAllow={_ip_allow()}"]
    for k, v in env.items():
        cmd += [f"--setenv={k}={v}"]
    cmd += list(args)
    p = subprocess.run(cmd, input=input_text, capture_output=True, text=True)
    return p.returncode, p.stdout, p.stderr
