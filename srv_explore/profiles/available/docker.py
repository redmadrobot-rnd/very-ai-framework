"""Профиль docker/docker-compose — read-подкоманды. Опциональный.
noun-формы (system/image/…) — read-глаголы; docker exec — вложенная команда
рекурсивно (g.recurse); logs -f / stats без --no-stream — блок как зависание.
"""

ID = "docker"
COMMANDS = ["docker", "docker-compose"]
DESC = "docker / docker-compose (read-подкоманды, exec с read-командой)"

_READS = [
    "ps",
    "inspect",
    "logs",
    "images",
    "stats",
    "top",
    "port",
    "version",
    "info",
    "df",
    "history",
]
_NOUN_READS = {
    "system": ["df", "info"],
    "image": ["ls", "inspect", "history"],
    "container": ["ls", "inspect", "port", "diff"],
    "volume": ["ls", "inspect"],
    "network": ["ls", "inspect"],
    "context": ["ls", "inspect", "show"],
    "node": ["ls", "inspect"],
    "config": ["ls", "inspect"],
}
_COMPOSE_READS = ["ps", "logs", "config", "top", "images", "version"]

_GLOBAL_VALUE = {
    "-H",
    "--host",
    "--context",
    "--config",
    "-l",
    "--log-level",
    "--tlscacert",
    "--tlscert",
    "--tlskey",
}
_EXEC_VALUE = {
    "-e",
    "--env",
    "--env-file",
    "-u",
    "--user",
    "-w",
    "--workdir",
    "--detach-keys",
}
_EXEC_DENY = {"--privileged", "-d", "--detach"}
_COMPOSE_GLOBAL_VALUE = {
    "-f",
    "--file",
    "-p",
    "--project-name",
    "--project-directory",
    "--env-file",
    "--profile",
    "-c",
    "--context",
    "-H",
    "--host",
}


def _first_pos(rest):
    for t in rest:
        if not t.startswith("-"):
            return t
    return None


def _exec(rest, g):
    i = 0
    while i < len(rest):
        a = rest[i]
        if a.split("=", 1)[0] in _EXEC_DENY:
            return False, f"docker exec {a.split('=', 1)[0]}: не read — запрещено"
        if a in _EXEC_VALUE:
            i += 2
            continue
        if a.startswith("-"):
            i += 1
            continue
        break
    if i >= len(rest):
        return False, "docker exec: нет контейнера"
    nested = rest[i + 1 :]
    if not nested:
        return False, "docker exec: нет вложенной команды"
    return g.recurse(nested)


def _compose_check(tokens, g, label):
    sub, rest = g.subcommand(tokens, value_flags=_COMPOSE_GLOBAL_VALUE)
    if sub is None:
        return False, f"{label}: нужна read-подкоманда"
    low = sub.lower()
    if low not in _COMPOSE_READS:
        return False, f"{label} {sub}: не read (разрешено: {', '.join(_COMPOSE_READS)})"
    if low == "logs" and g.follows(rest):
        return False, f"{label} logs -f стримит — запрещено"
    return True, f"{label} {sub} (read)"


def check(argv, g):
    name = g.name(argv)
    if name == "docker-compose":
        return _compose_check(argv, g, "docker-compose")

    sub, rest = g.subcommand(argv, value_flags=_GLOBAL_VALUE)
    if sub is None:
        if any(a in ("-v", "--version", "--help", "-h") for a in argv[1:]):
            return True, "docker (version/help)"
        return False, "docker: нужна read-подкоманда"
    low = sub.lower()
    if low == "compose":
        return _compose_check(["compose"] + rest, g, "docker compose")
    if low == "exec":
        return _exec(rest, g)
    if low == "logs":
        if g.follows(argv):
            return False, "docker logs -f стримит — запрещено"
        return True, "docker logs (read)"
    if low == "stats":
        if "--no-stream" not in rest:
            return False, "docker stats без --no-stream висит — добавь --no-stream"
        return True, "docker stats --no-stream (read)"
    if low in _NOUN_READS:
        ok_verbs = ", ".join(_NOUN_READS[low])
        verb = _first_pos(rest)
        if verb is None:
            return False, f"docker {low}: нужна read-подкоманда ({ok_verbs})"
        if verb.lower() not in _NOUN_READS[low]:
            return False, f"docker {low} {verb}: не read (разрешено: {ok_verbs})"
        return True, f"docker {low} {verb} (read)"
    if low in _READS:
        return True, f"docker {low} (read)"
    return False, f"docker {sub}: не read-подкоманда"
