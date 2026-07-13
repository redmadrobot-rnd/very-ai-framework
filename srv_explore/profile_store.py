"""Тумблеры профилей: profiles.json в StateDir, гард читает на каждый вызов.

Реестр профилей (id + описание) не зашит — берётся из guard.registry() (модули
profiles/*.py), тот же источник, что и у гарда. Новый профиль = новый файл.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from srv_explore import guard


def store_path() -> Path:
    return Path(guard.STATE)


def registry() -> dict[str, str]:
    """{id: desc} из профилей-модулей — единый список для админки."""
    reg = guard.registry()
    return {pid: getattr(mod, "DESC", "") for pid, mod in reg["mod_of"].items()}


def load() -> dict[str, bool]:
    try:
        data = json.loads(store_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    return {pid: bool(data.get(pid, True)) for pid in registry()}


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
