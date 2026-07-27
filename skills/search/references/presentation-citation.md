# Agent presentation contract — citation graph (refs / cited-by)

Simplified rendering standard for `sf-search refs <id>` and
`sf-search cited-by <id>` results in conversation. A deliberate
simplification of [presentation.md](presentation.md) — citation graphs
have no relevance ranking, so the 5-slot recommendation and 4-group
sections are semantically meaningless.

**Why:** A citation is a boolean edge, not a relevance score. Trying to
"recommend 5 key refs" from a list of 80 cited papers would be
fabrication — the agent has no signal to distinguish importance. A
simplified contract avoids that trap.

## Section 0 — citation summary header

```
🔗 sf-search {refs|cited-by} '<seed_normalized>'
📄 seed: <title> (<year> · <venue>)
📊 共 N 篇 · R resolved · U unresolved · sources: openalex:X s2:Y crossref:Z pubmed:W (failed: …)
```

- `seed_normalized` is the identifier as the classifier normalized it
  (DOI lowercased, arxiv id stripped of trailing vN, etc.)
- `title` / `year` / `venue` come from the seed's metadata (if available
  — the classifier doesn't fetch seed metadata, so this may be empty for
  identifier-only seeds).
- `resolved` / `unresolved` from the summary line.
- Per-source counts from `sources_ok`; `sources_failed` inline.

## Section 3 — full list

Density A only (1 line / paper). No density B or C — no recommendations
to justify.

```
#N  [year · venue · X cited]  identifier
    <title truncated> — <first author +N>
```

Hard cap:

- **refs**: 100 条 (overflow → `… and N more; use --out FILE for full list`)
- **cited-by**: 20 条 (overflow → `… and N more; use --out FILE for full list`)

The cited-by cap is tighter because the list can be thousands of items
and the user's intent is almost always to see the top N — not to scroll.

## What is NOT rendered

- **No Section 1** (5 recommendation slots) — would be fabricated.
- **No Section 2** (4 groups) — no meaningful grouping dimension in an
  unordered set of cited papers.
- **No tag-based reason** (`[综述]`, `[里程碑]`, etc.) — not applicable.
- **No `raw_citation` strings** in conversation — unresolved records are
  silently omitted from the chat rendering. The fact that they exist is
  captured in Section 0 (`U unresolved`). If the user asks about them,
  point to `--out-unresolved FILE`.
