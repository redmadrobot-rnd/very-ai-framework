#!/usr/bin/env python3
"""srv-explore — PreToolUse-гигиена. Универсальный, без доменных знаний и профилей.

Read-only держит РЕСУРС-СЛОЙ (RO-FS, egress-firewall, unprivileged-юзер, read-only
роли БД, docker-socket-proxy) — см. DESIGN.md. Гард команды НЕ регулирует по существу;
это дешёвая гигиена + бэкстоп:
  - метасимволы записи/подстановки/цепочки (`>`/`;`/`&`/`$()`) — понятный deny вместо
    EROFS-крэша и страховка, если RO-FS кто-то не включил;
  - чтение спецфайлов /dev/* (сырой диск/бесконечный источник).
Всё прочее — allow (разрулит ресурс-слой). PreToolUse-JSON на stdin, exit 0/2.
"""

from __future__ import annotations

import json
import shlex
import sys

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

# Метасимволы записи/подстановки/цепочки; пайп (|) разрешён (read-пайплайны).
DANGEROUS = ["`", "$(", ">", "<", ";", "&", "\n", "\r"]
SAFE_DEV = {"/dev/null", "/dev/stdin", "/dev/stdout", "/dev/stderr", "/dev/tty"}


def forbidden_path(tok: str) -> bool:
    p = tok.split("=", 1)[-1].strip("\"'") if "=" in tok else tok
    if p in SAFE_DEV or p.startswith("/dev/fd/"):
        return False
    return p.startswith("/dev/") or p.startswith("/proc/kcore")


def check_command_string(command: str) -> tuple[bool, str]:
    for m in DANGEROUS:
        if m in command:
            return False, f"метасимвол {m!r}: запись/подстановка/цепочка — read-only"
    for stage in command.split("|"):
        stage = stage.strip()
        if not stage:
            return False, "пустой сегмент пайпа"
        try:
            argv = shlex.split(stage, posix=True)
        except ValueError as e:
            return False, f"не удалось разобрать команду: {e}"
        for tok in argv:
            if forbidden_path(tok):
                return False, f"чтение спецфайла {tok} запрещено (устройство/бесконечный источник)"
    return True, "read-only ok"


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
    print(f"srv-explore guard: заблокировано — {reason}. Переформулируй как чтение.", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
