"""Тумблеры плагинов гарда: plugins.json в StateDir, гард читает на каждый вызов."""

from __future__ import annotations

import json
import os
from pathlib import Path

DEFAULT_STORE = "/var/lib/srv-explore/plugins.json"

PLUGINS = {
    "docker": "docker / docker-compose (read-подкоманды, exec с read-командой)",
    "postgres": "psql (одна read-инструкция за запрос)",
    "mysql": "mysql (одна read-инструкция за запрос)",
    "clickhouse": "clickhouse-client (одна read-инструкция за запрос)",
    "mongo": "mongosh --eval (read-методы, без $out/$merge/runCommand)",
    "redis": "redis-cli (read-глаголы, CONFIG только GET)",
    "rabbitmq": "rabbitmqctl (list_*/status/cluster_status)",
    "http": "curl (только GET/HEAD, внутренняя сеть; внешнее по allowlist)",
    "ssh": "ssh (удалённая команда рекурсивно через гард)",
}


def store_path() -> Path:
    return Path(os.environ.get("SRV_EXPLORE_PLUGINS", DEFAULT_STORE))


def load() -> dict[str, bool]:
    try:
        data = json.loads(store_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    return {name: bool(data.get(name, True)) for name in PLUGINS}


def set_enabled(name: str, enabled: bool) -> dict[str, bool]:
    if name not in PLUGINS:
        raise KeyError(name)
    state = load()
    state[name] = enabled
    path = store_path()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)
    return state
