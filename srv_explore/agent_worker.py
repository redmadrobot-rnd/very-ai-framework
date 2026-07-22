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


def _prompt() -> str:
    path = Path(os.environ.get("SRV_EXPLORE_PROMPT", str(HERE / "agent_prompt.md")))
    return path.read_text(encoding="utf-8").strip()


def _hook(steps):
    async def pretooluse(input_data, tool_use_id, context):  # noqa: ARG001 (сигнатура SDK)
        if input_data.get("tool_name") != "Bash":
            return {}
        cmd = (input_data.get("tool_input") or {}).get("command", "")
        if cmd.strip():
            ok, reason = guard.check_command_string(cmd)
        else:
            ok, reason = False, "пустая команда"
        steps.append({"cmd": cmd, "ok": ok, "reason": "" if ok else reason})
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
        max_turns=int(os.environ.get("SRV_EXPLORE_MAX_TURNS", "40")),
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
        print(json.dumps({"result": "", "steps": [], "error": "empty task"}))
        return 1
    print(json.dumps(asyncio.run(_run(task)), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
