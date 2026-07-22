"""Контрактные тесты гарда-гигиены srv-explore.

Гард регулирует read-only НЕ по существу (это ресурс-слой: RO-FS/firewall/роли БД) —
он лишь гигиена: метасимволы записи/подстановки/цепочки и чтение спецфайлов /dev/*.
Всё прочее — allow. Гоняем как рантайм: PreToolUse-JSON на stdin, exit 0/2.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

GUARD = Path(__file__).resolve().parents[1] / "srv_explore" / "guard.py"


def run_guard(command: str, tmp_path: Path) -> tuple[int, dict]:
    payload = json.dumps(
        {"tool_name": "Bash", "tool_input": {"command": command}, "session_id": "test"}
    )
    proc = subprocess.run(
        [sys.executable, str(GUARD)],
        input=payload,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={**dict(os.environ), "PYTHONUTF8": "1"},
    )
    try:
        decision = json.loads(proc.stdout) if proc.stdout.strip() else {}
    except ValueError:
        decision = {}
    return proc.returncode, decision


# allow: read-only держит ресурс-слой, гард пропускает почти всё.
ALLOW = [
    "cat /var/log/app.log",
    "ls -la /etc",
    "grep -i error app.log",
    "tail -n 100 /var/log/app.log",
    "ps aux | grep nginx",
    "docker logs --tail 200 web | grep ERROR",
    "journalctl -u nginx --since today",
    "systemctl status nginx",
    # инструменты, которые раньше резал парсер — теперь permissive (держит ресурс-слой)
    'psql -c "SELECT * FROM users LIMIT 10"',
    "docker ps -a",
    "docker exec web cat /app/config.yml",
    "redis-cli INFO",
    "curl -s http://localhost:8080/health",
    "cat /dev/null",
]

# deny: только гигиена — метасимволы и спецфайлы.
DENY = [
    # метасимволы записи/подстановки/цепочки
    "cat x > /etc/passwd",
    "cat x >> /etc/passwd",
    "echo hi; rm x",
    "cat a && rm b",
    "cat $(whoami)",
    "cat `whoami`",
    "psql < /tmp/script.sql",
    # спецфайлы: сырой диск / бесконечный источник
    "cat /dev/sda",
    "cat /dev/urandom",
    "docker exec web cat /dev/zero",
    "head /proc/kcore",
]


@pytest.mark.parametrize("command", ALLOW)
def test_allow(command: str, tmp_path: Path) -> None:
    code, decision = run_guard(command, tmp_path)
    assert code == 0, f"должно быть allow: {command}"
    assert decision.get("hookSpecificOutput", {}).get("permissionDecision") == "allow"


@pytest.mark.parametrize("command", DENY)
def test_deny(command: str, tmp_path: Path) -> None:
    code, decision = run_guard(command, tmp_path)
    assert code == 2, f"должно быть deny: {command}"
    assert decision.get("hookSpecificOutput", {}).get("permissionDecision") == "deny"


def test_non_bash_passthrough(tmp_path: Path) -> None:
    payload = json.dumps(
        {"tool_name": "Read", "tool_input": {"file_path": "/etc/hosts"}}
    )
    proc = subprocess.run(
        [sys.executable, str(GUARD)],
        input=payload,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={**dict(os.environ), "PYTHONUTF8": "1"},
    )
    assert proc.returncode == 0


def test_empty_command_denied(tmp_path: Path) -> None:
    code, _ = run_guard("   ", tmp_path)
    assert code == 2
