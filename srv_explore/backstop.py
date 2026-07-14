"""Стартовый детект песочницы агента: FS read-only + egress обрублен.

Пробы бегут В ПЕСОЧНИЦЕ агента (см. sandbox.py), не в привилегированном сервисе.
Результат — индикаторы в admin (FileSystem / Network). Красный = харденинг не активен
(сервис вне штатного окружения / cgroup v2 без IP-фильтра).
"""

from __future__ import annotations

import errno
import os
import secrets
import socket
from datetime import datetime, timezone

_PROBE_DIRS = ("/etc", "/usr", "/var/lib", "/opt", "/")


def _fs_readonly() -> bool | None:
    """True — запись в системный каталог даёт EROFS (ядро держит read-only)."""
    saw = False
    for d in _PROBE_DIRS:
        if not os.path.isdir(d):
            continue
        saw = True
        path = os.path.join(d, f".srvx_probe_{secrets.token_hex(4)}")
        try:
            fd = os.open(path, os.O_CREAT | os.O_WRONLY | os.O_EXCL, 0o600)
        except OSError as e:
            if e.errno == errno.EROFS:
                return True
            continue  # EACCES/EPERM — нет прав, не гарантия read-only
        os.close(fd)
        os.unlink(path)
        return False
    return False if saw else None


def _egress_locked() -> bool | None:
    """True — прямое внешнее соединение блокирует ядро (IPAddressDeny → EPERM).
    False — прошло (egress открыт). None — таймаут/неясно."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(1.5)
    try:
        s.connect(("1.1.1.1", 443))
        return False
    except PermissionError:
        return True
    except OSError:
        return None
    finally:
        s.close()


def probe() -> dict:
    return {
        "fs_readonly": _fs_readonly(),
        "egress_locked": _egress_locked(),
        "checked_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
    }


def _tri(v: bool | None) -> str:
    return "unknown" if v is None else ("green" if v else "red")


def status(p: dict) -> str:
    """Индикатор FileSystem — read-only ядром."""
    return _tri(p.get("fs_readonly"))


def net_status(p: dict) -> str:
    """Индикатор Network — прямая внешка обрублена (остальное — прокси-allowlist)."""
    return _tri(p.get("egress_locked"))
