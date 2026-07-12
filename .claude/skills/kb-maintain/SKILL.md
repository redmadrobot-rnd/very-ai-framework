---
name: kb-maintain
description: >
  Rules for maintaining a markdown knowledge base (KB/GitMark) — apply when adding, editing, moving, or deleting documentation (.md). 
  Keeps the KB structured instead of a pile of files. Use on "add a doc", "record a decision", "update the docs", 
  "reorganize docs", "rebuild the KB"
---

# kb-maintain — how to maintain the knowledge base (GitMark ontology)

The KB lives under **`docs/gitmark/`** (only this tree is scanned/linted; the rest of
`docs/` is free for non-KB material). Full model: `docs/gitmark/ontology.md`. This skill
is the operational checklist. Principle:
**md+git is the source of truth, with an ontology on top** (object types / properties /
links — inspired by Palantir Foundry/Gotham, but for documentation over code).

## Before writing — search, don't duplicate

```bash
python3 .claude/skills/kb-search/gitmark.py search "<topic>"
```
If the topic already exists — **edit the existing doc**, don't create a second one.
Adding/updating a `plan` or `report`? Add `--scope all` — default search hides historical
docs, so without it the duplicate check misses an existing plan/report.

## When ADDING knowledge (CREATE)

1. **Pick a `node_type`**: `service` · `reference` · `runbook` · `gotcha` · `decision`
   · `plan` · `guide` · `report` · `index`. Unsure → spec = `reference`, how-to = `guide`.
2. **Put it in the right folder** (type → folder, all under `docs/gitmark/`): service-specific →
   `docs/gitmark/services/<svc>/`; cross-cutting → `docs/gitmark/reference/`; ops procedure →
   `docs/gitmark/ops/`; plan → `docs/gitmark/plans/`; report → `docs/gitmark/reviews/`;
   decision → `docs/gitmark/decisions/`.
3. **Add frontmatter** (min `node_type`; for load-bearing docs also `title`, `service`,
   `status: active`, `updated: YYYY-MM-DD`):
   ```yaml
   ---
   node_type: runbook
   title: Deploy the gateway
   service: api
   status: active
   updated: 2026-06-06
   links:
     documents: [scripts/deploy.sh]
     depends_on: [docs/gitmark/reference/architecture.md]
   ---
   ```
4. **Add ≥1 link** — to code (`documents`/`implemented_by`) or a sibling doc
   (`depends_on`/`relates_to`). No orphans. **Paths are relative to the project root** (no `../`).
5. **Add a line to the folder's `README.md`** (its index): `- [Title](file.md) — hook`.

## When EDITING (UPDATE)

- Meaning changed → bump `updated:`. Doc is stale → `status: deprecated` and set
  `supersedes: [old.md]` on the replacement. Junk → delete (git keeps history).

## When MOVING (reorganizing)

- `git mv` (preserves history), then **rewrite every link** to it and update the
  README indexes of both folders.

## Always at the end

```bash
python3 .claude/skills/kb-search/gitmark.py lint     # invariants I1–I6
python3 .claude/skills/kb-search/gitmark.py index    # rebuild search
```
`lint` reports a violation per code — fix until clean:

- **I1** — a load-bearing doc has no frontmatter / no valid `node_type`.
- **I2** — `node_type` or `status` is a value outside its vocabulary.
- **I3** — orphan: a load-bearing doc has no links in **or** out.
- **I4** — broken link: a markdown link points to a file missing on disk.
- **I5** — a `docs/gitmark/` folder has no `README.md` index.
- **I6** — a `supersedes` target isn't marked `deprecated`/`archived`.

## Vocabularies

Controlled values (`node_type`, `status`) and the full link-type table live in
**`docs/gitmark/ontology.md`** — don't invent values, consult it. `service` is free-form
(name the component; `_platform` for cross-cutting).
