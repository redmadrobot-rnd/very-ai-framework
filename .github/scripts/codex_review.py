#!/usr/bin/env python3
#
# ЧТО ДЕЛАЕТ: авто-ревью PR через codex exec — постит live-коммент со статусом и
#             наполняет его вердиктом (LGTM / Needs changes) и inline-замечаниями.
# Вход:    env от workflow: GH_TOKEN, REPO (owner/repo), PR_NUMBER;
#          опц. BASE_REF, HEAD_SHA (иначе берутся из GitHub API по PR).
# Алгоритм:
#   сразу постим коммент-заглушку («анализирую… ⏳») — видно, что джоб пошёл;
#   считаем дифф PR против базовой ветки (git fetch base + refs/pull/<n>/head);
#   код PR — в read-only detached worktree; codex exec копает его сам и отдаёт JSON;
#   оставляем только находки, привязываемые к строке диффа (прочее — в summary);
#   есть inline → один PR-review: вердикт+summary в теле, заглушку удаляем;
#   нет inline → вписываем итог в заглушку.
# Выход:   PR-review или коммент с вердиктом; парс/пост-сбой деградирует в
#          summary-коммент — джоб не падает на шуме ревью.

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request

API = "https://api.github.com"
SEV_EMOJI = {"high": "🔴", "medium": "🟡", "low": "🟢"}

# Prepended to the prompt: codex reviews from a read-only worktree of the PR
# branch, so it fetches the actual changes and surrounding code itself.
PR_CONTEXT_NOTE = (
    "Your working directory is the PR branch checked out as a git repository "
    "(HEAD = PR head, base branch = origin/{base}). You have read-only shell "
    "access. The patch is NOT pasted — only the changed-file list below. Get "
    "the actual changes yourself with git, and go beyond them as needed:\n"
    "  - `git diff origin/{base}...HEAD` — full change (append `-- <path>` "
    "for one file);\n"
    "  - `git log --oneline origin/{base}..HEAD` and `git show <rev>` — "
    "commit history/intent;\n"
    "  - `git grep -n <symbol>` (or grep the tree) — find callers, definitions "
    "and other uses of anything the PR touches, across the WHOLE codebase;\n"
    "  - `git blame`, and read any file in full for imports, callers, type "
    "defs, neighbours.\n"
    "Review scope = the files this PR changes (see 'Changed files' below). "
    "Read the rest of the codebase as needed to judge the CONSEQUENCES of "
    "those changes (hot-path callers, duplicated logic elsewhere, broken "
    "contracts). Prefer running git/grep over guessing.\n\n"
)

PROMPT = """\
You are a senior code reviewer. Review the pull request and reply with ONLY a JSON
object (no markdown, no prose, no code fences). Schema:

{
  "verdict": "lgtm" | "needs_changes",
  "summary": "<2-3 sentence assessment: what the PR does + the verdict>",
  "overall": [
    {
      "severity": "high" | "medium" | "low",
      "category": "scope" | "process" | "architecture" | "correctness" |
                  "security" | "performance" | "testing",
      "comment": "<whole-PR observation that does NOT belong on a single diff line>"
    }
  ],
  "findings": [
    {
      "path": "<file path exactly as in the diff>",
      "line": <line number in the NEW file>,
      "severity": "high" | "medium" | "low",
      "category": "bug" | "security" | "performance" | "style",
      "comment": "<concise, actionable explanation>"
    }
  ]
}

Method — be thorough, not quick. A good review finds MANY issues, not one or two.
The changed-file list shows WHAT changed but not its consequences. The PR branch is
checked out in your working directory (see the context note above) — read the actual
code with git. For every changed file and every added/modified function you MUST
investigate beyond the immediate change:
  - Trace callers and call sites: is a newly added call on a hot path (per-request,
    per message, inside a loop)? Does it add I/O (DB query, network, S3, file read)
    that runs even when unnecessary? Flag added latency/cost on hot paths.
  - Look across files for the SAME logic added or edited in more than one place
    (copy-paste), and for two paths that should behave the same but now diverge.
  - Verify exact literals against their real meaning: MIME types, HTTP status codes,
    enum/constant values, SQL, regexes, format strings, headers. A plausible-looking
    string can be wrong (e.g. a fabricated media type).
  - Check error handling, resource cleanup, and edge cases (empty/None, large input,
    concurrent access) for each new code path.
  - Confirm claimed behaviour actually holds by reading the surrounding code, not the
    diff alone.
Cover EVERY changed file — do not stop after the first few findings. Report every real
issue you can substantiate. Prioritise by severity, but do not omit medium/low issues.

Review the change on its own merits. Two kinds of feedback, both required:

1. "overall" — judgments about the change AS A WHOLE that do not belong on one line.
   Assess, as applicable to THIS change (skip what doesn't apply — don't force it):
   - Scope & focus: does the change do what its title/description say, and only that?
     Flag unrelated work bundled in (drive-by refactors, formatting, infra, config)
     that should be a separate change, and any stated intent the diff does not
     actually deliver.
   - Consistency with stated intent: if the description or any committed doc/plan sets a
     boundary, non-goal, or "not in this change", flag code that crosses it.
   - Completeness & process: unfinished work (TODO/FIXME, stubs, dead flags), or a
     description that admits a check/verification was skipped or still pending.
   - Design & maintainability: duplicated logic across files, needless abstraction,
     dead code, a simpler approach the change ignores, breaking an existing contract.
   - Testing: new or changed behaviour with no matching test, or claims of passing
     checks the diff cannot support.
   Judge proportionally to the change's size and risk — a one-line fix rarely needs
   "overall" points; a large or risky change usually does. Empty is fine when clean.

2. "findings" — concrete correctness/security/performance issues on a specific changed
   line. Only report on lines present in the diff. Skip trivial style nits.

Do NOT modify any files. If nothing is notable, return empty "overall" and "findings"
arrays and verdict "lgtm". Ground every point in the actual diff/PR context — never
invent issues to fill the arrays.

"""

# PR metadata (title/body/draft) gives the model what the diff can't: stated
# intent, MVP/"stage 2" boundaries, and draft status. Without it scope-creep
# and draft-merge findings are impossible.
PR_META_TMPL = (
    "Pull request under review:\nTitle: {title}\nDraft: {draft}\n"
    "Description:\n<<<PR_BODY\n{body}\nPR_BODY\n\n"
)


def env(name: str) -> str:
    val = os.environ.get(name)
    if not val:
        sys.exit(f"missing env: {name}")
    return val


def gh_api(
    method: str, path: str, token: str, payload: dict | None = None
) -> tuple[int, dict]:
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(f"{API}{path}", data=data, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/vnd.github+json")
    if data:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req) as resp:
            body = resp.read()
            return resp.status, (json.loads(body) if body else {})
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read())
        except Exception:
            return exc.code, {}


def post_comment(repo: str, pr: str, token: str, body: str) -> int | None:
    status, data = gh_api(
        "POST", f"/repos/{repo}/issues/{pr}/comments", token, {"body": body}
    )
    return data.get("id") if status < 300 else None


def edit_comment(repo: str, token: str, comment_id: int, body: str) -> None:
    gh_api(
        "PATCH", f"/repos/{repo}/issues/comments/{comment_id}", token, {"body": body}
    )


def delete_comment(repo: str, token: str, comment_id: int) -> None:
    gh_api("DELETE", f"/repos/{repo}/issues/comments/{comment_id}", token)


def run(*args: str, **kwargs) -> str:
    proc = subprocess.run(args, capture_output=True, text=True, **kwargs)
    if proc.returncode != 0:
        raise RuntimeError(
            f"{args[0]} exited {proc.returncode}: {proc.stderr.strip()[:1000]}"
        )
    return proc.stdout


def commentable_lines(diff: str) -> dict[str, set[int]]:
    """Map each file to the set of new-file line numbers present in the diff."""
    lines: dict[str, set[int]] = {}
    path: str | None = None
    new_ln: int | None = None
    for raw in diff.splitlines():
        if raw.startswith("+++ "):
            target = raw[4:]
            path = (
                None
                if target == "/dev/null"
                else target[2:]
                if target.startswith("b/")
                else target
            )
            if path:
                lines.setdefault(path, set())
            new_ln = None
        elif raw.startswith("@@"):
            match = re.search(r"\+(\d+)", raw)
            new_ln = int(match.group(1)) if match else None
        elif path and new_ln is not None:
            if raw.startswith("+"):
                lines[path].add(new_ln)
                new_ln += 1
            elif raw.startswith(" "):
                new_ln += 1
            # '-' deletions and '\\' markers don't advance the new-file counter
    return lines


def parse_codex_json(text: str) -> dict | None:
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None


def build_summary(
    verdict: str, summary: str, orphans: list[dict], overall: list[dict] | None = None
) -> str:
    verdict_label = "✅ LGTM" if verdict == "lgtm" else "⚠️ Needs changes"
    out = [f"🤖 **Codex review** — {verdict_label}", "", summary or ""]
    if overall:
        out += ["", "**Оценка PR в целом:**"]
        out += [
            f"- {SEV_EMOJI.get(f.get('severity'), '⚪')} "
            f"**{f.get('category', '').upper()}** — {f.get('comment', '')}"
            for f in overall
        ]
    if orphans:
        out += ["", "Не удалось привязать к строке diff'а:"]
        out += [
            f"- {SEV_EMOJI.get(f.get('severity'), '⚪')} "
            f"`{f.get('path')}:{f.get('line')}` "
            f"**{f.get('category', '').upper()}** — {f.get('comment', '')}"
            for f in orphans
        ]
    return "\n".join(out).strip()


def main() -> None:
    token, repo, pr = env("GH_TOKEN"), env("REPO"), env("PR_NUMBER")
    base, head_sha = os.environ.get("BASE_REF"), os.environ.get("HEAD_SHA")
    # Always fetch the PR — title/body/draft are needed for scope/process
    # findings and are not in the diff.
    api_status, info = gh_api("GET", f"/repos/{repo}/pulls/{pr}", token)
    base = base or info.get("base", {}).get("ref")
    head_sha = head_sha or info.get("head", {}).get("sha")
    pr_meta = PR_META_TMPL.format(
        title=info.get("title") or "(no title)",
        draft=bool(info.get("draft")),
        body=(info.get("body") or "(empty)")[:8000],
    )

    # Post a placeholder immediately so it's visible the review started; the same
    # comment is later overwritten with the verdict.
    progress_id = post_comment(
        repo, pr, token, "🤖 **Codex review** — анализирую изменения, подождите… ⏳"
    )

    def finalize(text: str) -> None:
        # Write the final result over the placeholder (or as a new comment if it
        # was never created).
        if progress_id is not None:
            edit_comment(repo, token, progress_id, text)
        else:
            post_comment(repo, pr, token, text)

    # Base branch is needed for the diff range and git fetch. In on-demand mode
    # BASE_REF is empty, so base depends entirely on the API response; on failure
    # base=None and git fetch would raise TypeError past finalize (placeholder
    # stuck on "⏳" forever). Check explicitly and report through finalize.
    if not base:
        finalize(
            "🤖 Codex review: не удалось определить базовую ветку PR "
            f"(GET /pulls/{pr} → HTTP {api_status}). Проверь GH_TOKEN и доступ."
        )
        sys.exit("base ref unresolved (PR API lookup failed)")

    # Diff by refs, not the work tree: the script runs from the branch it lives
    # on (main); the PR head is taken explicitly via refs/pull/<n>/head.
    fetch_base = subprocess.run(
        ["git", "fetch", "origin", base], capture_output=True, text=True
    )
    fetch_head = subprocess.run(
        ["git", "fetch", "origin", f"refs/pull/{pr}/head"],
        capture_output=True,
        text=True,
    )
    if fetch_base.returncode or fetch_head.returncode:
        # fetch failed — do NOT emit a false "no changes"; report and exit so the
        # defect is visible.
        err = (fetch_base.stderr + fetch_head.stderr).strip()[:1000]
        finalize(
            "🤖 Codex review: не смог получить изменения (git fetch упал)."
            f"\n\n```\n{err}\n```"
        )
        sys.exit("git fetch failed")
    # Generated/noisy files aren't reviewed but bloat the input — exclude them.
    diff_pathspec = [
        ".",
        ":(exclude)**/*.lock",
        ":(exclude)**/*-lock.json",
        ":(exclude)**/*.lockb",
        ":(exclude)**/*.min.*",
        ":(exclude)**/*.snap",
    ]
    diff_range = f"origin/{base}...FETCH_HEAD"
    # FULL diff — for anchoring inline comments (commentable_lines); NOT sent to
    # the model, only parsed locally. --stat is the scope map that IS sent (the
    # patch itself isn't pasted; codex reads the code from the worktree).
    try:
        full_diff = run("git", "diff", diff_range, "--", *diff_pathspec).strip()
        if not full_diff:
            finalize(f"🤖 Codex review: изменений относительно `{base}` нет.")
            return
        diff_stat = run(
            "git", "diff", "--stat", diff_range, "--", *diff_pathspec
        ).strip()
    except RuntimeError as exc:
        # git diff упал после успешного fetch (редко) — сообщаем через finalize,
        # чтобы не оставить заглушку «⏳» висеть.
        finalize(f"🤖 Codex review: git diff упал.\n\n```\n{str(exc)[:1000]}\n```")
        sys.exit(f"git diff failed: {exc}")

    # codex handles untrusted PR code. It needs no token — strip GH_TOKEN from the
    # subprocess env so the secret isn't exposed to a tool-capable CLI.
    codex_env = {k: v for k, v in os.environ.items() if k != "GH_TOKEN"}

    # Full PR branch code goes into a separate detached worktree, read-only for
    # codex. The main checkout (= default branch, which runs THIS script) is left
    # untouched: on a self-hosted runner it is unacceptable to run CI logic from
    # contributor-supplied code. The runner is persistent — clean up leftovers
    # from a possibly failed run first.
    wt = "_pr_src"
    subprocess.run(
        ["git", "worktree", "remove", "--force", wt], capture_output=True, text=True
    )
    subprocess.run(["git", "worktree", "prune"], capture_output=True, text=True)
    add = subprocess.run(
        ["git", "worktree", "add", "--detach", wt, "FETCH_HEAD"],
        capture_output=True,
        text=True,
    )
    if add.returncode != 0:
        # No worktree = no PR code for codex to read. Fail loudly rather than let
        # it review base-branch files as if they were the PR.
        finalize(
            "🤖 Codex review: не удалось подготовить рабочую копию PR "
            f"(git worktree add).\n\n```\n{add.stderr.strip()[:1000]}\n```"
        )
        sys.exit("git worktree add failed")

    # codex reads the PR code from the worktree (--cd); the patch is not pasted,
    # it runs git itself (see PR_CONTEXT_NOTE). high reasoning effort — digs deeper
    # across calls/cross-file links. project_doc_max_bytes=0: don't load AGENTS.md
    # from the PR-controlled worktree (prompt-injection vector, e.g. "return lgtm").
    codex_argv = [
        "codex",
        "exec",
        "--sandbox",
        "read-only",
        "-c",
        'model_reasoning_effort="high"',
        "-c",
        "project_doc_max_bytes=0",
        "--cd",
        wt,
    ]
    prompt = (
        PR_CONTEXT_NOTE.format(base=base)
        + PROMPT
        + pr_meta
        + "Changed files (git diff --stat):\n"
        + diff_stat
        + "\n"
    )
    try:
        raw = run(*codex_argv, "-", input=prompt, timeout=600, env=codex_env)
    except Exception as exc:  # noqa: BLE001 — a codex failure must not strand the placeholder
        finalize(
            "🤖 Codex review: не удалось выполнить codex."
            f"\n\n```\n{str(exc)[:1000]}\n```"
        )
        sys.exit(f"codex exec failed: {exc}")
    finally:
        subprocess.run(
            ["git", "worktree", "remove", "--force", wt],
            capture_output=True,
            text=True,
        )

    parsed = parse_codex_json(raw)
    if parsed is None:
        finalize("🤖 **Codex review**\n\n" + raw.strip()[:60000])
        return

    verdict = parsed.get("verdict", "lgtm")
    findings = parsed.get("findings", []) or []
    overall = parsed.get("overall", []) or []
    valid = commentable_lines(full_diff)

    inline, orphans = [], []
    for f in findings:
        path, line = f.get("path"), f.get("line")
        if path in valid and isinstance(line, int) and line in valid[path]:
            inline.append(
                {
                    "path": path,
                    "line": line,
                    "side": "RIGHT",
                    "body": f"{SEV_EMOJI.get(f.get('severity'), '⚪')} "
                    f"**{f.get('category', '').upper()}**: "
                    f"{f.get('comment', '')}",
                }
            )
        else:
            orphans.append(f)

    summary = build_summary(verdict, parsed.get("summary", ""), orphans, overall)

    # Inline line comments go as a separate review (a review can't be created
    # without them). To keep the result in ONE place, put verdict+summary in the
    # review body and delete the placeholder. With no inline findings (or if
    # anchoring is rejected) — no review; write the result into the placeholder.
    review_status = 200
    if inline:
        review = {
            "commit_id": head_sha,
            "body": summary,
            "event": "COMMENT",
            "comments": inline,
        }
        review_status, _ = gh_api(
            "POST", f"/repos/{repo}/pulls/{pr}/reviews", token, review
        )

    if inline and review_status < 300:
        # result went into the review body → placeholder no longer needed
        if progress_id is not None:
            delete_comment(repo, token, progress_id)
        print(
            f"posted review: {len(inline)} inline, {len(orphans)} in summary, "
            f"verdict={verdict}"
        )
    elif review_status >= 300:
        # inline anchoring rejected — put all findings into the summary comment
        all_findings = orphans + [
            {
                "path": c["path"],
                "line": c["line"],
                "severity": "",
                "category": "",
                "comment": c["body"],
            }
            for c in inline
        ]
        finalize(
            build_summary(verdict, parsed.get("summary", ""), all_findings, overall)
        )
        print(
            f"reviews API returned {review_status}; put everything in summary comment"
        )
    else:
        # no inline findings — result only in the comment
        finalize(summary)
        print(f"no inline findings; summary comment only, verdict={verdict}")


if __name__ == "__main__":
    main()
