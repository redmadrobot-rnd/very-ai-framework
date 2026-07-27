"""Раннер установки плагина: гоняет чеклист, вычищает секреты, хранит креды.

Сервис привилегированный → apt/docker/клиенты зовутся напрямую, без sudo.
Что именно делает плагин — знает сам плагин (plugins/*.py, контракт в plugin_api).
Первый упавший шаг останавливает установку: креды не сохраняются.
"""

from __future__ import annotations

from srv_explore import plugin_store
from srv_explore.plugin_api import Ctx, step


def _plugin(pid: str):
    mod = plugin_store.modules().get(pid)
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
        detail = ctx.redact(f"{type(e).__name__}: {e}")[:200]  # редакция ДО усечения
        checklist.append(step("установка прервана", False, detail))
        ok = False

    ok = ok and bool(checklist)  # плагин, не выдавший ни шага, не «установлен»
    # страховка: чеклист уходит на диск и в UI, секретов в нём быть не должно
    for s in checklist:
        s["detail"] = ctx.redact(str(s.get("detail", "")))

    if ok and ctx.creds:
        plugin_store.set_creds(pid, ctx.creds)
        # установка оставляет ресурс поднятым (иначе нечем пробовать) — вернуть
        # его в состояние, которое сейчас показывает тумблер
        _apply_toggle(pid, plugin_store.load().get(pid, False))
    else:
        # Неудача или переустановка без кред: прежние могли протухнуть, чистим —
        # иначе агент получил бы креды прошлой установки под свежим чеклистом.
        plugin_store.drop_creds(pid)
        if not ok:
            plugin_store.set_enabled(pid, False)
    plugin_store.set_checklist(pid, checklist, ok)
    return {"ok": ok, "checklist": checklist}


def _apply_toggle(pid: str, enabled: bool) -> None:
    """Дать плагину физически открыть/закрыть ресурс. Без этого тумблер только
    прячет креды, а ресурс вроде docker-прокси на loopback остаётся доступен
    песочнице напрямую."""
    hook = getattr(_plugin(pid), "toggle", None)
    if hook is None:
        return
    try:
        hook(Ctx(), enabled)
    except Exception as e:  # noqa: BLE001 — наверх уходит как ошибка эндпоинта
        raise RuntimeError(f"{pid}: не удалось переключить ресурс — {e}") from e


def toggle(pid: str, enabled: bool) -> None:
    """Тумблер On/Off. Ресурс закрывается до того, как забываются креды."""
    _apply_toggle(pid, enabled)
    plugin_store.set_enabled(pid, enabled)


def uninstall(pid: str) -> None:
    """Снять установку: teardown плагина (если есть), забыть креды и чеклист."""
    mod = _plugin(pid)
    teardown = getattr(mod, "uninstall", None)
    if teardown is not None:
        # Упал teardown — состояние НЕ чистим: «снят» при живом ресурсе (docker-прокси
        # на loopback песочнице доступен и без кред) хуже честной ошибки. Плагин,
        # которому нечего проверять, просто не бросает.
        try:
            teardown(Ctx())
        except Exception as e:  # noqa: BLE001 — наверх как ошибка эндпоинта
            raise RuntimeError(f"{pid}: ресурс не снят — {e}") from e
    plugin_store.drop_creds(pid)
    plugin_store.set_enabled(pid, False)
    plugin_store.drop_checklist(pid)
