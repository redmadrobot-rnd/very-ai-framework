"""Контрактные тесты гарда-гигиены srv-explore.

Гард регулирует read-only НЕ по существу (это ресурс-слой: RO-FS/firewall/роли БД) —
он лишь гигиена: метасимволы записи/подстановки/цепочки и чтение спецфайлов /dev/*.
Всё прочее — allow. Зовётся in-process (agent_worker) — тестируем функцию напрямую.
"""

from __future__ import annotations

import pytest

from srv_explore.guard import check_command_string

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
def test_allow(command: str) -> None:
    ok, reason = check_command_string(command)
    assert ok, f"должно быть allow: {command} ({reason})"


@pytest.mark.parametrize("command", DENY)
def test_deny(command: str) -> None:
    ok, _ = check_command_string(command)
    assert not ok, f"должно быть deny: {command}"


def test_empty_command_denied() -> None:
    ok, _ = check_command_string("   ")
    assert not ok
