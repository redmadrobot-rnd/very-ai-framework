"""Codex PR conversational answerer — replies to `@codex answer` in a PR thread.

Triggered by a PR comment containing `@codex answer <question>`. Gathers the PR
context (title/body + diff) and the comment thread, asks `codex exec`, and posts the
reply (live placeholder → edited into the answer) so you can chat with Codex in the PR.

Reuses the shared helpers from codex_review (same dir on the runner).

Env (from the workflow): GH_TOKEN, REPO, PR_NUMBER, QUESTION (triggering comment body).
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile

from codex_review import edit_comment, env, gh_api, post_comment, run

PROMPT = """\
You are Codex, answering a question in a GitHub pull request discussion. Be concise,
concrete and technical, and ground code answers in the diff below. Reply in the same
language as the question. Do NOT modify any files. Output plain markdown — your whole
reply is published as-is as a PR comment.

"""

MAX_DIFF = 60000
MAX_COMMENT = 4000
TRIGGER = "@codex answer"


def main() -> None:
    token, repo, pr = env("GH_TOKEN"), env("REPO"), env("PR_NUMBER")
    question = os.environ.get("QUESTION", "").replace(TRIGGER, "", 1).strip()

    # Сразу сигналим, что начали; этот же коммент обновим ответом.
    progress_id = post_comment(repo, pr, token, "🤖 **Codex** — думаю над ответом… ⏳")

    def finalize(text: str) -> None:
        if progress_id is not None:
            edit_comment(repo, token, progress_id, text)
        else:
            post_comment(repo, pr, token, text)

    # Мета PR + дифф (для предметных ответов по коду).
    _, info = gh_api("GET", f"/repos/{repo}/pulls/{pr}", token)
    base = (info.get("base") or {}).get("ref", "")
    title, body = info.get("title", ""), (info.get("body") or "")

    diff = ""
    if base:
        subprocess.run(["git", "fetch", "origin", base], capture_output=True, text=True)
        subprocess.run(["git", "fetch", "origin", f"refs/pull/{pr}/head"], capture_output=True, text=True)
        diff = run("git", "diff", f"origin/{base}...FETCH_HEAD").strip()

    # Ветка обсуждения (issue-комментарии PR), старые→новые; свою заглушку исключаем.
    _, comments = gh_api("GET", f"/repos/{repo}/issues/{pr}/comments", token)
    thread = []
    for c in comments if isinstance(comments, list) else []:
        if c.get("id") == progress_id:
            continue
        text = (c.get("body") or "").strip()[:MAX_COMMENT]
        if text:
            thread.append(f"@{(c.get('user') or {}).get('login', '?')}: {text}")

    ctx = [f"## PR #{pr}: {title}", "", body.strip()[:MAX_COMMENT]]
    if diff:
        ctx += ["", "## Diff", "```diff", diff[:MAX_DIFF], "```"]
    if thread:
        ctx += ["", "## Conversation so far", *thread]
    ctx += ["", "## Question to answer", question or "(см. последний комментарий выше)"]

    codex_env = {k: v for k, v in os.environ.items() if k != "GH_TOKEN"}
    with tempfile.NamedTemporaryFile("w+", suffix=".md", delete=False, encoding="utf-8") as tf:
        out_path = tf.name
    try:
        # -o пишет ТОЛЬКО финальное сообщение агента (stdout содержит шапку/прогресс — он нам не нужен).
        run("codex", "exec", "-o", out_path, PROMPT + "\n".join(ctx), timeout=600, env=codex_env)
        with open(out_path, encoding="utf-8") as fh:
            answer = fh.read().strip()
    except Exception as exc:
        finalize(f"🤖 Codex: не удалось выполнить codex.\n\n```\n{str(exc)[:1000]}\n```")
        sys.exit(f"codex exec failed: {exc}")
    finally:
        try:
            os.unlink(out_path)
        except OSError:
            pass

    finalize("🤖 **Codex**\n\n" + (answer[:60000] or "_(пустой ответ)_"))
    print(f"answered: {len(answer)} chars")


if __name__ == "__main__":
    main()
