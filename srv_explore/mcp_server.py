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
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from srv_explore import backstop, profile_store, provision, sandbox, tunnel_keys
from srv_explore.token_store import TokenStore


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


HERE = Path(__file__).resolve().parent
ADMIN_PAGE = HERE / "admin.html"


def public_host() -> str:
    return os.environ.get("SRV_EXPLORE_PUBLIC_HOST", "<host>")


# Инженерный токен текущего запроса: ставит SplitAuth, читает srv_explore (label).
CURRENT_TOKEN: contextvars.ContextVar = contextvars.ContextVar(
    "srv_explore_token", default=None
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


def admin_token() -> str | None:
    """Админ-токен инстанса (гейт /admin). Генерит install.sh при развёртывании."""
    return os.environ.get("SRV_EXPLORE_ADMIN_TOKEN") or None


def admin_authorized(authorization: str | None) -> bool:
    """True, если предъявлен верный админ-токен. Нет админ-токена в env → /admin off."""
    configured = admin_token()
    if not configured:
        return False
    provided = parse_bearer(authorization)
    if not provided:
        return False
    return secrets.compare_digest(provided, configured)


# --- запуск агента в песочнице ------------------------------------------------

# env, которые нужно пробросить агенту в песочницу (наружу песочница env не наследует).
_AGENT_PASS_ENV = [
    "CLAUDE_CODE_OAUTH_TOKEN",
    "PATH",
    "SRV_EXPLORE_CWD",
    "SRV_EXPLORE_PROMPT",
    "SRV_EXPLORE_MAX_TURNS",
]


def security_probe() -> dict:
    """Проба FS-хардинга в ПЕСОЧНИЦЕ агента (не сервиса — тот привилегирован).
    Не root/нет systemd-run → in-process fallback (dev)."""
    if not sandbox.available():
        return backstop.probe()
    code = (
        "import json,srv_explore.backstop as b;"
        "print(json.dumps({'fs_readonly':b._fs_readonly()}))"
    )
    rc, out, _ = sandbox.run([sys.executable, "-c", code])
    try:
        return json.loads(out) if rc == 0 else {"fs_readonly": None}
    except ValueError:
        return {"fs_readonly": None}


async def run_agent(task: str) -> tuple[str, list]:
    """Прогнать задачу readonly-агентом В ПЕСОЧНИЦЕ; вернуть (отчёт, команды сессии)."""
    env = {k: os.environ[k] for k in _AGENT_PASS_ENV if os.environ.get(k)}
    env.update(profile_store.provisioned())  # DOCKER_HOST на socket-proxy, *_DSN
    worker = [sys.executable, "-m", "srv_explore.agent_worker"]

    def spawn():
        return sandbox.run(worker, input_text=task, extra_env=env)

    rc, out, err = await asyncio.to_thread(spawn)
    if rc != 0:
        return f"[agent error] {(err or out).strip()[:800]}", []
    try:
        data = json.loads(out)
    except ValueError:
        return f"[agent output parse error] {out.strip()[:800]}", []
    return data.get("result", ""), data.get("steps", [])


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
                result, steps = await coro_factory()
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
    from starlette.responses import HTMLResponse, JSONResponse
    from starlette.routing import Mount, Route

    tokens = store or TokenStore()
    jobs = JobRegistry()
    security = security_probe()  # один раз при старте (в песочнице агента)
    mcp = FastMCP("srv-explore", streamable_http_path="/mcp")

    @mcp.tool()
    async def srv_explore(task: str) -> str:
        """Запустить readonly-разведку по задаче. Вернёт job_id (поллить status)."""
        rec = CURRENT_TOKEN.get()
        label = rec.label if rec else "?"
        job_id = jobs.start(task, label=label, coro_factory=lambda: run_agent(task))
        return json.dumps({"job_id": job_id, "status": "running"}, ensure_ascii=False)

    @mcp.tool()
    async def srv_explore_status(job_id: str) -> str:
        """Статус/результат задачи разведки по job_id."""
        job = jobs.get(job_id)
        if job is None:
            return json.dumps({"error": "unknown job_id"}, ensure_ascii=False)
        return json.dumps(job, ensure_ascii=False)

    # --- /admin: HTML-оболочка публична, данные — за админ-токеном ---
    def _require_admin(request):
        if not admin_authorized(request.headers.get("authorization")):
            return JSONResponse({"error": "admin unauthorized"}, status_code=401)
        return None

    async def admin_page(request):  # noqa: ARG001
        try:
            html = ADMIN_PAGE.read_text(encoding="utf-8")
        except OSError:
            html = "<h1>srv-explore admin</h1><p>admin.html не найден</p>"
        return HTMLResponse(html)

    async def admin_users(request):
        denied = _require_admin(request)
        if denied:
            return denied
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
        denied = _require_admin(request)
        if denied:
            return denied
        body = await request.json()
        label = (body.get("label") or "").strip()
        removed_key = tunnel_keys.remove_label(label)
        removed_tok = tokens.revoke_label(label)
        return JSONResponse({"key_removed": removed_key, "tokens_revoked": removed_tok})

    async def admin_runs(request):
        denied = _require_admin(request)
        if denied:
            return denied
        return JSONResponse({"runs": jobs.recent(100)})

    async def admin_security(request):
        denied = _require_admin(request)
        if denied:
            return denied
        return JSONResponse({"security": security, "status": backstop.status(security)})

    async def admin_ask(request):
        denied = _require_admin(request)
        if denied:
            return denied
        body = await request.json()
        task = (body.get("task") or "").strip()
        if not task:
            return JSONResponse({"error": "task обязателен"}, status_code=400)
        job_id = jobs.start(task, label="admin", coro_factory=lambda: run_agent(task))
        return JSONResponse({"job_id": job_id})

    async def admin_ask_status(request):
        denied = _require_admin(request)
        if denied:
            return denied
        job = jobs.get(request.path_params["job_id"])
        if job is None:
            return JSONResponse({"error": "unknown job_id"}, status_code=404)
        return JSONResponse(job)

    async def admin_profiles(request):
        denied = _require_admin(request)
        if denied:
            return denied
        if request.method == "POST":
            body = await request.json()
            name = body.get("name", "")
            enabled = bool(body.get("enabled"))
            if name not in profile_store.registry():
                return JSONResponse({"error": "неизвестный профиль"}, status_code=404)
            try:
                if enabled:
                    env = await asyncio.to_thread(provision.enable, name)
                    if env:
                        profile_store.add_provisioned(env)
                else:
                    keys = await asyncio.to_thread(provision.down, name)
                    profile_store.drop_provisioned(keys)
                profile_store.set_enabled(name, enabled)
            except (subprocess.CalledProcessError, OSError, KeyError) as e:
                return JSONResponse({"error": f"provision: {e}"}, status_code=500)
        state = profile_store.load()
        return JSONResponse(
            {
                "profiles": [
                    {"name": n, "desc": d, "enabled": state[n]}
                    for n, d in profile_store.registry().items()
                ]
            }
        )

    class SplitAuth(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            path = request.url.path
            if path.startswith("/admin"):
                return await call_next(request)  # /admin гейтит себя сам (админ-токен)
            rec = authorize(request.headers.get("authorization"), tokens)
            if rec is None:
                return JSONResponse({"error": "unauthorized"}, status_code=401)
            CURRENT_TOKEN.set(rec)
            return await call_next(request)

    @contextlib.asynccontextmanager
    async def lifespan(app):
        async with mcp.session_manager.run():
            yield

    return Starlette(
        routes=[
            Route("/admin", admin_page),
            Route("/admin/api/users", admin_users, methods=["GET", "POST"]),
            Route("/admin/api/users/remove", admin_user_remove, methods=["POST"]),
            Route("/admin/api/runs", admin_runs),
            Route("/admin/api/security", admin_security),
            Route("/admin/api/profiles", admin_profiles, methods=["GET", "POST"]),
            Route("/admin/api/ask", admin_ask, methods=["POST"]),
            Route("/admin/api/ask/{job_id}", admin_ask_status),
            Mount("/", app=mcp.streamable_http_app()),
        ],
        middleware=[Middleware(SplitAuth)],
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
