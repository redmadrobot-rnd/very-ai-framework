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
import tempfile
from pathlib import Path

AGENT_USER = "srvx-agent"  # заводит install.sh
MAX_SEC = os.environ.get("SRV_EXPLORE_AGENT_MAX_SEC", "600")  # анти-подвисание
# Жёсткий потолок вывода: `yes` или дамп гигабайтного лога иначе копит в память до
# RuntimeMaxSec, прежде чем mcp_server срежет до 30 КБ. Читаем с капом и убиваем юнит.
MAX_OUTPUT_BYTES = int(os.environ.get("SRV_EXPLORE_MAX_OUTPUT_BYTES", str(1_000_000)))
PROXY = os.environ.get("SRV_EXPLORE_PROXY", "http://127.0.0.1:3128")
# каталог-родитель пакета srv_explore — чтобы `python -m srv_explore.*` в песочнице
# нашёл пакет независимо от cwd (RO-FS читать не мешает).
_PKG_PARENT = str(Path(__file__).resolve().parent.parent)

# Прямой egress: только loopback + приватные сети + доверенные CIDR (env).
# Явные префиксы, не именованные токены (старый systemd их не парсит).
#
# Link-local (169.254.0.0/16, fe80::/10) НЕ разрешаем: там живёт cloud metadata
# 169.254.169.254, а это ключи инстанса в обход прокси и его allowlist.
_ALLOW_BASE = "127.0.0.0/8 ::1/128 10.0.0.0/8 172.16.0.0/12 192.168.0.0/16"
_NO_PROXY = "localhost,127.0.0.1,::1,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16"


def _ip_allow() -> str:
    """База + что открыл деплой (env) и админ (настройки). Читается на каждом спавне,
    поэтому правка в админке действует со следующего прогона, без рестарта."""
    from srv_explore import settings

    extra = " ".join(settings.egress_cidrs())
    return _ALLOW_BASE + (" " + extra if extra else "")


_PROPS = [
    "ProtectSystem=strict",
    "ProtectHome=read-only",
    "PrivateTmp=yes",
    "NoNewPrivileges=yes",
    "IPAddressDeny=any",  # внешка обрублена; ниже — что разрешено
]


def props(max_sec: str | None = None) -> list[str]:
    """Свойства юнита песочницы. Кап по времени зависит от вызова: прогон агента
    минуты, одиночная команда — секунды."""
    return [
        *_PROPS,
        f"RuntimeMaxSec={max_sec or MAX_SEC}",
        f"IPAddressAllow={_ip_allow()}",
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


def _expand_secrets(secrets) -> set[str]:
    """Значения + их чувствительные составные части. DSN редактится целиком, но в
    выводе клиент может показать один только пароль — режем и его отдельно."""
    from urllib.parse import unquote, urlsplit

    out = set()
    for s in secrets:
        value = str(s or "")
        if not value:
            continue
        out.add(value)
        if "://" in value:
            pw = urlsplit(value).password
            if pw:
                out.update((pw, unquote(pw)))
    return out


def redact(text: str, secrets) -> str:
    """Затереть значения кредов в выводе.

    Гард запрещает дамп окружения, но обойти его формой команды несложно, а вывод
    уезжает вызывающему целиком. Поэтому значения, которые мы сами положили в
    окружение, из вывода вырезаем — это последний рубеж, а не единственный.
    """
    for value in sorted(_expand_secrets(secrets), key=len, reverse=True):
        if len(value) >= 8:  # короткие значения (порт, имя юзера) — не секрет
            text = text.replace(value, "***")
    return text


def _quote(value) -> str:
    """Значение для systemd EnvironmentFile: в кавычках, с экранированием.

    Переводы строк вырезаются: в EnvironmentFile такое значение разорвалось бы на две
    записи — вторая половина стала бы отдельной переменной. Кред с \\n всё равно битый,
    но пусть ломается он один, а не всё окружение агента.
    """
    text = str(value).replace("\r", "").replace("\n", "")
    escaped = text.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def run(
    args,
    input_text: str | None = None,
    extra_env: dict | None = None,
    on_line=None,
    max_sec: str | None = None,
):
    """Запустить args в песочнице. Вернуть (returncode, stdout, stderr).

    on_line — колбэк на каждую строку stdout по мере её появления: так родитель видит
    ход прогона, а не только финал (и сохраняет собранное, если песочницу прибьёт
    по RuntimeMaxSec).
    """
    env = {"HOME": "/tmp", "PYTHONPATH": _PKG_PARENT, **_PROXY_ENV, **(extra_env or {})}
    # env уходит файлом 0600, а не через --setenv: argv виден в ps любому на хосте,
    # а здесь и токен модели, и DSN включённых плагинов.
    fd, envfile = tempfile.mkstemp(prefix="srvx-env-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            for k, v in env.items():
                f.write(f"{k}={_quote(v)}\n")
        os.chmod(envfile, 0o600)

        cmd = [
            "systemd-run",
            "--pipe",
            "--quiet",
            "--collect",
            "--wait",
            f"--uid={AGENT_USER}",
        ]
        for p in props(max_sec):
            cmd += ["-p", p]
        cmd += ["-p", f"EnvironmentFile={envfile}"]
        cmd += list(args)

        # Всегда Popen с капом по байтам: capture_output буферил бы `yes`/дамп лога
        # в память до RuntimeMaxSec. stderr — во временный файл: чтение одного пайпа
        # может залипнуть на другом.
        with tempfile.TemporaryFile("w+", encoding="utf-8", errors="replace") as errf:
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=errf,
                text=True,
            )
            if input_text is not None:
                proc.stdin.write(input_text)
            proc.stdin.close()
            out: list[str] = []
            total = 0
            capped = False
            for line in proc.stdout:
                total += len(line.encode("utf-8", "replace"))
                if total > MAX_OUTPUT_BYTES:
                    capped = True
                    proc.kill()
                    break
                out.append(line)
                if on_line is not None:
                    on_line(line)
            proc.stdout.close()
            rc = proc.wait()
            errf.seek(0)
            err = errf.read()
            if capped:
                err += f"\n[вывод срезан на {MAX_OUTPUT_BYTES} байт, процесс убит]"
            return rc, "".join(out), err
    finally:
        try:
            os.unlink(envfile)
        except OSError:
            pass
