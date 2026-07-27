"""Раннер установки плагина: гоняет чеклист, вычищает секреты, хранит креды.

Сервис привилегированный → apt/docker/клиенты зовутся напрямую, без sudo.
Что именно делает плагин — знает сам плагин (profiles/*.py, контракт в plugin_api).
Первый упавший шаг останавливает установку: креды не сохраняются.
"""

from __future__ import annotations

from srv_explore import profile_store
from srv_explore.plugin_api import Ctx, step


def _plugin(pid: str):
    mod = profile_store.modules().get(pid)
    if mod is None:
        raise KeyError(pid)
    return mod


def fields(pid: str) -> list[dict]:
    """Поля формы установки, объявленные плагином (ядро в них не вникает)."""
    return list(getattr(_plugin(pid), "FIELDS", []))


def missing_fields(pid: str, values: dict) -> list[str]:
    """Обязательные поля, которые админ не заполнил."""
    return [
        f["name"]
        for f in fields(pid)
        if f.get("required", True) and not str(values.get(f["name"]) or "").strip()
    ]


def install(pid: str, values: dict | None = None) -> dict:
    """Прогнать чеклист плагина. Успех → сохранить креды (агент получит их при On).
    Введённые значения живут только внутри вызова, на диск не попадают."""
    mod = _plugin(pid)
    runner = getattr(mod, "install", None)
    if runner is None:
        raise KeyError(f"{pid}: плагин не умеет install")

    values = values or {}
    secret_names = {f["name"] for f in fields(pid) if f.get("secret")}
    ctx = Ctx(values, secrets_in=[values.get(n) for n in secret_names])

    checklist: list[dict] = []
    ok = True
    try:
        for s in runner(ctx):
            checklist.append(s)
            if not s["ok"]:
                ok = False
                break
    except Exception as e:  # noqa: BLE001 — падение плагина = пункт чеклиста, не 500
        detail = f"{type(e).__name__}: {e}"[:200]
        checklist.append(step("установка прервана", False, detail))
        ok = False

    # чеклист уходит на диск и в UI — секретов в нём быть не должно
    for s in checklist:
        s["detail"] = ctx.redact(s.get("detail", ""))

    if ok and ctx.creds:
        profile_store.set_creds(pid, ctx.creds)
    else:
        profile_store.drop_creds(pid)
        profile_store.set_enabled(pid, False)
    profile_store.set_checklist(pid, checklist, ok)
    return {"ok": ok, "checklist": checklist}


def uninstall(pid: str) -> None:
    """Снять установку: teardown плагина (если есть), забыть креды и чеклист."""
    mod = _plugin(pid)
    teardown = getattr(mod, "uninstall", None)
    if teardown is not None:
        try:
            teardown(Ctx())
        except Exception:  # noqa: BLE001 — ресурс мог исчезнуть, состояние всё равно чистим
            pass
    profile_store.drop_creds(pid)
    profile_store.set_enabled(pid, False)
    profile_store.drop_checklist(pid)
