# Ranking, dedup & arXiv upgrade

The three cross-source post-processing stages every multi-source search
runs, in order: **dedup** (merge records that are the same paper) →
**arXiv upgrade** (resolve preprint-only hits to their journal DOI, then
re-dedup) → **rank** (order the survivors). This file is the single
source of truth for all three; `SKILL.md` only points here.

The per-field metadata merge that runs alongside dedup lives in
[output-schema.md](output-schema.md) ("Meta object field rules") — not
repeated here.

## Ranking — RRF

Default: **Reciprocal Rank Fusion**, `k=60`. Each source contributes
`1 / (60 + rank_in_that_source)`; a paper's score is the sum across the
sources that returned it. Stronger cross-source consensus → higher
score. `k=60` is the standard TREC value (see
[config.md](config.md) "Non-configuration").

`--sort` overrides RRF:

| Value | Order key |
|---|---|
| `relevance` (default) | RRF score, descending |
| `year:desc` | publication year, descending |
| `citations:desc` | `citation_count`, descending |

The chosen key is written to each record's `score` field
(see [output-schema.md](output-schema.md)).

## Dedup — union-find (β mode)

Union-find over `(doi, pmid, pmcid, arxiv_id, openalex_id, s2_id)`. Any
two records that share one non-empty identifier merge into one group.

**Title-based fuzzy matching is deliberately not done** — that is the
zero-false-merge promise. Two records only merge on a hard identifier
match, never on title similarity.

## arXiv preprint ↔ journal upgrade

A paper often exists as both an arXiv preprint and a journal article
with separate identifiers. Upgrade resolves the preprint hit to its
journal DOI so the two records dedup into one. Three tiers; the first
two are on by default.

**Path A — zero HTTP, always on.** Adapters mine cross-refs already
present in the search responses:

- OpenAlex `locations[*].landing_page_url` / `open_access.oa_url`
- Crossref `relation.has-preprint` / `is-preprint-of`
- arXiv `<arxiv:doi>` / `<arxiv:journal_ref>` / `<arxiv:comment>`
  (scanned for embedded DOIs)

Any preprint↔journal pair found in these fields β-dedups for free.

**Path B — post-dedup lookup, on by default** (disable with
`--no-arxiv-upgrade`). For groups still arxiv-only after β dedup,
concurrent OpenAlex + Semantic Scholar lookup by arxiv id. OpenAlex uses
a **two-hop query** (preprint DOI → title → `type:article`) because it
represents preprint and journal as two separate work IDs. DataCite arxiv
self-DOIs (`10.48550/arxiv.*`) are rejected as "journal DOIs". First
real journal DOI wins; dedup re-runs so the records merge.

**Path C — optional**, `--arxiv-upgrade-fallback title-search`. When
Path B fails, query Crossref by the preprint title; accept only when
title Jaccard ≥ 0.85 AND first-author surname matches.

**Post-hoc verification.** After any upgrade returns a DOI, that DOI is
cross-checked against Crossref (`/works/{doi}`) for year (±3 tolerance)
+ first-author surname agreement. A mismatch drops the upgrade; a
network flake keeps it (fail-open). This catches plausible-but-wrong
DOIs before injection.

Upgrade sources do **not** enter `sources_hit` (so RRF is unaffected) —
they only inject a DOI. Final records carry `arxiv_upgraded: true` and
`arxiv_upgrade_via: "id-lookup" | "title-search"` for audit.
