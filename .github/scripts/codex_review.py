"""Codex PR reviewer — posts a live status comment, then fills it with the verdict.

Flow:
  1. immediately post a placeholder comment ("анализирую… ⏳") so it's visible the job started;
  2. compute the PR diff against the base branch;
  3. ask `codex exec` for structured JSON findings (subscription auth on the runner);
  4. keep only findings that land on a line actually present in the diff;
  5. post inline threads as a PR review, and edit the placeholder into the summary (verdict).

Findings that can't be anchored to a diff line, and any parse/post failure,
degrade into the summary comment — the job never hard-fails on review noise.

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


def post_comment(repo: str, pr: str, token: str, body: str) -> int | None:
    """Создать issue-коммент на PR. Возвращает его id (или None при ошибке)."""
    status, data = gh_api("POST", f"/repos/{repo}/issues/{pr}/comments", token, {"body": body})
    return data.get("id") if status < 300 else None


def edit_comment(repo: str, token: str, comment_id: int, body: str) -> None:
    """Перезаписать тело ранее созданного issue-коммента."""
    gh_api("PATCH", f"/repos/{repo}/issues/comments/{comment_id}", token, {"body": body})


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

    # Сразу постим коммент-заглушку — сигнал, что ревью стартовало; его же обновим вердиктом.
    progress_id = post_comment(repo, pr, token, "🤖 **Codex review** — анализирую изменения, подождите… ⏳")

    def finalize(text: str) -> None:
        """Финальный результат — поверх заглушки (или новым комментом, если её создать не вышло)."""
        if progress_id is not None:
            edit_comment(repo, token, progress_id, text)
        else:
            post_comment(repo, pr, token, text)

    # Диффим по ref'ам, не по рабочему дереву: скрипт запускается из ветки,
    # где он лежит (main), а PR-head берём явно через refs/pull/<n>/head.
    fetch_base = subprocess.run(["git", "fetch", "origin", base], capture_output=True, text=True)
    fetch_head = subprocess.run(["git", "fetch", "origin", f"refs/pull/{pr}/head"], capture_output=True, text=True)
    if fetch_base.returncode or fetch_head.returncode:
        # fetch упал — НЕ выдаём ложное «изменений нет»: сообщаем и падаем, чтобы дефект был виден.
        err = (fetch_base.stderr + fetch_head.stderr).strip()[:1000]
        finalize(f"🤖 Codex review: не смог получить изменения (git fetch упал).\n\n```\n{err}\n```")
        sys.exit("git fetch failed")
    diff = run("git", "diff", f"origin/{base}...FETCH_HEAD").strip()
    if not diff:
        finalize(f"🤖 Codex review: изменений относительно `{base}` нет.")
        return

    # codex обрабатывает недоверенный текст диффа (его пишет автор PR). Токен ему не нужен —
    # убираем GH_TOKEN из окружения подпроцесса, чтобы не отдавать секрет tool-capable CLI.
    codex_env = {k: v for k, v in os.environ.items() if k != "GH_TOKEN"}
    try:
        raw = run("codex", "exec", PROMPT + diff, timeout=600, env=codex_env)
    except Exception as exc:  # noqa: BLE001 — любой сбой codex не должен оставить заглушку висеть
        finalize(f"🤖 Codex review: не удалось выполнить codex.\n\n```\n{str(exc)[:1000]}\n```")
        sys.exit(f"codex exec failed: {exc}")

    parsed = parse_codex_json(raw)
    if parsed is None:
        finalize("🤖 **Codex review**\n\n" + raw.strip()[:60000])
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

    summary = build_summary(verdict, parsed.get("summary", ""), orphans)

    # Inline-замечания по строкам — отдельным review (без них review создавать нельзя).
    # Итоговый вердикт держим в коммент-заглушке, поэтому тело review — короткий указатель.
    review_status = 200
    if inline:
        review = {
            "commit_id": head_sha,
            "body": "🤖 Codex review — построчные замечания ниже; итог в комментарии Codex.",
            "event": "COMMENT",
            "comments": inline,
        }
        review_status, _ = gh_api("POST", f"/repos/{repo}/pulls/{pr}/reviews", token, review)

    if review_status >= 300:
        # привязка inline отклонена — складываем все находки в итоговый коммент
        all_findings = orphans + [
            {"path": c["path"], "line": c["line"], "severity": "", "category": "", "comment": c["body"]} for c in inline
        ]
        finalize(build_summary(verdict, parsed.get("summary", ""), all_findings))
        print(f"reviews API returned {review_status}; put everything in summary comment")
    else:
        finalize(summary)
        print(f"posted review: {len(inline)} inline, {len(orphans)} in summary, verdict={verdict}")


if __name__ == "__main__":
    main()
