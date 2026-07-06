# AGENTS.md - Codex working rules

## Repository Role

This repository is a reference framework, not an application service. It contains:

- an agent development flow;
- GitMark knowledge-base tooling;
- GitHub Actions CI/CD templates;
- setup skills that install the framework into a target repository.

Do not invent an app deployment for this repo. A real server deploy applies to a target
repository after the framework has been installed there and the target has services,
Dockerfiles, `docker-compose.yml`, GitHub environments, and deploy credentials.

## Work Rules

- Work from a branch or worktree, not directly on `main`.
- Inspect relevant files before changing them.
- Keep changes scoped to the requested framework behavior.
- Before substantial edits, state the intended scope, risk, and acceptance criteria.
- Verify with deterministic checks: GitMark lint/index, shell script smoke tests,
  Ruff/pre-commit, or the smallest command that proves the change.
- Do not read or mutate secrets, private keys, auth files, `.env` files, or GitHub
  environment values unless the user explicitly requests a security/setup task for them.

## Codex Setup Path

For Codex-first rollout into another repository, use
`skills/setup-framework-codex/SKILL.md`. It installs the same three pillars as the
Claude setup path, but writes repo-local instructions to `AGENTS.md` and uses `.codex/`
for skills/commands when the target wants Codex-native assets.

The older Claude setup path remains in `skills/setup-framework/SKILL.md` for teams that
still use Claude Code. Keep both paths behaviorally aligned when changing the framework.

## Knowledge Base

Markdown under `docs/gitmark/` is the source of truth. The `.gitmark/` index is derived
and must not be committed.

Use the resolver when possible:

```bash
bash .github/scripts/gitmark.sh lint --strict
bash .github/scripts/gitmark.sh index
bash .github/scripts/gitmark.sh stat
```

The resolver prefers `.codex/skills/kb-search/gitmark.py` and falls back to
`.claude/skills/kb-search/gitmark.py`, so the same CI/pre-commit files work in both
agent layouts.
