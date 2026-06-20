---
description: Render the knowledge-base ontology as a self-contained HTML graph — documents as nodes (colored by node_type), typed links as edges (frontmatter links + inline). Offline, zero-dependency. Use to get a visual overview of the KB and how docs connect.
allowed-tools: Bash(python3:*)
---

Build the KB ontology graph for: `$ARGUMENTS` (optional output path; default `docs/kb-graph.html`).

This is a **view**, derived from markdown — regenerate any time, never commit the HTML.

1. Build it:
   `python3 .claude/skills/kb-search/graph.py -o "${ARGUMENTS:-docs/kb-graph.html}"`
2. Report the output path and the node/edge counts the script prints.
3. Point the user to open the file in a browser (force-directed, drag/zoom; nodes
   colored by `node_type`, edges by link type).

The graph reads the same ontology the linter checks (`kb-maintain` / `docs/gitmark/ontology.md`):
typed `links:` plus inline md links. If links look sparse, the KB is under-connected —
run `gitmark lint` (I3 orphans) and add typed links.
