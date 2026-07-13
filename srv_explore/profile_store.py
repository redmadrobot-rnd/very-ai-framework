"""Профили = тонкие конфиги (profiles/*.py) + тумблеры в profiles.json (StateDir).

Реестр (id + описание) сканится из модулей — не зашит. Профиль default-OFF: включается
в админке. Гард профили не грузит; их читают админка и провизионер.
"""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROFILES_DIR = Path(os.environ.get("SRV_EXPLORE_PROFILES_DIR", str(HERE / "profiles")))
STATE = os.environ.get(
    "SRV_EXPLORE_PROFILE_STATE", "/var/lib/srv-explore/profiles.json"
)

_cache: dict | None = None


def _load() -> dict:
    mods: dict = {}
    for f in sorted(PROFILES_DIR.glob("*.py")):
        if f.name.startswith("_"):
            continue
        spec = importlib.util.spec_from_file_location(f"srvx_profile_{f.stem}", f)
        if not spec or not spec.loader:
            continue
        mod = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(mod)
        except Exception:  # noqa: BLE001
            continue
        pid = getattr(mod, "ID", None)
        if pid:
            mods[pid] = mod
    return mods


def modules() -> dict:
    global _cache
    if _cache is None:
        _cache = _load()
    return _cache


def registry() -> dict[str, str]:
    """{id: desc} из профилей-конфигов — список для админки."""
    return {pid: getattr(mod, "DESC", "") for pid, mod in modules().items()}


def store_path() -> Path:
    return Path(STATE)


def load() -> dict[str, bool]:
    try:
        data = json.loads(store_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    return {pid: bool(data.get(pid, False)) for pid in registry()}


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
