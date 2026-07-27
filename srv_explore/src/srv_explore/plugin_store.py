"""Состояние плагинов в StateDir. Три независимые вещи:

- `plugins.json`  — тумблер On/Off (выдавать креды агенту или нет), default-OFF;
- `installed.json` — факт установки + чеклист последнего Install;
- `creds.json`     — что плагин выдал агенту (ro-DSN, DOCKER_HOST), по плагинам.

Агент получает креды ТОЛЬКО установленных и включённых плагинов (`active_creds`).
Реестр плагинов сканится из plugins/*.py — имена нигде не зашиты.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
PLUGINS_DIR = HERE / "plugins"
STATE = os.environ.get("SRV_EXPLORE_PLUGIN_STATE", "/var/lib/srv-explore/plugins.json")

_cache: dict | None = None


def _load() -> dict:
    mods: dict = {}
    for f in sorted(PLUGINS_DIR.glob("*.py")):
        if f.name.startswith("_"):
            continue
        spec = importlib.util.spec_from_file_location(f"srvx_plugin_{f.stem}", f)
        if not spec or not spec.loader:
            continue
        mod = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(mod)
        except Exception as e:  # noqa: BLE001 — один битый плагин не роняет остальные
            print(f"plugins: {f.name} не загружен: {e!r}", file=sys.stderr)
            continue
        pid = getattr(mod, "ID", None)
        if not pid:
            print(f"plugins: {f.name} без ID — пропущен", file=sys.stderr)
            continue
        if pid in mods:
            msg = f"plugins: дубль ID {pid!r} в {f.name} — перекрывает"
            print(msg, file=sys.stderr)
        mods[pid] = mod
    return mods


def modules() -> dict:
    global _cache
    if _cache is None:
        _cache = _load()
    return _cache


def registry() -> dict[str, str]:
    """{id: desc} — список плагинов для админки."""
    return {pid: getattr(mod, "DESC", "") for pid, mod in modules().items()}


def store_path() -> Path:
    return Path(STATE)


def _read(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _write(path: Path, data) -> None:
    # 0600: в creds.json лежат ro-DSN. Песочница агента читает /var (RO-FS не
    # прячет), поэтому от неё файлы закрыты правами, а не расположением.
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


# --- тумблер On/Off -------------------------------------------------------------


def load() -> dict[str, bool]:
    data = _read(store_path(), {})
    return {pid: bool(data.get(pid, False)) for pid in registry()}


def set_enabled(name: str, enabled: bool) -> dict[str, bool]:
    if name not in registry():
        raise KeyError(name)
    state = load()
    state[name] = enabled
    _write(store_path(), state)
    return state


# --- установка + чеклист --------------------------------------------------------


def _installed_path() -> Path:
    return store_path().with_name("installed.json")


def installed_all() -> dict[str, dict]:
    return _read(_installed_path(), {})


def set_checklist(pid: str, checklist: list[dict], ok: bool) -> None:
    data = installed_all()
    data[pid] = {
        "ok": ok,
        "checklist": checklist,
        "at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
    }
    _write(_installed_path(), data)


def drop_checklist(pid: str) -> None:
    data = installed_all()
    if data.pop(pid, None) is not None:
        _write(_installed_path(), data)


def is_installed(pid: str) -> bool:
    return bool(installed_all().get(pid, {}).get("ok"))


# --- креды плагинов -------------------------------------------------------------


def _creds_path() -> Path:
    return store_path().with_name("creds.json")


def creds_all() -> dict[str, dict]:
    return _read(_creds_path(), {})


def set_creds(pid: str, creds: dict[str, str]) -> None:
    data = creds_all()
    data[pid] = creds
    _write(_creds_path(), data)


def drop_creds(pid: str) -> None:
    data = creds_all()
    if data.pop(pid, None) is not None:
        _write(_creds_path(), data)


def active_by_plugin() -> dict[str, dict[str, str]]:
    """Креды по плагинам — только установленные И включённые."""
    enabled = load()
    installed = installed_all()
    return {
        pid: creds
        for pid, creds in creds_all().items()
        if enabled.get(pid) and installed.get(pid, {}).get("ok")
    }


def active_creds() -> dict[str, str]:
    """env агенту: плоский набор кред активных плагинов."""
    env: dict[str, str] = {}
    for creds in active_by_plugin().values():
        env.update(creds)
    return env
