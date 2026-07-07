# GitMark ontology — a knowledge model over code

> Rules for **how to maintain** a knowledge base (not just how to search it). The idea
> is borrowed from **Palantir's Ontology** (Gotham/Foundry): an organization is modeled
> as a graph of typed **objects**, their **properties**, and the **links** between them —
> a "digital twin." GitMark applies the same model to project documentation: every `.md`
> document is an **object** with a **type** and **properties**, and markdown links are
> **typed links**. The result is an ontology not of data, but of **knowledge over code**.

Why: so the KB doesn't rot into a pile of files. Type + properties + links make it
navigable and checkable (via the linter).

## Semantic layer — Objects, Properties, Links

### Object types (`node_type`)

Each document has exactly one `node_type` — its "table" in the ontology.

| node_type | what it is | lives in |
|---|---|---|
| `service` | overview/index of one service/component | `docs/gitmark/services/<svc>/README.md` |
| `reference` | cross-cutting spec (not about one service) | `docs/gitmark/reference/` |
| `runbook` | operational procedure ("how to X") | `docs/gitmark/ops/` |
| `gotcha` | a pitfall + how to avoid it | `docs/gitmark/ops/` |
| `decision` | an architectural/product decision (ADR) | `docs/gitmark/decisions/` |
| `plan` | a plan/design before implementation | `docs/gitmark/plans/` |
| `guide` | how to use something (clients, public API) | varies |
| `report` | a one-off dated analysis/audit | `docs/gitmark/reviews/` |
| `index` | a folder's table of contents | any `README.md` |

> The KB lives under `docs/gitmark/` (so the rest of `docs/` stays free for non-KB
> material). All folders below are relative to it; only this tree is scanned/linted.

**Living vs historical.** All docs stay in the ontology — typed, linked, linted, on the
graph. But `plan` and `report` are **historical**: dated snapshots (intent before build,
a one-off audit) that go stale — a `plan` diverges from what shipped, an audit's relevance
fades. So `search` hides them by default (`--scope live`); reach them with `--scope all`
(or `--scope history` for only those). Everything else is **living knowledge** and must stay
current; distill durable findings from a plan/report into `reference`/`gotcha`/`decision`.

Rule: if unsure, a spec is `reference`, a how-to is `guide`. Add a new type only if
none fit and there will be ≥3 such documents.

### Properties (frontmatter)

YAML frontmatter at the top of the file — the "columns" of the object row.

```yaml
---
node_type: service          # REQUIRED — one of the table above
title: Billing              # human-readable object name
service: billing            # which component; free-form, use _platform for cross-cutting
status: active              # active | draft | deprecated | archived
updated: 2026-06-06          # last meaningful edit (YYYY-MM-DD)
tags: [payments, api]       # free-form labels for search/grouping
links:                      # typed links (see below), optional
  documents: [src/billing]
  depends_on: [docs/gitmark/reference/architecture.md]
  supersedes: [docs/gitmark/services/billing/old-billing.md]
---
```

Required: `node_type`. Strongly recommended for load-bearing docs
(`service|reference|runbook|plan|decision`): `title`, `service`, `status`, `updated`.

`service` is a **free-form** label — the curator (human or agent) decides which
component a doc belongs to. It is not validated against a fixed vocabulary.

### Link types (`links`)

Links are markdown links `[text](path.md)`. The link type is declared by a key under
`links:`; inline links default to `relates_to`. The linter uses them to check for orphans
and broken links.

**Write all link paths relative to the project root** (`docs/gitmark/reference/x.md`, `src/billing`) —
frontmatter and inline links alike, never relative to the current file, so no `../` ladders.
Trade-off: inline prose links then aren't click-through in a plain markdown viewer (it
resolves them against the file's folder); gitmark and the linter resolve them correctly.

| link type | meaning | direction |
|---|---|---|
| `documents` | this doc describes that code/service | doc → code |
| `depends_on` | read that one first to understand this | doc → doc |
| `supersedes` | replaces a stale document | new → old |
| `relates_to` | adjacent topic (default for inline links) | doc ↔ doc |
| `implemented_by` | where it lives in code | doc → source file |
| `part_of` | belongs to a larger index | doc → index |

The doc→code link (`documents`/`implemented_by`) is what makes this an ontology **over
code**: a document is explicitly tied to the files/component it describes.

## Kinetic layer — Actions (curation rules)

In Palantir, **Actions** sit on top of the semantics — what you can do with objects.
Here, Actions = the **curation procedures** a human/agent runs (see `kb-maintain` skill):
CREATE → classify, place, frontmatter, link, index. UPDATE → bump `updated`/`status`.
DEPRECATE → `status` + `supersedes`. LINK → no orphans. REINDEX → `gitmark index`.

## Invariants (checked by `gitmark lint`)

- **I1.** Every load-bearing doc has frontmatter with a valid `node_type`.
- **I2.** `node_type`/`status` values are within their vocabularies (`service` is free-form).
- **I3.** No orphans: a load-bearing doc has ≥1 incoming or outgoing link.
- **I4.** No broken links (a markdown link to a missing file).
- **I5.** Every `docs/gitmark/**` folder has a `README.md` index.
- **I6.** A `supersedes` target has `status: deprecated|archived`.

## Why this, not a wiki/Notion

- **md+git** is already the source of truth. The ontology adds *structure on top* without
  changing the medium — frontmatter and links are plain markdown, readable in any viewer.
- The object/link graph gives Foundry-like navigation with no platform — typed links
  between docs, readable in any markdown viewer.
- Types + invariants keep the KB from degrading into a pile as it grows — the exact pain
  Palantir's ontology solves for data, applied here to knowledge over code.

Prototype model: [Palantir Ontology overview](https://www.palantir.com/docs/foundry/ontology/overview)
· [Core concepts](https://www.palantir.com/docs/foundry/ontology/core-concepts).
