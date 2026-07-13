"""Одноразовая песочница для опасного кода (bash агента).

Сервис привилегированный, но код агента запускаем БЕЗ прав: systemd-run под
unprivileged-юзером srvx-agent + read-only FS + no-new-privileges + кап по времени.
Требует root у сервиса (systemd-run --uid). Нет systemd-run/не root → available()=False.
"""

from __future__ import annotations

import os
import shutil
import subprocess

AGENT_USER = os.environ.get("SRV_EXPLORE_AGENT_USER", "srvx-agent")
MAX_SEC = os.environ.get("SRV_EXPLORE_AGENT_MAX_SEC", "300")  # анти-подвисание

_PROPS = [
    "ProtectSystem=strict",
    "ProtectHome=read-only",
    "PrivateTmp=yes",
    "NoNewPrivileges=yes",
    f"RuntimeMaxSec={MAX_SEC}",
]


def available() -> bool:
    return shutil.which("systemd-run") is not None and os.geteuid() == 0


def run(args, input_text: str | None = None, extra_env: dict | None = None):
    """Запустить args в песочнице. Вернуть (returncode, stdout, stderr)."""
    cmd = [
        "systemd-run",
        "--pipe",
        "--quiet",
        "--collect",
        "--wait",
        f"--uid={AGENT_USER}",
        "--setenv=HOME=/tmp",
    ]
    for p in _PROPS:
        cmd += ["-p", p]
    for k, v in (extra_env or {}).items():
        cmd += [f"--setenv={k}={v}"]
    cmd += list(args)
    p = subprocess.run(cmd, input=input_text, capture_output=True, text=True)
    return p.returncode, p.stdout, p.stderr
