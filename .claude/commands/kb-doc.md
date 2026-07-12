---
description: Compose or update a knowledge-base document for the given topic following the OntoShip ontology (node_type, frontmatter, typed links, folder README index). Wraps the kb-maintain skill.
allowed-tools: Bash(python3:*)
---

Compose or update a KB document for: `$ARGUMENTS`

Follow the `kb-maintain` skill (ontology over code):

1. **Search first** — `python3 .claude/skills/kb-search/gitmark.py search "$ARGUMENTS"`.
   If the topic already exists → **edit that doc**, don't create a second one. For a
   `plan`/`report` add `--scope all` (default search hides historical docs → missed dupes).
2. **Pick a `node_type`** — `service` · `reference` · `runbook` · `gotcha` · `decision` ·
   `plan` · `guide` · `report` · `index` (unsure → spec = `reference`, how-to = `guide`)
   and the **right folder** (all under `docs/gitmark/`: service → `docs/gitmark/services/<svc>/`,
   cross-cutting → `docs/gitmark/reference/`, ops → `docs/gitmark/ops/`, plan →
   `docs/gitmark/plans/`, report → `docs/gitmark/reviews/`, decision → `docs/gitmark/decisions/`).
   `plan`/`report` are historical — they stay in the KB but `search` hides them by default
   (`--scope all` to include them).
3. **Write frontmatter** — `node_type`, `title`, `service`, `status: active`, `updated: <today>`.
4. **Add ≥1 typed link** — to code (`documents`/`implemented_by`) or a sibling doc
   (`depends_on`/`relates_to`). No orphans.
5. **Add a line to the folder `README.md`** (its index): `- [Title](file.md) — hook`.
6. **Lint + reindex** — `python3 .claude/skills/kb-search/gitmark.py lint`
   then `... gitmark.py index`.

Report which file you created/updated, its `node_type`, and the links you added.
