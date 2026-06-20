---
name: kb-search
description: Search the project's markdown knowledge base via the GitMark CLI instead of grep cmd. 
  Use to find where something is documented, answer "where do the docs say X", or before reading many files at random;
  Hits come back as `file:line · heading · snippet`.

---

# kb-search — search the knowledge base (GitMark)

The repo's markdown is a **md + README-index + git** knowledge base. Markdown is the source
of truth; the search index is derived and regenerated from it

```bash
G="python3 .claude/skills/kb-search/gitmark.py"   # paths are relative to the project root
```

## How to run (including `/kb-search <query>`)

1. Index stale (docs changed since last build)? → `$G index` (rebuild, fast).
2. `$G search "<query>" -k 8` — `[bm25]` exact term · `[trigram]` substring · `[fuzzy]` typos/forms/Cyrillic.
3. Summarize the top hits (`file:line`), open the 1–2 most relevant, and answer — don't reprint whole snippets.

- Bare `/kb-search` (no query) → `$G stat` + show the `/kb-search <query>` syntax.
- Add `--json` for machine-readable output.
- Visual overview of the whole KB → `/kb-graph` (separate script `graph.py`; HTML ontology graph).

## Principles

- **Markdown is the source of truth** — edit `.md`, never the index.
- **Don't commit `.gitmark/`** — it's a gitignored, rebuildable cache.
- Maintaining the KB (types, placement, links) → the `kb-maintain` skill.
