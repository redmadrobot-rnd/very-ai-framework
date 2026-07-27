"""Remote MCP srv-explore: на хосте живёт readonly-агент, на вход — задача.

Инженер из своего Claude Code дёргает tool `srv_explore(task)`. Сервис (root) провижинит
и спавнит агента в ПЕСОЧНИЦЕ (sandbox.py: unprivileged `srvx-agent` + RO-FS); опасный
bash крутится там, не здесь. Каждую Bash-команду агента фильтрует гард-гигиена.

Read-only держит РЕСУРС-СЛОЙ (RO-FS песочницы, read-only роли БД, docker-socket-proxy,
egress-firewall) — см. README. Плюс bearer-токен на входе (token_store).

Зависимости рантайма (mcp, starlette, uvicorn) импортируются лениво, чтобы чистая логика
(авторизация) тестировалась без них.
"""

from __future__ import annotations

import asyncio
import contextvars
import json
import os
import secrets
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from srv_explore import (
    backstop,
    plugin_store,
    provision,
    sandbox,
    settings,
    tunnel_keys,
)
from srv_explore.token_store import TokenStore


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


HERE = Path(__file__).resolve().parent
WEB = HERE / "web"
ADMIN_PAGE = WEB / "admin.html"
UI_PAGE = WEB / "ui.html"
UI_CSS = WEB / "ui.css"
FLOW_SVG = WEB / "flow.svg"


def public_host() -> str:
    return os.environ.get("SRV_EXPLORE_PUBLIC_HOST", "<host>")


# Кто пришёл — ставит Gate, читают хендлеры и MCP-инструмент (label в прогоне).
CURRENT: contextvars.ContextVar = contextvars.ContextVar(
    "srv_explore_identity", default=None
)


# --- авторизация (чистая, тестируемая) ---------------------------------------


def parse_bearer(authorization: str | None) -> str | None:
    """Достать токен из заголовка 'Authorization: Bearer <token>'."""
    if not authorization:
        return None
    parts = authorization.split(" ", 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    return parts[1].strip() or None


def authorize(authorization: str | None, store: TokenStore):
    """Вернуть валидную запись токена, иначе None."""
    token = parse_bearer(authorization)
    if token is None:
        return None
    return store.verify(token)


def admin_authorized(authorization: str | None) -> bool:
    """True, если предъявлен верный админ-токен (env `SRV_EXPLORE_ADMIN_TOKEN`,
    генерит install.sh). Нет админ-токена в env — /admin недоступна никому."""
    configured = os.environ.get("SRV_EXPLORE_ADMIN_TOKEN") or None
    if not configured:
        return False
    provided = parse_bearer(authorization)
    if not provided:
        return False
    return secrets.compare_digest(provided, configured)


@dataclass(frozen=True)
class Identity:
    """Кто прислал запрос. Роль решает, что ему покажут и куда пустят."""

    label: str
    role: str  # admin | engineer
    created: str = ""  # когда выдан токен; у админского даты нет


def identify(authorization: str | None, store: TokenStore) -> Identity | None:
    """Единственное место, где читается предъявленный токен."""
    if admin_authorized(authorization):
        return Identity("admin", "admin")
    rec = authorize(authorization, store)
    return Identity(rec.label, "engineer", rec.created) if rec else None


# Допуск по пути: None — пускаем без токена, иначе нужна роль. Вся картина «кто
# куда пускается» — здесь; хендлеры права не проверяют. Путь с `/` на конце
# закрывает поддерево, остальные — ровно себя.
GATE: tuple[tuple[str, str | None], ...] = (
    ("/", None),  # лендинг: оболочка публична, данные за /app/api/*
    ("/ui.css", None),
    ("/flow.svg", None),  # схема на лендинге
    ("/admin", None),  # оболочка админки — так же
    ("/admin/api/", "admin"),
    ("/app/api/", "engineer"),  # админ проходит тоже: роль старше
)
DEFAULT_ROLE = "engineer"  # всё прочее, включая /mcp: без токена нельзя


def required_role(path: str) -> str | None:
    for prefix, role in GATE:
        if path == prefix:
            return role
    for prefix, role in GATE:
        if len(prefix) > 1 and prefix.endswith("/") and path.startswith(prefix):
            return role
    return DEFAULT_ROLE


def role_allows(role: str, need: str | None) -> bool:
    return need is None or role == need or role == "admin"


# --- запуск агента в песочнице ------------------------------------------------

# env, которые нужно пробросить агенту в песочницу (наружу песочница env не наследует).
_AGENT_PASS_ENV = [
    "CLAUDE_CODE_OAUTH_TOKEN",
    "PATH",
    "SRV_EXPLORE_CWD",
    "SRV_EXPLORE_PROMPT",
]


def security_probe() -> dict:
    """Проба хардинга (FS read-only + egress обрублен) в ПЕСОЧНИЦЕ агента, не сервиса
    (тот привилегирован). Не root/нет systemd-run → in-process fallback (dev)."""
    if not sandbox.available():
        return backstop.probe()
    code = "import json,srv_explore.backstop as b;print(json.dumps(b.probe()))"
    rc, out, _ = sandbox.run([sys.executable, "-c", code])
    try:
        return json.loads(out) if rc == 0 else {}
    except ValueError:
        return {}


async def run_agent(task: str, steps: list | None = None) -> tuple[str, list]:
    """Прогнать задачу readonly-агентом В ПЕСОЧНИЦЕ; вернуть (отчёт, команды сессии).

    Воркер шлёт события построчно: шаги попадают в `steps` по ходу прогона (видно в
    админке), поэтому при обрыве по таймауту собранное не теряется.
    """
    env = {k: os.environ[k] for k in _AGENT_PASS_ENV if os.environ.get(k)}
    active = plugin_store.active_by_plugin()  # только установленные и включённые
    reg = plugin_store.registry()
    for creds in active.values():
        env.update(creds)
    # чтобы агент не гадал, что ему выдали: описание плагина + имена переменных
    env["SRV_EXPLORE_RESOURCES"] = "; ".join(
        f"{reg.get(pid, pid)} — {', '.join(creds)}"
        for pid, creds in sorted(active.items())
    )
    # и что существует, но не выдано: иначе агент перебирает обходные пути вместо
    # того, чтобы сразу назвать плагин, который админу надо включить
    env["SRV_EXPLORE_RESOURCES_OFF"] = "; ".join(
        f"{pid} ({desc})" for pid, desc in sorted(reg.items()) if pid not in active
    )
    # что открыл админ в настройках: без этого агент считает, что сети нет вообще
    env["SRV_EXPLORE_EGRESS"] = ", ".join(
        d for d in settings.egress_domains() if d != settings.MODEL_DOMAIN
    )
    worker = [sys.executable, "-m", "srv_explore.agent_worker"]
    if steps is None:
        steps = []
    final: dict = {}

    def on_line(line: str):
        try:
            ev = json.loads(line)
        except ValueError:
            return
        if ev.get("type") == "step":
            steps.append({k: ev.get(k) for k in ("cmd", "ok", "reason")})
        elif ev.get("type") == "result":
            final.update(ev)

    def spawn():
        return sandbox.run(worker, input_text=task, extra_env=env, on_line=on_line)

    rc, out, err = await asyncio.to_thread(spawn)
    if (final.get("result") or "").strip():
        return final["result"], steps
    # результата нет: статус задачи должен быть error, а не done с обрезком.
    # Собранные шаги не теряются — steps это тот же список, что лежит в job.
    tail = (err or out).strip()[:600]
    if rc != 0:
        raise RuntimeError(
            f"агент прерван (код {rc}, лимит прогона {sandbox.MAX_SEC}s); "
            f"успел команд: {len(steps)}. {tail}"
        )
    raise RuntimeError(f"агент не вернул результат. {tail}")


# --- реестр задач (job-id + poll) --------------------------------------------


class JobRegistry:
    """Прогоны в оперативе (живые + недавние завершённые), капается по limit."""

    def __init__(self, limit: int = 200):
        self._jobs: dict[str, dict] = {}
        self._order: list[str] = []
        self.limit = limit

    def start(self, task: str, label: str, coro_factory) -> str:
        job_id = "job_" + secrets.token_hex(6)
        self._jobs[job_id] = {
            "id": job_id,
            "task": task,
            "label": label,
            "status": "running",
            "started": _now(),
            "finished": None,
            "result": None,
            "error": None,
            "steps": [],
        }
        self._order.append(job_id)
        if len(self._order) > self.limit:
            self._jobs.pop(self._order.pop(0), None)

        async def runner():
            job = self._jobs.get(job_id)
            try:
                # тот же список, что лежит в job: шаги видны по ходу прогона
                result, steps = await coro_factory(job["steps"] if job else None)
                if job:
                    job.update(
                        status="done", result=result, steps=steps, finished=_now()
                    )
            except Exception as e:  # noqa: BLE001 — статус задачи, не глушим молча
                if job:
                    job.update(status="error", error=repr(e), finished=_now())

        asyncio.ensure_future(runner())
        return job_id

    def get(self, job_id: str) -> dict | None:
        return self._jobs.get(job_id)

    def recent(self, limit: int = 100) -> list[dict]:
        return [self._jobs[i] for i in reversed(self._order)][:limit]


# --- сборка сервера (ленивый импорт MCP/Starlette) ---------------------------


def build_app(store: TokenStore | None = None):
    """ASGI: MCP (инженерный токен) + /admin (админ-токен). Запуск через uvicorn."""
    import contextlib

    from mcp.server.fastmcp import FastMCP
    from starlette.applications import Starlette
    from starlette.middleware import Middleware
    from starlette.middleware.base import BaseHTTPMiddleware
    from starlette.responses import HTMLResponse, JSONResponse, Response
    from starlette.routing import Mount, Route

    tokens = store or TokenStore()
    jobs = JobRegistry()
    prov_lock = asyncio.Lock()  # провижининг строго последовательный (toggle-спам)
    security = security_probe()  # один раз при старте (в песочнице агента)
    mcp = FastMCP("srv-explore", streamable_http_path="/mcp")

    @mcp.tool()
    async def srv_explore(task: str) -> str:
        """Спросить сервер: readonly-агент на хосте выполнит задачу и вернёт факты.

        Асинхронно: здесь возвращается только job_id, результат забирай
        srv_explore_status(job_id), опрашивая раз в 3-5 секунд (прогон занимает от
        20 секунд до нескольких минут, потолок — 10 минут).

        task — цель словами, без готовых команд («почему сервис отдаёт 502?»).
        Агент только смотрит: изменить файлы, БД или контейнеры он не может, наружу
        ходит только по адресам, которые открыл администратор — просить его о
        большем бесполезно.

        В ответе resources — к чему у него сейчас есть доступ помимо файлов и логов.
        """
        who = CURRENT.get()
        label = who.label if who else "?"
        job_id = jobs.start(
            task, label=label, coro_factory=lambda steps: run_agent(task, steps)
        )
        reg = plugin_store.registry()
        resources = [reg.get(p, p) for p in sorted(plugin_store.active_by_plugin())]
        return json.dumps(
            {"job_id": job_id, "status": "running", "resources": resources},
            ensure_ascii=False,
        )

    @mcp.tool()
    async def srv_explore_status(job_id: str) -> str:
        """Статус задачи разведки: running | done | error.

        running — продолжай опрашивать; steps растёт по ходу и показывает, какие
        команды агент уже выполнил (ok=false значит команду отклонила гигиена, агент
        обычно переформулирует сам).
        done — result содержит факты: что спросили, какие команды, что показали.
        error — в error причина; шаги, собранные до обрыва, остаются в steps и обычно
        уже отвечают на часть вопроса.
        """
        job = jobs.get(job_id)
        if job is None:
            return json.dumps({"error": "unknown job_id"}, ensure_ascii=False)
        return json.dumps(job, ensure_ascii=False)

    # --- страницы: HTML/CSS публичны, данные — за токеном ---
    # no-store: апгрейд сервиса должен быть виден сразу, без «почисти кэш»
    NO_STORE = {"Cache-Control": "no-store"}

    def _page(path: Path, fallback: str):
        try:
            body = path.read_text(encoding="utf-8")
        except OSError:
            body = fallback
        return HTMLResponse(body, headers=NO_STORE)

    async def ui_page(request):  # noqa: ARG001
        return _page(UI_PAGE, "<h1>srv-explore</h1><p>ui.html не найден</p>")

    def _asset(path: Path, media: str, fallback: str = ""):
        try:
            body = path.read_text(encoding="utf-8")
        except OSError:
            body = fallback
        return Response(body, media_type=media, headers=NO_STORE)

    async def ui_css(request):  # noqa: ARG001
        return _asset(UI_CSS, "text/css; charset=utf-8", "/* ui.css не найден */")

    async def ui_flow(request):  # noqa: ARG001
        return _asset(FLOW_SVG, "image/svg+xml; charset=utf-8", "<svg/>")

    # --- /app: кабинет. Кого сюда пускать, решил Gate; тут только рендер по роли ---
    def _who(request) -> Identity:
        return request.scope["srvx_identity"]

    async def app_me(request):
        who = _who(request)
        enabled = plugin_store.load()
        installed = plugin_store.installed_all()
        runs = jobs.recent(200)
        if who.role != "admin":
            runs = [j for j in runs if j["label"] == who.label]
        return JSONResponse(
            {
                "label": who.label,
                "role": who.role,
                "created": who.created,
                "plugins": [
                    {
                        "name": n,
                        "desc": d,
                        "installed": bool(installed.get(n, {}).get("ok")),
                        "enabled": bool(enabled.get(n)),
                    }
                    for n, d in plugin_store.registry().items()
                ],
                "runs": runs[:20],
            }
        )

    async def app_ask(request):
        who = _who(request)
        body = await request.json()
        task = (body.get("task") or "").strip()
        if not task:
            return JSONResponse({"error": "task обязателен"}, status_code=400)
        job_id = jobs.start(
            task, label=who.label, coro_factory=lambda steps: run_agent(task, steps)
        )
        return JSONResponse({"job_id": job_id})

    async def app_ask_status(request):
        who = _who(request)
        job = jobs.get(request.path_params["job_id"])
        # чужой прогон неотличим от несуществующего; админ видит любой
        if job is None or (who.role != "admin" and job["label"] != who.label):
            return JSONResponse({"error": "unknown job_id"}, status_code=404)
        return JSONResponse(job)

    # --- /admin: оболочка публична, данные — под ролью admin (см. GATE) ---
    async def admin_page(request):  # noqa: ARG001
        return _page(
            ADMIN_PAGE, "<h1>srv-explore admin</h1><p>admin.html не найден</p>"
        )

    async def admin_users(request):
        if request.method == "POST":
            body = await request.json()
            label = (body.get("label") or "").strip()
            pubkey = (body.get("pubkey") or "").strip()
            if not label or not pubkey:
                return JSONResponse(
                    {"error": "нужны label и публичный ключ"}, status_code=400
                )
            try:
                tunnel_keys.add(label, pubkey)
            except ValueError as e:
                return JSONResponse({"error": f"ключ не принят: {e}"}, status_code=400)
            _, token = tokens.issue(label)
            # Токен в открытую — единожды, показать админу и отдать инженеру.
            return JSONResponse({"label": label, "host": public_host(), "token": token})
        return JSONResponse({"users": tunnel_keys.list_users()})

    async def admin_user_remove(request):
        body = await request.json()
        label = (body.get("label") or "").strip()
        removed_key = tunnel_keys.remove_label(label)
        removed_tok = tokens.revoke_label(label)
        return JSONResponse({"key_removed": removed_key, "tokens_revoked": removed_tok})

    async def admin_runs(request):  # noqa: ARG001
        return JSONResponse({"runs": jobs.recent(100)})

    async def admin_security(request):  # noqa: ARG001
        return JSONResponse(
            {
                "status": backstop.status(security),
                "net_status": backstop.net_status(security),
            }
        )

    async def admin_settings(request):
        """Объявленные ручки + значения. Правка применяется сразу: домены — через
        пересборку allowlist прокси, подсети — со следующего прогона агента."""
        if request.method == "POST":
            body = await request.json()
            values = body.get("values") or {}
            err = settings.validate(values)
            if err:
                return JSONResponse({"error": err}, status_code=400)
            saved = settings.save(values)
            # изменения границы должны оставлять след вне UI
            print(f"settings: {saved}", file=sys.stderr, flush=True)
            applied = await asyncio.to_thread(settings.apply_egress)
            return JSONResponse(
                {"fields": settings.FIELDS, "values": saved, "applied": applied}
            )
        return JSONResponse({"fields": settings.FIELDS, "values": settings.load()})

    def _plugins_payload():
        """Сохранённое состояние: установлен (+чеклист) и включён. Живых проб нет."""
        enabled = plugin_store.load()
        installed = plugin_store.installed_all()
        out = []
        for n, d in plugin_store.registry().items():
            rec = installed.get(n, {})
            out.append(
                {
                    "name": n,
                    "desc": d,
                    "fields": provision.fields(n),  # форму рисует плагин, не ядро
                    "installed": bool(rec.get("ok")),
                    "enabled": enabled[n],
                    "checklist": rec.get("checklist", []),
                    "at": rec.get("at", ""),
                }
            )
        return JSONResponse({"plugins": out})

    async def admin_plugins(request):
        if request.method == "POST":
            body = await request.json()
            name = body.get("name", "")
            action = body.get("action", "")
            values = body.get("values") or {}
            if name not in plugin_store.registry():
                return JSONResponse({"error": "неизвестный плагин"}, status_code=404)
            async with prov_lock:  # установка/переключение строго последовательны
                try:
                    if action == "install":
                        if provision.missing_fields(name, values):
                            # форму рисует админка по FIELDS плагина
                            return JSONResponse(
                                {"need_fields": provision.fields(name), "name": name}
                            )
                        await asyncio.to_thread(provision.install, name, values)
                    elif action == "uninstall":
                        await asyncio.to_thread(provision.uninstall, name)
                    elif action in ("on", "off"):
                        if action == "on" and not plugin_store.is_installed(name):
                            return JSONResponse(
                                {"error": "плагин не установлен"}, status_code=400
                            )
                        await asyncio.to_thread(provision.toggle, name, action == "on")
                    else:
                        return JSONResponse(
                            {"error": "неизвестное действие"}, status_code=400
                        )
                except (OSError, KeyError, RuntimeError) as e:
                    return JSONResponse({"error": f"provision: {e}"}, status_code=500)
        return _plugins_payload()

    class Gate(BaseHTTPMiddleware):
        """Единственная проверка доступа: путь → нужная роль (GATE) → токен → роль.

        Личность кладётся в scope (её читают хендлеры) и в contextvar (её читает
        MCP-инструмент, у которого объекта запроса нет).
        """

        async def dispatch(self, request, call_next):
            need = required_role(request.url.path)
            who = identify(request.headers.get("authorization"), tokens)
            if need is not None and (who is None or not role_allows(who.role, need)):
                return JSONResponse({"error": "unauthorized"}, status_code=401)
            request.scope["srvx_identity"] = who
            CURRENT.set(who)
            return await call_next(request)

    @contextlib.asynccontextmanager
    async def lifespan(app):
        async with mcp.session_manager.run():
            yield

    return Starlette(
        routes=[
            Route("/", ui_page),
            Route("/ui.css", ui_css),
            Route("/flow.svg", ui_flow),
            Route("/app/api/me", app_me),
            Route("/app/api/ask", app_ask, methods=["POST"]),
            Route("/app/api/ask/{job_id}", app_ask_status),
            Route("/admin", admin_page),
            Route("/admin/api/users", admin_users, methods=["GET", "POST"]),
            Route("/admin/api/users/remove", admin_user_remove, methods=["POST"]),
            Route("/admin/api/runs", admin_runs),
            Route("/admin/api/security", admin_security),
            Route("/admin/api/plugins", admin_plugins, methods=["GET", "POST"]),
            Route("/admin/api/settings", admin_settings, methods=["GET", "POST"]),
            Mount("/", app=mcp.streamable_http_app()),
        ],
        middleware=[Middleware(Gate)],
        lifespan=lifespan,
    )


def main() -> int:
    import uvicorn

    host = os.environ.get("SRV_EXPLORE_HOST", "127.0.0.1")
    port = int(
        os.environ.get("SRV_EXPLORE_PORT", "8765")
    )  # 8080 часто занят docker-proxy
    uvicorn.run(build_app(), host=host, port=port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
