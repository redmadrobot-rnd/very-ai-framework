"""Профиль docker — конфиг подготовки, НЕ парсер команд.
Read-only держит docker-socket-proxy: клиент бьёт в прокси, не в реальный сокет.
"""

ID = "docker"
DESC = "Docker — read-only через socket-proxy"
COMMANDS = ["docker", "docker-compose"]
PACKAGES = ["docker-cli"]
CREDS_ENV = "DOCKER_HOST"  # провизионер укажет на прокси

# Прокси перед /var/run/docker.sock: read-эндпоинты on, мутации (POST) off.
PROXY = {
    "image": "ghcr.io/tecnativa/docker-socket-proxy",
    "env": {
        "CONTAINERS": "1",
        "IMAGES": "1",
        "NETWORKS": "1",
        "VOLUMES": "1",
        "INFO": "1",
        "POST": "0",
    },
}
