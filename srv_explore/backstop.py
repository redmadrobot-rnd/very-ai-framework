"""Стартовый детект OS-бэкстопа: FS read-only (ProtectSystem) и закрытый egress.

Результат — индикатор в admin (зелёный/амбер/красный). Не меняет поведение гарда;
это честный сигнал, активен ли на этом хосте OS-хардeнинг, который держит read-only
и no-exfil на уровне ядра. Красный = сервис поднят вне штатного systemd-юнита.
"""

from __future__ import annotations

import errno
import os
import secrets
import socket
from datetime import datetime, timezone

# Системные каталоги, писабельность которых различает режимы: под ProtectSystem=strict
# создание файла упирается в EROFS; без харденинга — EACCES (нет прав) или успех.
_PROBE_DIRS = ("/etc", "/usr", "/var/lib", "/opt", "/")


def _fs_readonly() -> bool | None:
    """True — запись в системный каталог даёт EROFS (ядро держит read-only).
    False — где-то удалось создать файл или везде лишь EACCES (RO не доказан).
    None — не Linux / нет каталогов для пробы."""
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
        return False  # файл создан → FS писабельна
    return False if saw else None


def _egress_locked() -> bool | None:
    """True — исходящее соединение блокирует ядро (IPAddressDeny → EPERM).
    False — соединение прошло (egress открыт). None — таймаут/неясно."""
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


def status(p: dict) -> str:
    """Индикатор по главной гарантии — FS read-only ядром (ProtectSystem=strict).
    egress не критерий: агенту нужен внешний API модели, сеть не закрыта наглухо
    (egress-firewall — коммит 2). green — FS read-only; red — писабельна."""
    fs = p.get("fs_readonly")
    if fs is None:
        return "unknown"
    return "green" if fs is True else "red"
