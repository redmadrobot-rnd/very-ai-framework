"""Контрактные тесты гарда-гигиены srv-explore.

Гард регулирует read-only НЕ по существу (это ресурс-слой: RO-FS/firewall/роли БД) —
он лишь гигиена: метасимволы записи/подстановки/цепочки и чтение спецфайлов /dev/*.
Всё прочее — allow. Зовётся in-process (agent_worker) — тестируем функцию напрямую.
"""

from __future__ import annotations

import pytest

from srv_explore.guard import check_command_string, check_file_path

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


def test_env_dump_is_refused():
    """В окружении агента токен модели и креды плагинов."""
    for cmd in (
        "env",
        "printenv PG_INSPECTOR_DSN",
        "env | grep DSN",
        "cat /proc/self/environ",
        "cat /proc/1/environ",
        # обход каталога процесса, а не прямой путь к environ
        "grep -R CLAUDE_CODE_OAUTH_TOKEN /proc/self",
        "grep -rl DSN /proc/1",
        "ls /proc",
    ):
        ok, reason = check_command_string(cmd)
        assert not ok, cmd
    # обычное чтение не задето, включая листовые файлы /proc вне процессов
    assert check_command_string("cat /etc/os-release")[0]
    assert check_command_string("ps -eo pid,args | grep nginx")[0]
    assert check_command_string("cat /proc/meminfo")[0]
    assert check_command_string("cat /proc/loadavg")[0]


def test_file_path_guard_blocks_environ_and_devices():
    """Read/Grep/Glob получают путь, не команду: тот же гард на спецфайлы, иначе
    `Read /proc/self/environ` достал бы окружение воркера мимо Bash-гарда."""
    for path in ("/proc/self/environ", "/proc/1/environ", "/dev/sda", "/dev/mem"):
        ok, _ = check_file_path(path)
        assert not ok, path
    # каталог процесса как цель рекурсивного Grep — тоже deny
    for path in ("/proc/self", "/proc/1", "/proc"):
        assert not check_file_path(path)[0], path
    # обычные пути и безопасные спецфайлы проходят
    for path in (
        "/var/log/app.log",
        "/etc/os-release",
        "/dev/null",
        "/proc/meminfo",
        "",
    ):
        assert check_file_path(path)[0], path
