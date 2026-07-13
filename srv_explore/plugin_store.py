"""Тумблеры плагинов гарда: plugins.json в StateDir, гард читает на каждый вызов.

Реестр плагинов (имя + описание) НЕ зашит — берётся из profiles/*.json (файлы с полем
`plugin`), тот же источник, что и у гарда. Новый плагин = новый профиль, код не трогаем.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

DEFAULT_STORE = "/var/lib/srv-explore/plugins.json"
PROFILES = Path(__file__).resolve().parent / "profiles"


def store_path() -> Path:
    return Path(os.environ.get("SRV_EXPLORE_PLUGINS", DEFAULT_STORE))


def registry() -> dict[str, str]:
    """{plugin: desc} из профилей — единый декларативный список."""
    out: dict[str, str] = {}
    profiles_dir = Path(os.environ.get("SRV_EXPLORE_PROFILES", str(PROFILES)))
    for f in sorted(profiles_dir.glob("*.json")):
        try:
            prof = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if prof.get("plugin"):
            out[prof["plugin"]] = prof.get("desc", "")
    return out


def load() -> dict[str, bool]:
    try:
        data = json.loads(store_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    return {name: bool(data.get(name, True)) for name in registry()}


def set_enabled(name: str, enabled: bool) -> dict[str, bool]:
    if name not in registry():
        raise KeyError(name)
    state = load()
    state[name] = enabled
    path = store_path()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)
    return state
