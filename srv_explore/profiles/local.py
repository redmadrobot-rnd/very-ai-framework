"""Профиль shell — базовая read-оболочка. FALLBACK, единственный профиль ядра.

Гарантия против записи — read-only FS (systemd ProtectSystem=strict); гард её НЕ
дублирует. Режет лишь то, что RO-FS не ловит: подвисание (стрим), не-FS сайд-эффекты
(смена времени/сети) и побег из allowlist (find -exec запускает любой бинарь).
Серверные бинари (nginx/ufw/crontab/systemctl) — декларативно через g.verbs.
Не покрыто включённым профилем → deny.
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

# Флаги, которые RO-FS НЕ ловит: смена часов/сети, бесконечный стрим.
_DENY_FLAGS = {
    "date": {"-s", "--set"},  # системные часы
    "ss": {"-K", "--kill"},  # рвёт коннекты
    "netstat": {"-c", "--continuous"},  # бесконечный стрим
}

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


def _read_util(argv, g):
    name = g.name(argv)
    if name not in READ_COMMANDS:
        return False, f"'{name}' не покрыта включённым профилем — deny (default-deny)"
    deny = _DENY_FLAGS.get(name, set())
    for a in argv[1:]:
        if a.split("=", 1)[0] in deny:
            return False, f"{name} {a.split('=', 1)[0]}: не read — запрещено"
    if name == "tail" and g.follows(argv):
        return False, "tail -f стримит бесконечно — запрещено"
    if name == "journalctl":
        for a in argv[1:]:
            # -f/--follow (в т.ч. слитно -fu) стримит; -F/--field — read, ок
            if (
                a in ("-f", "--follow")
                or a.startswith("--follow=")
                or (a.startswith("-") and not a.startswith("--") and "f" in a[1:])
            ):
                return False, "journalctl -f стримит бесконечно — запрещено"
    return True, f"{name} (read)"


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
    return _read_util(argv, g)
