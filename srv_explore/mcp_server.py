"""Remote MCP srv-explore: на хосте живёт readonly-агент, на вход — задача.

Инженер из своего Claude Code дёргает tool `srv_explore(task)`; сервер крутит Claude
Agent SDK headless с readonly-агентом, а каждую Bash-команду агента пропускает через
`guard.py` (единый источник правды политики «только чтение»).

Границы, которые держат «только чтение», живут ЗДЕСЬ, на сервере, вне машины инженера:
- `guard.py` PreToolUse-мостом режет не-read (curl/ssh off: `SRV_EXPLORE_NO_NETWORK`);
- `permission_mode="dontAsk"` + узкий `allowed_tools`;
- bearer-токен на входе (см. token_store), привязан к окружению этого инстанса;
- readonly-роль БД — фундамент (провижинится отдельно).

Зависимости рантайма (claude-agent-sdk, mcp, starlette, uvicorn) импортируются лениво,
чтобы чистая логика (авторизация, мост к гарду) тестировалась без них.
"""

from __future__ import annotations

import contextvars
import json
import os
import secrets
import subprocess
import sys
from pathlib import Path

from srv_explore.run_store import RunStore
from srv_explore.token_store import TokenStore

HERE = Path(__file__).resolve().parent
DEFAULT_GUARD = HERE / "guard.py"
DEFAULT_PROMPT = HERE / "agent_prompt.md"
ADMIN_PAGE = HERE / "admin.html"

ALLOWED_TOOLS = ["Read", "Grep", "Glob", "Bash"]

# Кто запустил текущий запрос (запись инженерного токена) — выставляет BearerAuth,
# читает tool srv_explore, чтобы пометить прогон в истории. Пробрасывается по контексту
# запроса (middleware → MCP-обработчик — один asyncio-таск).
CURRENT_TOKEN: contextvars.ContextVar = contextvars.ContextVar(
    "srv_explore_token", default=None
)


def guard_path() -> Path:
    return Path(os.environ.get("SRV_EXPLORE_GUARD", str(DEFAULT_GUARD)))


def server_env() -> str:
    """Окружение этого инстанса (dev|prod) — идентичность деплоя, не выбор клиента."""
    return os.environ.get("SRV_EXPLORE_ENV", "dev")


# --- авторизация (чистая, тестируемая) ---------------------------------------


def parse_bearer(authorization: str | None) -> str | None:
    """Достать токен из заголовка 'Authorization: Bearer <token>'."""
    if not authorization:
        return None
    parts = authorization.split(" ", 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    return parts[1].strip() or None


def authorize(authorization: str | None, store: TokenStore, env: str):
    """Вернуть валидную запись токена для окружения env, иначе None."""
    token = parse_bearer(authorization)
    if token is None:
        return None
    return store.verify(token, env=env)


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


# --- мост к guard.py (единый источник правды read-only политики) --------------


def guard_decision(
    tool_name: str, tool_input: dict, session_id: str = "mcp"
) -> tuple[bool, str]:
    """Прогнать команду через guard.py как рантайм Claude Code. (allow, reason)."""
    if tool_name != "Bash":
        return True, "не Bash — гард не применяется"
    payload = json.dumps(
        {"tool_name": tool_name, "tool_input": tool_input, "session_id": session_id}
    )
    proc = subprocess.run(
        [sys.executable, str(guard_path())],
        input=payload,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=dict(os.environ),
    )
    reason = ""
    try:
        decision = json.loads(proc.stdout) if proc.stdout.strip() else {}
        reason = decision.get("hookSpecificOutput", {}).get(
            "permissionDecisionReason", ""
        )
    except ValueError:
        reason = ""
    if not reason:
        reason = (proc.stderr or "").strip() or "решение гарда"
    return proc.returncode == 0, reason


def make_pretooluse_hook():
    """Async PreToolUse-хук для Agent SDK: мост к guard_decision."""

    async def hook(input_data, tool_use_id, context):  # noqa: ARG001 (сигнатура SDK)
        allow, reason = guard_decision(
            input_data.get("tool_name", ""),
            input_data.get("tool_input", {}) or {},
        )
        if allow:
            return {}
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": reason,
            }
        }

    return hook


def load_system_prompt(prompt_file: Path | None = None) -> str:
    """Системный промпт readonly-агента из srv_explore/agent_prompt.md."""
    path = prompt_file or Path(
        os.environ.get("SRV_EXPLORE_PROMPT", str(DEFAULT_PROMPT))
    )
    text = path.read_text(encoding="utf-8")
    if text.startswith("---"):  # на случай, если файл всё же с frontmatter
        end = text.find("\n---", 3)
        if end != -1:
            text = text[end + 4 :]
    return text.strip()


# --- запуск агента (ленивый импорт SDK) --------------------------------------


async def run_agent(task: str) -> str:
    """Прогнать задачу readonly-агентом headless, вернуть финальный текст-отчёт."""
    from claude_agent_sdk import (
        AssistantMessage,
        ClaudeAgentOptions,
        HookMatcher,
        ResultMessage,
        TextBlock,
        query,
    )

    options = ClaudeAgentOptions(
        system_prompt=load_system_prompt(),
        allowed_tools=ALLOWED_TOOLS,
        permission_mode="dontAsk",
        hooks={
            "PreToolUse": [HookMatcher(matcher="Bash", hooks=[make_pretooluse_hook()])]
        },
        cwd=os.environ.get("SRV_EXPLORE_CWD", "/"),
        setting_sources=[],  # изоляция: не тянем чужой .claude, всё задаём явно
        max_turns=int(os.environ.get("SRV_EXPLORE_MAX_TURNS", "40")),
    )

    final_text: list[str] = []
    result: str | None = None
    async for msg in query(prompt=task, options=options):
        if isinstance(msg, AssistantMessage):
            final_text = [b.text for b in msg.content if isinstance(b, TextBlock)]
        elif isinstance(msg, ResultMessage):
            result = msg.result
    return result or "\n".join(final_text)


# --- реестр задач (job-id + poll) --------------------------------------------


class JobRegistry:
    """Запуск исследований в фоне + персистентная история через RunStore.

    HTTP-запрос не держит длинное исследование; статус/результат/история переживают
    рестарт (RunStore пишет JSONL), питая монитор задач и лог сессий в /admin.
    """

    def __init__(self, runs: RunStore):
        self.runs = runs

    def start(self, task: str, label: str, env: str, coro_factory) -> str:
        import asyncio

        job_id = "job_" + secrets.token_hex(6)
        self.runs.start(job_id, task=task, label=label, env=env)

        async def runner():
            try:
                result = await coro_factory()
                self.runs.finish(job_id, result=result)
            except Exception as e:  # noqa: BLE001 — статус задачи, не глушим молча
                self.runs.finish(job_id, error=repr(e))

        asyncio.ensure_future(runner())
        return job_id

    def get(self, job_id: str) -> dict | None:
        rec = self.runs.get(job_id)
        if rec is None:
            return None
        return {"status": rec.status, "result": rec.result, "error": rec.error}


# --- сборка сервера (ленивый импорт MCP/Starlette) ---------------------------


def build_app(store: TokenStore | None = None, runs: RunStore | None = None):
    """ASGI: MCP (инженерный токен) + /admin (админ-токен). Запуск через uvicorn."""
    import contextlib

    from mcp.server.fastmcp import FastMCP
    from starlette.applications import Starlette
    from starlette.middleware import Middleware
    from starlette.middleware.base import BaseHTTPMiddleware
    from starlette.responses import HTMLResponse, JSONResponse
    from starlette.routing import Mount, Route

    tokens = store or TokenStore()
    run_store = runs or RunStore()
    env = server_env()
    jobs = JobRegistry(run_store)
    mcp = FastMCP("srv-explore", streamable_http_path="/mcp")

    @mcp.tool()
    async def srv_explore(task: str) -> str:
        """Запустить readonly-разведку по задаче. Вернёт job_id (поллить status)."""
        rec = CURRENT_TOKEN.get()
        label = rec.label if rec else "?"
        job_id = jobs.start(
            task, label=label, env=env, coro_factory=lambda: run_agent(task)
        )
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

    async def admin_tokens(request):
        denied = _require_admin(request)
        if denied:
            return denied
        if request.method == "POST":
            body = await request.json()
            label = (body.get("label") or "").strip()
            tok_env = (body.get("env") or env).strip()
            if not label:
                return JSONResponse({"error": "label обязателен"}, status_code=400)
            try:
                record, token = tokens.issue(label, tok_env)
            except ValueError as e:
                return JSONResponse({"error": str(e)}, status_code=400)
            # Токен в открытую — единственный раз, показать админу и не хранить.
            return JSONResponse({"token": token, "record": _rec_dict(record)})
        return JSONResponse(
            {"tokens": [_rec_dict(r) for r in tokens.list()], "env": env}
        )

    async def admin_revoke(request):
        denied = _require_admin(request)
        if denied:
            return denied
        ok = tokens.revoke(request.path_params["token_id"])
        return JSONResponse({"revoked": ok}, status_code=200 if ok else 404)

    async def admin_runs(request):
        denied = _require_admin(request)
        if denied:
            return denied
        return JSONResponse(
            {"runs": [_run_dict(r) for r in run_store.list_recent(100)]}
        )

    class SplitAuth(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            path = request.url.path
            if path.startswith("/admin"):
                return await call_next(request)  # /admin гейтит себя сам (админ-токен)
            rec = authorize(request.headers.get("authorization"), tokens, env)
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
            Route("/admin/api/tokens", admin_tokens, methods=["GET", "POST"]),
            Route(
                "/admin/api/tokens/{token_id}/revoke", admin_revoke, methods=["POST"]
            ),
            Route("/admin/api/runs", admin_runs),
            Mount("/", app=mcp.streamable_http_app()),
        ],
        middleware=[Middleware(SplitAuth)],
        lifespan=lifespan,
    )


def _rec_dict(r) -> dict:
    return {"id": r.id, "label": r.label, "env": r.env, "created": r.created}


def _run_dict(r) -> dict:
    return {
        "id": r.id,
        "task": r.task,
        "label": r.label,
        "env": r.env,
        "status": r.status,
        "started": r.started,
        "finished": r.finished,
        "result": r.result,
        "error": r.error,
    }


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
