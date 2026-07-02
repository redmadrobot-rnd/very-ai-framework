"""Codex PR conversational answerer — replies to `@codex <question>`.

Two modes (set by MODE env):
  issue  — `@codex …` in the PR conversation; reads PR meta + diff + the
           issue-comment thread; posts a PR comment (placeholder -> edited).
  inline — `@codex …` as a reply under an inline review comment (e.g. under a
           Codex finding); reads the code hunk + that review thread; replies IN the
           thread (placeholder reply -> edited).

GH_TOKEN is stripped from the codex subprocess; the answer is read via `codex exec -o`
(final message only). Question text comes via env (no shell injection).

Env: GH_TOKEN, REPO, PR_NUMBER, QUESTION, MODE; inline also: COMMENT_ID.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile

from codex_review import edit_comment, env, gh_api, post_comment, run

MAX_DIFF = 60000
MAX_COMMENT = 4000
TRIGGER = "@codex"

ISSUE_PROMPT = """\
You are Codex, answering a question in a GitHub pull request discussion. Be concise,
concrete and technical, and ground code answers in the diff below. Reply in the same
language as the question. Do NOT modify any files. Output plain markdown — your whole
reply is published as-is as a PR comment.

"""

INLINE_PROMPT = """\
You are Codex, answering a follow-up in an INLINE code review thread on a GitHub PR —
often under one of your own review findings. Be concise and technical; ground your answer
in the code hunk and the thread below. Reply in the same language as the question. Do NOT
modify any files. Output plain markdown — published as-is as a threaded reply.

"""


def ask_codex(prompt: str) -> str:
    """Запустить codex exec и вернуть ТОЛЬКО финальное сообщение (через -o).

    GH_TOKEN вычищаем из окружения подпроцесса: codex обрабатывает недоверенный текст
    (дифф/комментарии автора PR) — секрет ему не нужен. Промпт уходит через stdin
    (`codex exec -`): большой контекст в argv упёрся бы в ARG_MAX.
    """
    codex_env = {k: v for k, v in os.environ.items() if k != "GH_TOKEN"}
    with tempfile.NamedTemporaryFile("w+", suffix=".md", delete=False, encoding="utf-8") as tf:
        out_path = tf.name
    try:
        run("codex", "exec", "-o", out_path, "-", input=prompt, timeout=600, env=codex_env)
        with open(out_path, encoding="utf-8") as fh:
            return fh.read().strip()
    finally:
        try:
            os.unlink(out_path)
        except OSError:
            pass


def pr_meta_and_diff(repo: str, pr: str, token: str) -> tuple[dict, str]:
    """Мета PR + дифф ветки относительно базы (для предметных ответов по коду)."""
    _, info = gh_api("GET", f"/repos/{repo}/pulls/{pr}", token)
    base = (info.get("base") or {}).get("ref", "")
    diff = ""
    if base:
        subprocess.run(["git", "fetch", "origin", base], capture_output=True, text=True)
        subprocess.run(["git", "fetch", "origin", f"refs/pull/{pr}/head"], capture_output=True, text=True)
        diff = run("git", "diff", f"origin/{base}...FETCH_HEAD").strip()
    return info, diff


def answer_issue(repo: str, pr: str, token: str, question: str) -> None:
    """`@codex …` в общем треде PR → коммент (заглушка → обновление)."""
    progress_id = post_comment(repo, pr, token, "🤖 **Codex** — думаю над ответом… ⏳")

    def finalize(text: str) -> None:
        if progress_id is not None:
            edit_comment(repo, token, progress_id, text)
        else:
            post_comment(repo, pr, token, text)

    info, diff = pr_meta_and_diff(repo, pr, token)
    title, body = info.get("title", ""), (info.get("body") or "")

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

    try:
        answer = ask_codex(ISSUE_PROMPT + "\n".join(ctx))
    except Exception as exc:
        finalize(f"🤖 Codex: не удалось выполнить codex.\n\n```\n{str(exc)[:1000]}\n```")
        sys.exit(f"codex exec failed: {exc}")
    finalize("🤖 **Codex**\n\n" + (answer[:60000] or "_(пустой ответ)_"))
    print(f"answered (issue): {len(answer)} chars")


def reply_review_comment(repo: str, pr: str, token: str, comment_id: int, body: str) -> int | None:
    """Создать reply в inline-треде. Возвращает id ответа (или None)."""
    status, data = gh_api("POST", f"/repos/{repo}/pulls/{pr}/comments/{comment_id}/replies", token, {"body": body})
    return data.get("id") if status < 300 else None


def edit_review_comment(repo: str, token: str, comment_id: int, body: str) -> None:
    """Перезаписать тело inline review-комментария."""
    gh_api("PATCH", f"/repos/{repo}/pulls/comments/{comment_id}", token, {"body": body})


def review_thread(repo: str, pr: str, token: str, comment_id: int) -> tuple[dict | None, list]:
    """Корень и упорядоченная ветка inline-треда, содержащего comment_id.

    Треды восстанавливаем по in_reply_to_id (per_page=100 — длиннее редкость).
    """
    _, allc = gh_api("GET", f"/repos/{repo}/pulls/{pr}/comments?per_page=100", token)
    allc = allc if isinstance(allc, list) else []
    by_id = {c["id"]: c for c in allc}
    if comment_id not in by_id:
        return None, []

    def root_id_of(c: dict) -> int:
        node = c
        for _ in range(50):
            rid = node.get("in_reply_to_id")
            if rid in by_id:
                node = by_id[rid]
            else:
                break
        return node["id"]

    root_id = root_id_of(by_id[comment_id])
    thread = [c for c in allc if root_id_of(c) == root_id]
    thread.sort(key=lambda c: c.get("created_at", ""))
    return by_id[root_id], thread


def answer_inline(repo: str, pr: str, token: str, question: str, comment_id: int) -> None:
    """`@codex …` под inline-находкой → reply в том же треде (заглушка → обновление)."""
    progress_id = reply_review_comment(repo, pr, token, comment_id, "🤖 **Codex** — думаю над ответом… ⏳")

    def finalize(text: str) -> None:
        if progress_id is not None:
            edit_review_comment(repo, token, progress_id, text)
        else:
            reply_review_comment(repo, pr, token, comment_id, text)

    _, info = gh_api("GET", f"/repos/{repo}/pulls/{pr}", token)
    root, thread = review_thread(repo, pr, token, comment_id)
    path = (root or {}).get("path", "")
    hunk = (root or {}).get("diff_hunk", "")

    msgs = []
    for c in thread:
        if c.get("id") == progress_id:
            continue
        text = (c.get("body") or "").strip()[:MAX_COMMENT]
        if text:
            msgs.append(f"@{(c.get('user') or {}).get('login', '?')}: {text}")

    ctx = [f"## PR #{pr}: {info.get('title', '')}", "", f"## Inline thread on `{path}`"]
    if hunk:
        ctx += ["```diff", hunk[:MAX_DIFF], "```"]
    if msgs:
        ctx += ["", "## Thread so far", *msgs]
    ctx += ["", "## Question to answer", question or "(см. последний комментарий в треде)"]

    try:
        answer = ask_codex(INLINE_PROMPT + "\n".join(ctx))
    except Exception as exc:
        finalize(f"🤖 Codex: не удалось выполнить codex.\n\n```\n{str(exc)[:1000]}\n```")
        sys.exit(f"codex exec failed: {exc}")
    finalize("🤖 **Codex**\n\n" + (answer[:60000] or "_(пустой ответ)_"))
    print(f"answered (inline): {len(answer)} chars")


def main() -> None:
    token, repo, pr = env("GH_TOKEN"), env("REPO"), env("PR_NUMBER")
    question = os.environ.get("QUESTION", "").replace(TRIGGER, "", 1).strip()
    if os.environ.get("MODE") == "inline":
        answer_inline(repo, pr, token, question, int(env("COMMENT_ID")))
    else:
        answer_issue(repo, pr, token, question)


if __name__ == "__main__":
    main()
