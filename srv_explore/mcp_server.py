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

import json
import os
import subprocess
import sys
from pathlib import Path

from srv_explore.token_store import TokenStore

HERE = Path(__file__).resolve().parent
DEFAULT_GUARD = HERE / "guard.py"
DEFAULT_PROMPT = HERE / "agent_prompt.md"

ALLOWED_TOOLS = ["Read", "Grep", "Glob", "Bash"]


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
    """Простой in-memory реестр: длинные исследования не держат HTTP-соединение."""

    def __init__(self):
        self._jobs: dict[str, dict] = {}

    def new_id(self) -> str:
        import secrets

        return "job_" + secrets.token_hex(6)

    def start(self, coro_factory) -> str:
        import asyncio

        job_id = self.new_id()
        self._jobs[job_id] = {"status": "running", "result": None, "error": None}

        async def runner():
            try:
                self._jobs[job_id]["result"] = await coro_factory()
                self._jobs[job_id]["status"] = "done"
            except Exception as e:  # noqa: BLE001 — статус задачи, не глушим молча
                self._jobs[job_id]["status"] = "error"
                self._jobs[job_id]["error"] = repr(e)

        asyncio.ensure_future(runner())
        return job_id

    def get(self, job_id: str) -> dict | None:
        return self._jobs.get(job_id)


# --- сборка сервера (ленивый импорт MCP/Starlette) ---------------------------


def build_app(store: TokenStore | None = None):
    """ASGI-приложение: MCP streamable-HTTP + bearer-мидлварь. Запуск через uvicorn."""
    import contextlib

    from mcp.server.fastmcp import FastMCP
    from starlette.applications import Starlette
    from starlette.middleware import Middleware
    from starlette.middleware.base import BaseHTTPMiddleware
    from starlette.responses import JSONResponse
    from starlette.routing import Mount

    tokens = store or TokenStore()
    env = server_env()
    jobs = JobRegistry()
    mcp = FastMCP("srv-explore", streamable_http_path="/mcp")

    @mcp.tool()
    async def srv_explore(task: str) -> str:
        """Запустить readonly-разведку по задаче. Вернёт job_id (поллить status)."""
        job_id = jobs.start(lambda: run_agent(task))
        return json.dumps({"job_id": job_id, "status": "running"}, ensure_ascii=False)

    @mcp.tool()
    async def srv_explore_status(job_id: str) -> str:
        """Статус/результат задачи разведки по job_id."""
        job = jobs.get(job_id)
        if job is None:
            return json.dumps({"error": "unknown job_id"}, ensure_ascii=False)
        return json.dumps(job, ensure_ascii=False)

    class BearerAuth(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            if authorize(request.headers.get("authorization"), tokens, env) is None:
                return JSONResponse({"error": "unauthorized"}, status_code=401)
            return await call_next(request)

    @contextlib.asynccontextmanager
    async def lifespan(app):
        async with mcp.session_manager.run():
            yield

    return Starlette(
        routes=[Mount("/", app=mcp.streamable_http_app())],
        middleware=[Middleware(BearerAuth)],
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
