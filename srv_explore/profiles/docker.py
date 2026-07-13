"""Профиль docker/docker-compose — read-подкоманды. Движок docker (g.docker).

docker несовместим с OS-песочницей (демон вне namespace) — держится на гарде.
verb-dispatch: read-подкоманды; noun-формы (system/image/…) — read-глаголы;
docker exec <read-команда> — вложенная команда рекурсивно через весь гард;
logs -f / stats без --no-stream — блок как зависание.
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


def check(argv, g):
    return g.docker(
        argv, reads=_READS, noun_reads=_NOUN_READS, compose_reads=_COMPOSE_READS
    )
