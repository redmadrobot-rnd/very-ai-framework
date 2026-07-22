"""Профиль docker — конфиг подготовки, НЕ парсер команд.
Read-only держит docker-socket-proxy: клиент бьёт в прокси, не в реальный сокет.
"""

ID = "docker"
DESC = "Docker — read-only через socket-proxy"
COMMANDS = ["docker", "docker-compose"]
PACKAGES = []  # docker CLI уже на docker-хосте; ставить нечего
CREDS_ENV = "DOCKER_HOST"  # провизионер укажет на прокси

# Прокси перед /var/run/docker.sock: read-эндпоинты on, мутации (POST) off.
# Агент ходит в него по DOCKER_HOST, к реальному сокету доступа нет.
PROXY = {
    "image": "tecnativa/docker-socket-proxy:latest",
    "port": "127.0.0.1:2375:2375",
    "env": {
        "CONTAINERS": "1",
        "IMAGES": "1",
        "NETWORKS": "1",
        "VOLUMES": "1",
        "INFO": "1",
        "PING": "1",
        "VERSION": "1",
        "POST": "0",
    },
    "sets": {"DOCKER_HOST": "tcp://127.0.0.1:2375"},
}
