---
description: Build the ENTIRE knowledge base for this repo — survey the codebase, then dispatch kb-maintain curator agents per area to produce docs/ (per-service READMEs, reference specs, runbooks, decisions, entry point) following the ontology, then lint + index. Use to bootstrap or rebuild a project's whole KB.
allowed-tools: Bash(python3:*), Task
---

Build (or rebuild) the **whole knowledge base** for this repository by fanning out curator agents.

Scope hint: `$ARGUMENTS` — empty = whole repo; or a subset (e.g. `services/api services/billing`, or "only reference docs").

## Plan

1. **Survey the repo** — map top-level dirs, services/modules, entry points, build/deploy
   files, and existing docs. Check current coverage:
   `python3 .claude/skills/kb-search/gitmark.py stat`.

2. **Decompose into doc areas** — one unit of work per area (all under `docs/gitmark/`):
   - each service/component → `docs/gitmark/services/<svc>/README.md` (`node_type: service`)
   - cross-cutting specs (architecture, billing, limits, security) → `docs/gitmark/reference/` (`reference`)
   - operational procedures → `docs/gitmark/ops/` (`runbook` / `gotcha`)
   - architectural decisions → `docs/gitmark/decisions/` (`decision`)

3. **Dispatch curators (fan-out)** — for each area spawn a **subagent (Task)** that follows the
   `kb-maintain` skill on that slice only:
   - search first (don't duplicate); pick `node_type` + correct folder;
   - write frontmatter (`node_type`, `title`, `service`, `status: active`, `updated`);
   - add ≥1 typed link to the code it documents (`documents:[src/…]` / `implemented_by`);
   - add a line to the folder `README.md` index.
   Run independent areas in parallel; keep each agent scoped to its area to avoid collisions.

4. **Entry point + indexes** — `docs/gitmark/README.md` is the master index of the KB,
   and every folder under `docs/gitmark/` has a README index.

5. **Verify & derive** —
   `python3 .claude/skills/kb-search/gitmark.py lint` (fix broken links / orphans /
   missing frontmatter), then `... gitmark.py index`.

6. **Report** — how many docs created/updated, KB coverage before→after, and the lint result.
   List any areas that need a human decision.

Principle: md+git is the source of truth; the derived search index is regenerated. Never
duplicate — edit existing docs. Keep curator agents scoped so they don't fight over files.
