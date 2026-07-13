#!/usr/bin/env python3
"""srv-explore — PreToolUse-гард. Универсальный движок, БЕЗ доменных знаний.

Читает PreToolUse-JSON со stdin: allow → exit 0, deny → exit 2. Fail-closed.
Ядро знает только инварианты (метасимволы/пайпы/спецфайлы), загрузку профилей
profiles/*.py и диспатч к владельцу; доменная логика — в профилях. Формат —
profiles/FORMAT.md, шаблон — profiles/profile.py.example.
"""

from __future__ import annotations

import importlib.util
import ipaddress
import json
import os
import re
import shlex
import sys
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

HERE = Path(__file__).resolve().parent
_DIRS = os.environ.get("SRV_EXPLORE_PROFILES_DIR", str(HERE / "profiles"))
PROFILE_DIRS = [Path(p) for p in _DIRS.split(os.pathsep) if p]
STATE = os.environ.get("SRV_EXPLORE_PROFILE_STATE", "/var/lib/srv-explore/profiles.json")

# Метасимволы записи/подстановки/цепочки; пайп (|) разрешён (read-пайплайны).
DANGEROUS = ["`", "$(", ">", "<", ";", "&", "\n", "\r"]
SAFE_DEV = {"/dev/null", "/dev/stdin", "/dev/stdout", "/dev/stderr", "/dev/tty"}


# --- универсальные примитивы разбора ------------------------------------------

def forbidden_path(tok: str) -> bool:
    p = tok.split("=", 1)[-1].strip("\"'") if "=" in tok else tok
    if p in SAFE_DEV or p.startswith("/dev/fd/"):
        return False
    return p.startswith("/dev/") or p.startswith("/proc/kcore")


def _subcommand(args: list[str], value_flags: set[str]) -> tuple[str | None, list[str]]:
    """Первый позиционный токен и хвост."""
    i = 0
    while i < len(args):
        a = args[i]
        if a in value_flags:
            i += 2
            continue
        if a.startswith("--") and "=" in a:
            i += 1
            continue
        if a.startswith("-"):
            i += 1
            continue
        return a, args[i + 1:]
    return None, []


def _follows(argv: list[str]) -> bool:
    """-f / -F / --follow / слитные короткие — стриминг."""
    for a in argv:
        if a in ("-f", "-F", "--follow") or a.startswith("--follow="):
            return True
        if a.startswith("-") and not a.startswith("--") and "f" in a[1:]:
            return True
    return False


def _values(argv, take_flags: set[str], file_flags: set[str]) -> tuple[list[str] | None, str]:
    """Значения take-флагов (`-c x`, `--x=y`, слитно `-cSELECT`); file_flag → ошибка."""
    vals, i = [], 1
    while i < len(argv):
        a = argv[i]
        if a in file_flags or any(
            a.startswith(f + "=") or (not f.startswith("--") and a.startswith(f) and len(a) > len(f))
            for f in file_flags
        ):
            return None, "скрипт/файл-инструкция не проверяется гардом — запрещено"
        if a in take_flags:
            if i + 1 >= len(argv):
                return None, f"{a} без аргумента"
            vals.append(argv[i + 1])
            i += 2
            continue
        matched = False
        for f in take_flags:
            if a.startswith(f + "="):
                vals.append(a.split("=", 1)[1]); matched = True; break
            if not f.startswith("--") and a.startswith(f) and len(a) > 2:
                vals.append(a[len(f):]); matched = True; break
        i += 1 if not matched else 1
    return vals, ""


def _forbid_words(text: str, words) -> str | None:
    for kw in words:
        if re.search(rf"\b{re.escape(kw)}\b", text, re.I):
            return kw
    return None


def _forbid_substr(text: str, subs) -> str | None:
    low = text.lower()
    for kw in subs:
        if kw.lower() in low:
            return kw
    return None


def _url_host(url: str) -> str:
    u = url.split("://", 1)[-1].split("/", 1)[0].split("?", 1)[0]
    if "@" in u:
        u = u.rsplit("@", 1)[1]
    if u.startswith("["):
        return u[1:u.find("]")].lower() if "]" in u else ""
    return u.split(":", 1)[0].lower()


def _internal_host(host: str) -> bool:
    if not host:
        return False
    try:
        ip = ipaddress.ip_address(host)
        return ip.is_private or ip.is_loopback or ip.is_link_local
    except ValueError:
        return "." not in host


# --- generic verb+flag движок (данные-driven) --------------------------------

def _verbs(argv, *, allow=(), subreads=None, value_flags=(), deny_flags=(),
          allow_flags=None, require_flag=None, allow_bare=False,
          no_follow=False) -> tuple[bool, str]:
    name = os.path.basename(argv[0])
    rest = argv[1:]
    value_flags, deny_flags = set(value_flags), set(deny_flags)
    allow_up = {v.upper() for v in allow}
    subreads = {k.upper(): {s.upper() for s in v} for k, v in (subreads or {}).items()}
    seen_flags, positionals, i = [], [], 0
    while i < len(rest):
        a = rest[i]
        base = a.split("=", 1)[0]
        if base in deny_flags:
            return False, f"{name} {base}: запрещён (не read-only)"
        if no_follow and (a in ("-f", "-F", "--follow") or a.startswith("--follow=")
                          or (a.startswith("-") and not a.startswith("--") and "f" in a[1:])):
            return False, f"{name} -f стримит бесконечно — запрещено"
        if a in value_flags:
            i += 2
            continue
        if a.startswith("--") and "=" in a:
            i += 1
            continue
        if a.startswith("-"):
            if allow_flags is not None and base not in allow_flags:
                return False, f"{name}: разрешены только флаги {', '.join(allow_flags)}"
            seen_flags.append(base)
            i += 1
            continue
        positionals.append(a)
        i += 1

    if allow_up or subreads:
        if not positionals:
            if allow_bare:
                return True, f"{name} (read)"
            return False, f"{name}: нужна read-команда/подкоманда"
        verb = positionals[0].upper()
        if verb in subreads:
            if len(positionals) < 2:
                return False, f"{name} {verb}: нужна подкоманда ({', '.join(sorted(subreads[verb]))})"
            sub = positionals[1].upper()
            if sub not in subreads[verb]:
                return False, f"{name} {verb} {positionals[1]}: не read (разрешено: {', '.join(sorted(subreads[verb]))})"
            return True, f"{name} {verb} {sub} (read)"
        if verb in allow_up:
            return True, f"{name} {verb} (read)"
        return False, f"{name} {positionals[0]}: не read-only команда"

    if positionals:
        return False, f"{name}: неожиданный аргумент {positionals[0]!r} (только чтение)"
    if allow_flags is not None:
        if not seen_flags:
            return False, f"{name}: нужен read-флаг ({', '.join(allow_flags)})"
        if require_flag and require_flag not in seen_flags:
            return False, f"{name}: обязателен {require_flag}"
    return True, f"{name} (read)"


# --- тулкит g: универсальные примитивы для profile.check(argv, g) ------------

class Toolkit:
    """Хелперы ядра для профилей. Доменных знаний не несёт."""

    def __init__(self, depth: int):
        self.depth = depth

    def name(self, argv):
        return os.path.basename(argv[0])

    def subcommand(self, argv, value_flags=()):
        return _subcommand(argv[1:], set(value_flags))

    def verbs(self, argv, **kw):
        return _verbs(argv, **kw)

    def values(self, argv, flags, file_flags=()):
        return _values(argv, set(flags), set(file_flags))

    def forbid_words(self, text, words):
        return _forbid_words(text, words)

    def forbid_substr(self, text, subs):
        return _forbid_substr(text, subs)

    def follows(self, argv):
        return _follows(argv)

    def url_host(self, url):
        return _url_host(url)

    def internal_host(self, host):
        return _internal_host(host)

    def recurse(self, argv):
        return dispatch(argv, self.depth + 1)


# --- загрузка профилей-модулей -----------------------------------------------

_registry: dict | None = None


def _load_profiles() -> dict:
    cmd_of, mod_of, fallback = {}, {}, None
    for d in PROFILE_DIRS:
        for f in sorted(d.glob("*.py")):
            if f.name.startswith("_"):
                continue
            spec = importlib.util.spec_from_file_location(f"srvx_profile_{f.stem}", f)
            if not spec or not spec.loader:
                continue
            mod = importlib.util.module_from_spec(spec)
            try:
                spec.loader.exec_module(mod)
            except Exception:  # noqa: BLE001
                continue
            pid = getattr(mod, "ID", None)
            if not pid or not callable(getattr(mod, "check", None)):
                continue
            mod_of[pid] = mod
            if getattr(mod, "FALLBACK", False):
                fallback = mod
            for cmd in getattr(mod, "COMMANDS", []):
                cmd_of[cmd] = pid
    return {"cmd_of": cmd_of, "mod_of": mod_of, "fallback": fallback}


def registry() -> dict:
    global _registry
    if _registry is None:
        _registry = _load_profiles()
    return _registry


def profile_enabled(pid: str) -> bool:
    # Свежее чтение: тумблер админки без рестарта.
    try:
        toggles = json.loads(Path(STATE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        toggles = {}
    return bool(toggles.get(pid, True))


# --- диспетчер ---------------------------------------------------------------

def dispatch(argv: list[str], depth: int) -> tuple[bool, str]:
    if not argv:
        return False, "пустая команда"
    for tok in argv[1:]:
        if forbidden_path(tok):
            return False, f"чтение спецфайла {tok} запрещено (сырое устройство/бесконечный источник)"
    reg = registry()
    name = os.path.basename(argv[0])
    pid = reg["cmd_of"].get(name)
    g = Toolkit(depth)
    if pid:
        if not profile_enabled(pid):
            return False, f"профиль '{pid}' выключен — {name} запрещён"
        return reg["mod_of"][pid].check(argv, g)
    fb = reg["fallback"]
    if fb is None:
        return False, f"команда '{name}' не покрыта ни одним профилем"
    if not profile_enabled(fb.ID):
        return False, f"профиль '{fb.ID}' (базовая оболочка) выключен — {name} запрещён"
    return fb.check(argv, g)


def check_command_string(command: str, depth: int = 0) -> tuple[bool, str]:
    if depth > 4:
        return False, "слишком глубокая вложенность команд"
    for m in DANGEROUS:
        if m in command:
            return False, f"запрещённый метасимвол: {m!r} (запись/подстановка/цепочка)"
    for stage in command.split("|"):
        stage = stage.strip()
        if not stage:
            return False, "пустой сегмент пайпа"
        try:
            argv = shlex.split(stage, posix=True)
        except ValueError as e:
            return False, f"не удалось разобрать команду: {e}"
        ok, reason = dispatch(argv, depth)
        if not ok:
            return ok, reason
    return True, "read-only pipeline"


# --- hook I/O ----------------------------------------------------------------

def emit(decision: str, reason: str) -> None:
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": decision,
            "permissionDecisionReason": reason,
        }
    }, ensure_ascii=False))


def main() -> int:
    raw = sys.stdin.read()
    try:
        payload = json.loads(raw)
    except ValueError:
        print("srv-explore guard: не разобран PreToolUse JSON — блок (fail-closed)", file=sys.stderr)
        return 2
    if payload.get("tool_name") != "Bash":
        return 0
    command = (payload.get("tool_input") or {}).get("command", "")
    if not command.strip():
        print("srv-explore guard: пустая команда", file=sys.stderr)
        return 2
    ok, reason = check_command_string(command)
    if ok:
        emit("allow", reason)
        return 0
    emit("deny", reason)
    print(
        f"srv-explore guard: заблокировано — {reason}. Разрешено только чтение. "
        f"Не обходи (sh -c/base64/файлы) — переформулируй как чтение или предложи действие текстом.",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    sys.exit(main())
