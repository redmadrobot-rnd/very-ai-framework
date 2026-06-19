"""Codex PR reviewer — posts one GitHub review with inline comments.

Flow:
  1. compute the PR diff against the base branch;
  2. ask `codex exec` for structured JSON findings (subscription auth on the runner);
  3. keep only findings that land on a line actually present in the diff;
  4. post a single PR review with inline threads + a summary body (verdict).

Findings that can't be anchored to a diff line, and any parse/post failure,
degrade into a plain summary comment — the job never hard-fails on review noise.

Env (provided by the workflow):
  GH_TOKEN, REPO (owner/repo), PR_NUMBER, BASE_REF, HEAD_SHA
"""

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

PROMPT = """\
You are a senior code reviewer. Review the git diff below and reply with ONLY a JSON
object (no markdown, no prose, no code fences). Schema:

{
  "verdict": "lgtm" | "needs_changes",
  "summary": "<one or two sentence overall assessment>",
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

Focus on bugs, security and performance. Skip trivial style nits. Do NOT modify
any files. If nothing is notable, return an empty "findings" array and verdict "lgtm".

Diff:
"""


def env(name: str) -> str:
    val = os.environ.get(name)
    if not val:
        sys.exit(f"missing env: {name}")
    return val


def gh_api(method: str, path: str, token: str, payload: dict | None = None) -> tuple[int, dict]:
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


def run(*args: str, **kwargs) -> str:
    return subprocess.run(args, capture_output=True, text=True, **kwargs).stdout


def commentable_lines(diff: str) -> dict[str, set[int]]:
    """Map each file to the set of new-file line numbers present in the diff."""
    lines: dict[str, set[int]] = {}
    path: str | None = None
    new_ln: int | None = None
    for raw in diff.splitlines():
        if raw.startswith("+++ "):
            target = raw[4:]
            path = None if target == "/dev/null" else target[2:] if target.startswith("b/") else target
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
            # '-' deletions and '\' markers don't advance the new-file counter
    return lines


def parse_codex_json(text: str) -> dict | None:
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None


def build_summary(verdict: str, summary: str, orphans: list[dict]) -> str:
    verdict_label = "✅ LGTM" if verdict == "lgtm" else "⚠️ Needs changes"
    out = [f"🤖 **Codex review** — {verdict_label}", "", summary or ""]
    if orphans:
        out += ["", "Не удалось привязать к строке diff'а:"]
        out += [
            f"- {SEV_EMOJI.get(f.get('severity'), '⚪')} `{f.get('path')}:{f.get('line')}` "
            f"**{f.get('category', '').upper()}** — {f.get('comment', '')}"
            for f in orphans
        ]
    return "\n".join(out).strip()


def main() -> None:
    token, repo, pr = env("GH_TOKEN"), env("REPO"), env("PR_NUMBER")
    base, head_sha = os.environ.get("BASE_REF"), os.environ.get("HEAD_SHA")
    if not base or not head_sha:
        # путь «по комментарию» не несёт base/head в событии — берём из API
        _, info = gh_api("GET", f"/repos/{repo}/pulls/{pr}", token)
        base = base or info["base"]["ref"]
        head_sha = head_sha or info["head"]["sha"]

    # Диффим по ref'ам, не по рабочему дереву: скрипт запускается из ветки,
    # где он лежит (main), а PR-head берём явно через refs/pull/<n>/head.
    subprocess.run(["git", "fetch", "origin", base], check=False)
    subprocess.run(["git", "fetch", "origin", f"refs/pull/{pr}/head"], check=False)
    diff = run("git", "diff", f"origin/{base}...FETCH_HEAD").strip()
    if not diff:
        gh_api(
            "POST",
            f"/repos/{repo}/issues/{pr}/comments",
            token,
            {"body": f"🤖 Codex review: изменений относительно `{base}` нет."},
        )
        return

    raw = run("codex", "exec", PROMPT + diff, timeout=600)
    parsed = parse_codex_json(raw)
    if parsed is None:
        gh_api(
            "POST",
            f"/repos/{repo}/issues/{pr}/comments",
            token,
            {"body": "🤖 **Codex review**\n\n" + raw.strip()[:60000]},
        )
        return

    verdict = parsed.get("verdict", "lgtm")
    findings = parsed.get("findings", []) or []
    valid = commentable_lines(diff)

    inline, orphans = [], []
    for f in findings:
        path, line = f.get("path"), f.get("line")
        if path in valid and isinstance(line, int) and line in valid[path]:
            inline.append(
                {
                    "path": path,
                    "line": line,
                    "side": "RIGHT",
                    "body": f"{SEV_EMOJI.get(f.get('severity'), '⚪')} **{f.get('category', '').upper()}**: "
                    f"{f.get('comment', '')}",
                }
            )
        else:
            orphans.append(f)

    body = build_summary(verdict, parsed.get("summary", ""), orphans)
    review = {"commit_id": head_sha, "body": body, "event": "COMMENT", "comments": inline}
    status, _ = gh_api("POST", f"/repos/{repo}/pulls/{pr}/reviews", token, review)

    if status >= 300:
        # inline anchoring rejected — fall back to a plain summary comment
        all_findings = orphans + [
            {"path": c["path"], "line": c["line"], "severity": "", "category": "", "comment": c["body"]} for c in inline
        ]
        gh_api(
            "POST",
            f"/repos/{repo}/issues/{pr}/comments",
            token,
            {"body": build_summary(verdict, parsed.get("summary", ""), all_findings)},
        )
        print(f"reviews API returned {status}; posted summary comment instead")
    else:
        print(f"posted review: {len(inline)} inline, {len(orphans)} in summary, verdict={verdict}")


if __name__ == "__main__":
    main()
