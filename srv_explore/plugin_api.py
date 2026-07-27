"""Контракт плагина: что плагин получает и что отдаёт.

Плагин (profiles/*.py) объявляет метаданные и генератор `install(ctx)`, который
yield-ит шаги чеклиста. Первый упавший шаг останавливает установку. Успешный прогон
кладёт креды агенту в `ctx.creds`.

Модуль намеренно без зависимостей на остальной пакет — его импортируют и плагины,
и провизионер.
"""

from __future__ import annotations

import os
import secrets
import shutil
import subprocess
import tempfile
from urllib.parse import quote, unquote, urlsplit, urlunsplit


def step(name: str, ok: bool, detail: str = "") -> dict:
    """Пункт чеклиста установки."""
    return {"name": name, "ok": bool(ok), "detail": detail}


class Ctx:
    """Инструменты плагина на время установки. Сервис привилегированный (root),
    поэтому apt/docker/клиенты зовутся напрямую."""

    def __init__(self, admin_dsn: str = ""):
        self.admin_dsn = admin_dsn  # одноразовый, никуда не сохраняется
        self.creds: dict[str, str] = {}  # что получит агент при On

    def sh(self, argv: list[str], env: dict | None = None, timeout: int = 30):
        """Запустить команду. Секреты — через env, не через argv (argv виден в ps)."""
        try:
            p = subprocess.run(
                argv,
                env={**os.environ, **(env or {})},
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except (OSError, subprocess.SubprocessError) as e:
            return 1, "", str(e)
        return p.returncode, p.stdout, p.stderr

    def sh_script(self, argv_for, content: str, suffix: str = "", timeout: int = 30):
        """Как sh, но команда работает с временным файлом 0600 — для клиентов,
        которые не берут креды из env (иначе секрет виден в argv)."""
        fd, path = tempfile.mkstemp(suffix=suffix)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(content)
            os.chmod(path, 0o600)
            return self.sh(argv_for(path), timeout=timeout)
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass

    def which(self, cmd: str) -> bool:
        return shutil.which(cmd) is not None

    def apt(self, packages: list[str]) -> tuple[bool, str]:
        """Доустановить пакеты, если клиента ещё нет."""
        if not packages:
            return True, "ставить нечего"
        rc, _, err = self.sh(
            ["apt-get", "install", "-y", *packages],
            env={"DEBIAN_FRONTEND": "noninteractive"},
            timeout=300,
        )
        return rc == 0, "установлено" if rc == 0 else err.strip()[:200]

    def password(self) -> str:
        return secrets.token_hex(24)


# --- DSN: разбор и сборка (нужно почти каждому БД-плагину) ----------------------


def dsn_db(dsn: str) -> str:
    return urlsplit(dsn).path.lstrip("/")


def dsn_host(dsn: str) -> tuple[str, int | None]:
    p = urlsplit(dsn)
    return p.hostname or "", p.port


def dsn_user(dsn: str) -> tuple[str, str]:
    p = urlsplit(dsn)
    return unquote(p.username or ""), unquote(p.password or "")


def dsn_with_creds(dsn: str, user: str, pw: str) -> str:
    """Тот же DSN с другими creds (хост/порт/база/query сохраняются)."""
    p = urlsplit(dsn)
    netloc = f"{quote(user)}:{quote(pw)}@{p.hostname or ''}"
    if p.port:
        netloc += f":{p.port}"
    return urlunsplit((p.scheme, netloc, p.path, p.query, p.fragment))


def tcp_open(host: str, port: int, timeout: float = 5.0) -> bool:
    """Достижим ли ресурс (шаг «БД обнаружена»)."""
    import socket

    if not host or not port:
        return False
    s = socket.socket()
    s.settimeout(timeout)
    try:
        s.connect((host, port))
        return True
    except OSError:
        return False
    finally:
        s.close()
