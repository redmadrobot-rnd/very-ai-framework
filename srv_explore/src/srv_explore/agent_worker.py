"""Воркер агента — крутится В ПЕСОЧНИЦЕ (см. sandbox.py), без прав.

Читает задачу со stdin, гоняет Claude Agent SDK с гард-хуком (гигиена), печатает в
stdout JSON {result, steps}. Опасный bash живёт здесь, под RO-FS и unprivileged-юзером.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

from srv_explore import guard

HERE = Path(__file__).resolve().parent
ALLOWED_TOOLS = ["Read", "Grep", "Glob", "Bash"]
MAX_TURNS = 40  # потолок шагов агента; общий потолок по времени — sandbox.MAX_SEC


def _prompt() -> str:
    path = Path(os.environ.get("SRV_EXPLORE_PROMPT", str(HERE / "agent_prompt.md")))
    text = path.read_text(encoding="utf-8").strip()
    res = os.environ.get("SRV_EXPLORE_RESOURCES", "").strip()
    off = os.environ.get("SRV_EXPLORE_RESOURCES_OFF", "").strip()
    tail = [
        f"Доступные ресурсы (креды уже в окружении, значения не печатай): {res}"
        if res
        else "Доступных ресурсов нет: плагины выключены, только файлы и логи."
    ]
    if off:
        tail.append(
            f"Выключенные ресурсы — доступа к ним НЕТ и обходных путей не ищи, "
            f"назови плагин администратору и остановись: {off}"
        )
    return "\n\n".join([text, *tail])


def _emit(event: dict) -> None:
    """Событие родителю построчно — чтобы шаги были видны по ходу, а не только в конце
    (и уцелели, если прогон прибьёт по RuntimeMaxSec)."""
    print(json.dumps(event, ensure_ascii=False), flush=True)


def _hook(steps):
    async def pretooluse(input_data, tool_use_id, context):  # noqa: ARG001 (сигнатура SDK)
        if input_data.get("tool_name") != "Bash":
            return {}
        cmd = (input_data.get("tool_input") or {}).get("command", "")
        if cmd.strip():
            ok, reason = guard.check_command_string(cmd)
        else:
            ok, reason = False, "пустая команда"
        rec = {"cmd": cmd, "ok": ok, "reason": "" if ok else reason}
        steps.append(rec)
        _emit({"type": "step", **rec})
        if ok:
            return {}
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": reason,
            }
        }

    return pretooluse


async def _run(task: str) -> dict:
    from claude_agent_sdk import (
        AssistantMessage,
        ClaudeAgentOptions,
        HookMatcher,
        ResultMessage,
        TextBlock,
        query,
    )

    steps: list = []
    options = ClaudeAgentOptions(
        system_prompt=_prompt(),
        allowed_tools=ALLOWED_TOOLS,
        permission_mode="dontAsk",
        hooks={"PreToolUse": [HookMatcher(matcher="Bash", hooks=[_hook(steps)])]},
        cwd=os.environ.get("SRV_EXPLORE_CWD", "/"),
        setting_sources=[],
        max_turns=MAX_TURNS,
    )
    final: list[str] = []
    result: str | None = None
    async for msg in query(prompt=task, options=options):
        if isinstance(msg, AssistantMessage):
            final = [b.text for b in msg.content if isinstance(b, TextBlock)]
        elif isinstance(msg, ResultMessage):
            result = msg.result
    return {"result": result or "\n".join(final), "steps": steps}


def main() -> int:
    task = sys.stdin.read().strip()
    if not task:
        _emit({"type": "result", "result": "", "error": "empty task"})
        return 1
    data = asyncio.run(_run(task))
    _emit({"type": "result", "result": data.get("result", "")})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
