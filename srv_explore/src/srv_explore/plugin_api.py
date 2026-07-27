"""Контракт плагина: что плагин получает и что отдаёт.

Плагин (plugins/*.py) объявляет метаданные, список полей формы `FIELDS` и генератор
`install(ctx)`, который yield-ит шаги чеклиста. Первый упавший шаг останавливает
установку. Успешный прогон кладёт креды агенту в `ctx.creds`.

Что вводит админ — решает сам плагин: ядро рендерит форму по `FIELDS` и кладёт
введённое в `ctx.field(<имя>)`. Ядро не знает ни про DSN, ни про какие-либо другие
поля конкретных плагинов.

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


def field(
    name: str,
    label: str,
    placeholder: str = "",
    secret: bool = False,
    required: bool = True,
    hint: str = "",
) -> dict:
    """Поле формы установки. Ядро отрендерит его в админке и вернёт в ctx.field(name).
    secret=True — ввод скрыт, значение вырезается из чеклиста и нигде не сохраняется."""
    return {
        "name": name,
        "label": label,
        "placeholder": placeholder,
        "secret": bool(secret),
        "required": bool(required),
        "hint": hint,
    }


class Ctx:
    """Инструменты плагина на время установки. Сервис привилегированный (root),
    поэтому apt/docker/клиенты зовутся напрямую."""

    def __init__(self, values: dict | None = None, secrets_in: list | None = None):
        self._values = dict(values or {})
        self.creds: dict[str, str] = {}  # что получит агент при On
        # что нельзя показывать/сохранять: введённые секреты + сгенерированные
        self.secrets: list[str] = [s for s in (secrets_in or []) if s]

    def field(self, name: str, default: str = "") -> str:
        """Значение поля формы (см. FIELDS плагина). Вводится один раз при Install."""
        return str(self._values.get(name) or default).strip()

    def sh(
        self,
        argv: list[str],
        env: dict | None = None,
        timeout: int = 30,
        input_text: str | None = None,
    ):
        """Запустить команду. Секреты — через env или input_text, не через argv
        (argv виден в ps). Вывод возвращается уже без известных секретов: плагины
        его усекают, а усечённый секрет редакцией потом не поймать."""
        # своё окружение целиком не отдаём: в нём админ-токен и токен модели
        base = {k: os.environ.get(k, "") for k in ("PATH", "HOME", "LANG")}
        try:
            p = subprocess.run(
                argv,
                env={**base, **(env or {})},
                capture_output=True,
                text=True,
                timeout=timeout,
                input=input_text,
            )
        except (OSError, subprocess.SubprocessError) as e:
            return 1, "", self.redact(str(e))
        return p.returncode, self.redact(p.stdout), self.redact(p.stderr)

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
        """Пароль для создаваемой read-only роли. Запоминается как секрет — не попадёт
        ни в чеклист, ни в UI."""
        pw = secrets.token_hex(24)
        self.secrets.append(pw)
        return pw

    def redact(self, text: str) -> str:
        """Вырезать известные секреты из строки. Режется точное совпадение — если
        плагин выводит производное значение (например распарсенный пароль), пусть
        сам добавит его в ctx.secrets."""
        for s in self.secrets:
            if s:
                text = text.replace(s, "***")
        return text


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
