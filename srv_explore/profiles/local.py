"""Профиль local — базовая read-оболочка. FALLBACK: ловит команды без своего профиля.

Свобода базы: широкий READ_COMMANDS (обычные read-утилиты). Покомандные write-флаг-
стражи (sort -o, find -exec, date -s, tail -f, ss -K…) — в движке local (g.read_util).
Серверные бинари (nginx/ufw/crontab/systemctl) — декларативно, через g.verbs.
Команда не отсюда и не из другого профиля → deny (default-deny).
"""

ID = "shell"
COMMANDS: list[str] = []  # fallback — владеет всем, что не забрал другой профиль
DESC = "базовая read-оболочка (cat/ls/grep/…, серверные бинари)"
FALLBACK = True

READ_COMMANDS = [
    "cat",
    "ls",
    "grep",
    "egrep",
    "fgrep",
    "head",
    "tail",
    "wc",
    "sort",
    "uniq",
    "cut",
    "tr",
    "jq",
    "yq",
    "stat",
    "date",
    "echo",
    "printf",
    "column",
    "nl",
    "tac",
    "comm",
    "diff",
    "df",
    "free",
    "uptime",
    "whoami",
    "id",
    "ps",
    "pgrep",
    "journalctl",
    "ss",
    "netstat",
    "lsof",
    "du",
    "tree",
    "readlink",
    "realpath",
    "file",
    "zcat",
    "uname",
    "which",
    "basename",
    "dirname",
    "nproc",
    "arch",
    "lscpu",
    "lsblk",
    "lsmem",
    "getconf",
    "lsb_release",
    "dpkg-query",
]

# Серверные бинари: kwargs для g.verbs (read-глаголы / whitelist флагов).
COMMAND_RULES = {
    "nginx": {"allow_flags": ["-v", "-V", "-t", "-T"]},
    "ufw": {"allow": ["status", "show", "version"]},
    "crontab": {
        "allow_flags": ["-l", "-u"],
        "value_flags": ["-u"],
        "require_flag": "-l",
    },
}

_SYSTEMCTL_READS = [
    "status",
    "is-active",
    "is-enabled",
    "is-failed",
    "list-units",
    "list-unit-files",
    "list-timers",
    "list-sockets",
    "list-dependencies",
    "show",
    "cat",
    "get-default",
    "help",
]
_SYSTEMCTL_VALUE_FLAGS = [
    "-H",
    "--host",
    "-M",
    "--machine",
    "-t",
    "--type",
    "--state",
    "-p",
    "--property",
    "--job-mode",
    "--kill-whom",
    "--signal",
]


def check(argv, g):
    name = g.name(argv)
    if name == "systemctl":
        return g.verbs(
            argv,
            allow=_SYSTEMCTL_READS,
            value_flags=_SYSTEMCTL_VALUE_FLAGS,
            allow_bare=True,
        )
    rule = COMMAND_RULES.get(name)
    if rule:
        return g.verbs(argv, **rule)
    return g.read_util(argv, READ_COMMANDS)
