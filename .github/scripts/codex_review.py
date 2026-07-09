"""Codex PR reviewer — posts a live status comment, then fills it with the verdict.

Flow:
  1. immediately post a placeholder comment ("анализирую… ⏳") so it's visible the job started;
  2. compute the PR diff against the base branch;
  3. ask `codex exec` for structured JSON findings (subscription auth on the runner);
  4. keep only findings that land on a line actually present in the diff;
  5. if there are inline findings, post ONE PR review carrying the verdict/summary in its
     body plus the inline threads, then delete the placeholder (single surface). With no
     inline findings, edit the placeholder into the summary instead.

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
MAX_DIFF = 200000  # почти весь дифф влезает в контекст; урезание = пропущенные находки

# Префикс с контекстом ветки добавляется ТОЛЬКО когда worktree реально поднят
# (codex запущен в коде PR). Иначе промпт не должен заявлять о доступности файлов —
# иначе codex прочитает файлы default-ветки, приняв их за PR (вводящее в заблуждение ревью).
PR_CONTEXT_NOTE = (
    "Your working directory is the PR branch checked out as a git repository "
    "(HEAD = PR head, base branch = origin/{base}). You have read-only shell access. "
    "The patch is NOT pasted — only the changed-file list below. Get the actual changes "
    "yourself with git, and go beyond them as needed:\n"
    "  - `git diff origin/{base}...HEAD` — full change (append `-- <path>` for one file);\n"
    "  - `git log --oneline origin/{base}..HEAD` and `git show <rev>` — commit history/intent;\n"
    "  - `git grep -n <symbol>` (or grep the tree) — find callers, definitions and other "
    "uses of anything the PR touches, across the WHOLE codebase;\n"
    "  - `git blame`, and read any file in full for imports, callers, type defs, neighbours.\n"
    "Review scope = the files this PR changes (see 'Changed files' below). Read the rest of "
    "the codebase as needed to judge the CONSEQUENCES of those changes (hot-path callers, "
    "duplicated logic elsewhere, broken contracts). Prefer running git/grep over guessing.\n\n"
)

PROMPT = """\
You are a senior code reviewer. Review the pull request and reply with ONLY a JSON
object (no markdown, no prose, no code fences). Schema:

{
  "verdict": "lgtm" | "needs_changes",
  "summary": "<two or three sentence overall assessment: what the PR does and the headline verdict>",
  "overall": [
    {
      "severity": "high" | "medium" | "low",
      "category": "scope" | "process" | "architecture" | "correctness" | "security" | "performance" | "testing",
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
The full PR branch is checked out; the diff shows WHAT changed but not its consequences.
For every changed file and every added/modified function you MUST investigate beyond the
diff using the checked-out code:
  - Trace callers and call sites: is a newly added call on a hot path (per-request, per
    message, inside a loop)? Does it add I/O (DB query, network, S3, file read) that runs
    even when unnecessary? Flag added latency/cost on hot paths.
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
   Assess, as applicable to THIS change (skip what doesn't apply — do not force a point):
   - Scope & focus: does the change do what its title/description say, and only that?
     Flag unrelated work bundled in (drive-by refactors, formatting, infra, config) that
     should be a separate change, and any stated intent the diff does not actually deliver.
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

# Блок с метаданными PR (title/body/draft) — даёт модели то, чего в diff нет: заявленную
# цель, границы MVP/«stage 2» и признак незавершённости (draft). Без него scope-creep и
# draft-мёрдж находки невозможны в принципе.
PR_META_TMPL = "Pull request under review:\nTitle: {title}\nDraft: {draft}\nDescription:\n<<<PR_BODY\n{body}\nPR_BODY\n\n"


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
    """Создать issue-коммент на PR. Возвращает его id (или None при ошибке)."""
    status, data = gh_api(
        "POST", f"/repos/{repo}/issues/{pr}/comments", token, {"body": body}
    )
    return data.get("id") if status < 300 else None


def edit_comment(repo: str, token: str, comment_id: int, body: str) -> None:
    """Перезаписать тело ранее созданного issue-коммента."""
    gh_api(
        "PATCH", f"/repos/{repo}/issues/comments/{comment_id}", token, {"body": body}
    )


def delete_comment(repo: str, token: str, comment_id: int) -> None:
    """Удалить issue-коммент (заглушку), когда итог уезжает в body ревью."""
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
            f"- {SEV_EMOJI.get(f.get('severity'), '⚪')} **{f.get('category', '').upper()}** — {f.get('comment', '')}"
            for f in overall
        ]
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
    # Всегда тянем PR — нужны title/body/draft для scope/process-находок (в diff их нет).
    _, info = gh_api("GET", f"/repos/{repo}/pulls/{pr}", token)
    base = base or info.get("base", {}).get("ref")
    head_sha = head_sha or info.get("head", {}).get("sha")
    pr_meta = PR_META_TMPL.format(
        title=info.get("title") or "(no title)",
        draft=bool(info.get("draft")),
        body=(info.get("body") or "(empty)")[:8000],
    )

    # Сразу постим коммент-заглушку — сигнал, что ревью стартовало; его же обновим вердиктом.
    progress_id = post_comment(
        repo, pr, token, "🤖 **Codex review** — анализирую изменения, подождите… ⏳"
    )

    def finalize(text: str) -> None:
        """Финальный результат — поверх заглушки (или новым комментом, если её создать не вышло)."""
        if progress_id is not None:
            edit_comment(repo, token, progress_id, text)
        else:
            post_comment(repo, pr, token, text)

    # Диффим по ref'ам, не по рабочему дереву: скрипт запускается из ветки,
    # где он лежит (main), а PR-head берём явно через refs/pull/<n>/head.
    fetch_base = subprocess.run(
        ["git", "fetch", "origin", base], capture_output=True, text=True
    )
    fetch_head = subprocess.run(
        ["git", "fetch", "origin", f"refs/pull/{pr}/head"],
        capture_output=True,
        text=True,
    )
    if fetch_base.returncode or fetch_head.returncode:
        # fetch упал — НЕ выдаём ложное «изменений нет»: сообщаем и падаем, чтобы дефект был виден.
        err = (fetch_base.stderr + fetch_head.stderr).strip()[:1000]
        finalize(
            f"🤖 Codex review: не смог получить изменения (git fetch упал).\n\n```\n{err}\n```"
        )
        sys.exit("git fetch failed")
    # Сгенерённые/шумные файлы не ревьюятся, но раздувают вход — исключаем из дифа.
    diff_pathspec = [
        ".",
        ":(exclude)**/*.lock",
        ":(exclude)**/*-lock.json",
        ":(exclude)**/*.lockb",
        ":(exclude)**/*.min.*",
        ":(exclude)**/*.snap",
    ]
    diff_range = f"origin/{base}...FETCH_HEAD"
    # ПОЛНЫЙ дифф — для анкоринга inline-комментов (commentable_lines). В модель НЕ уходит
    # целиком; парсится локально, поэтому привязка работает для всех строк любого размера.
    full_diff = run("git", "diff", diff_range, "--", *diff_pathspec).strip()
    if not full_diff:
        finalize(f"🤖 Codex review: изменений относительно `{base}` нет.")
        return
    # --stat — полный список изменённых файлов + churn. Дёшев, НИКОГДА не режется: даже при
    # урезанном патче модель знает весь scope и дочитывает нужные файлы из worktree.
    diff_stat = run("git", "diff", "--stat", diff_range, "--", *diff_pathspec).strip()
    # Патч в промпт — до лимита; остаток модель добирает чтением файлов (worktree ниже).
    prompt_diff = full_diff
    if len(prompt_diff) > MAX_DIFF:
        prompt_diff = (
            prompt_diff[:MAX_DIFF]
            + "\n\n[патч обрезан по лимиту; полный список файлов — в Changed files выше, "
            "весь код ветки доступен в рабочей папке — дочитай файлы сам]"
        )

    # codex обрабатывает недоверенный код PR. Токен ему не нужен — убираем GH_TOKEN из
    # окружения подпроцесса, чтобы не отдавать секрет tool-capable CLI.
    codex_env = {k: v for k, v in os.environ.items() if k != "GH_TOKEN"}

    # Полный код ветки PR — в отдельный detached worktree, для codex ТОЛЬКО на чтение.
    # Главный чекаут (= default branch, откуда исполняется ЭТОТ скрипт) не трогаем: на
    # self-hosted раннере недопустимо выполнять CI-логику из присланного контрибьютором
    # кода. Раннер персистентный — сначала чистим остаток от возможного упавшего прогона.
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
    pr_cwd = wt if add.returncode == 0 else None
    if pr_cwd is None:
        # worktree не встал — деградируем к старому поведению (контекст = только дифф).
        print(f"worktree add failed, fallback diff-only: {add.stderr.strip()[:300]}")

    # Префикс о доступности файлов — ТОЛЬКО при поднятом worktree, иначе промпт врал бы
    # codex'у про PR-контекст и тот ревьюил бы файлы default-ветки как будто это PR.
    # high reasoning effort — глубже проходит по вызовам/крос-файловым связям (больше находок).
    codex_argv = [
        "codex",
        "exec",
        "--sandbox",
        "read-only",
        "-c",
        'model_reasoning_effort="high"',
    ]
    prompt = PROMPT + pr_meta + "Changed files (git diff --stat):\n" + diff_stat + "\n"
    if pr_cwd:
        # worktree поднят → патч не вставляем, codex берёт `git diff` сам (см. PR_CONTEXT_NOTE).
        codex_argv += ["--cd", pr_cwd]
        prompt = PR_CONTEXT_NOTE.format(base=base) + prompt
    else:
        # worktree не встал → codex не может достать дифф git'ом; вставляем патч (до лимита).
        prompt += "\nDiff:\n" + prompt_diff
    try:
        raw = run(*codex_argv, "-", input=prompt, timeout=600, env=codex_env)
    except Exception as exc:  # noqa: BLE001 — любой сбой codex не должен оставить заглушку висеть
        finalize(
            f"🤖 Codex review: не удалось выполнить codex.\n\n```\n{str(exc)[:1000]}\n```"
        )
        sys.exit(f"codex exec failed: {exc}")
    finally:
        if pr_cwd:
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
                    "body": f"{SEV_EMOJI.get(f.get('severity'), '⚪')} **{f.get('category', '').upper()}**: "
                    f"{f.get('comment', '')}",
                }
            )
        else:
            orphans.append(f)

    summary = build_summary(verdict, parsed.get("summary", ""), orphans, overall)

    # Inline-замечания по строкам — отдельным review (без них review создавать нельзя).
    # Чтобы итог жил в ОДНОМ месте, кладём вердикт+summary прямо в body ревью, а
    # коммент-заглушку удаляем. Если inline-находок нет (или привязка отклонена) —
    # ревью не создаём и пишем итог в заглушку, как раньше.
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
        # итог уехал в body ревью → заглушка больше не нужна
        if progress_id is not None:
            delete_comment(repo, token, progress_id)
        print(
            f"posted review: {len(inline)} inline, {len(orphans)} in summary, verdict={verdict}"
        )
    elif review_status >= 300:
        # привязка inline отклонена — складываем все находки в итоговый коммент
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
        # inline-находок нет — итог только в комменте
        finalize(summary)
        print(f"no inline findings; summary comment only, verdict={verdict}")


if __name__ == "__main__":
    main()
