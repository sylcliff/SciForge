# Citation graph — `refs` / `cited-by`

Given one seed paper, list its outgoing references (what it cites) or
incoming citations (who cites it). Multi-source union with union-find
dedup; output matches the main `sf-search` NDJSON record shape so results
pipe cleanly into `sf-download` and `sf-lit add`.

## Commands

```
sf-search refs      <id>  [--top N]  [--sources ...]  [--no-fetch-meta]
                          [--format ndjson|ids|table|bib|ris]  [--out FILE]
                          [--out-unresolved FILE]

sf-search cited-by  <id>  [--top N | --all]
                          [--sort citations:desc|year:desc]
                          [--sources ...]  [--no-fetch-meta]
                          [--format ...] [--out FILE]  [--out-unresolved FILE]
```

`<id>` accepts one of:

| Shape | Example |
|---|---|
| DOI | `10.1038/s41586-020-2649-2` |
| PMID | `32939066` |
| arXiv id (modern or old-style) | `1706.03762`, `hep-th/9901001` |
| OpenAlex work id | `W3082521543` |
| Semantic Scholar SHA1 | `204e3073870fae3d05bcbc2f6a8e263d9b72e776` |
| Local library URI | `sciforge://literature/vaswani2017attention` |

The DataCite arXiv self-DOI shape (`10.48550/arxiv.*`) is auto-downgraded
to its embedded arxiv id — it's treated as an arxiv seed, not a DOI.

The `sciforge://` URI shape shells out to `sf-lit show <citekey> --json`
to read the paper's `doi` / `arxiv_id` / `s2_paper_id` fields. If
`sf-lit` isn't on PATH, or the citekey is unknown, exit code is 3 with
an explicit error. External-id seeds don't need `sf-lit` installed.

## Source coverage matrix

| Source | refs | cited-by | Notes |
|---|---|---|---|
| OpenAlex | ✓ | ✓ | `referenced_works[]` for refs; `works?filter=cites:W…` for cited-by |
| Semantic Scholar | ✓ | ✓ | `/paper/{id}/references` and `/paper/{id}/citations` |
| Crossref | ✓ | n/a | `reference[]` on `works/{doi}`; **Event Data API for cited-by is not covered** |
| PubMed | ✓ (PMID + PMC) | ✓ (PMID + PMC) | `elink linkname=pubmed_pubmed_refs / pubmed_pubmed_citedin` — only fires when the seed maps to a PMID **and** the paper is in PubMed Central |
| arXiv | — | — | Atom API returns no citation graph |

`sf-search doctor` prints this same matrix.

Multi-source union is done as a **set** (union-find dedup on the 5+1
identifier space: `doi / pmid / pmcid / arxiv_id / openalex_id / s2_id`).
No relevance ranking / RRF applies — a citation is a boolean edge, not a
relevance score. `sources_hit` on each output record records exactly
which sources reported that edge.

## What is dedup'd, and what isn't

- **β-dedup**: same as `sf-search` main mode. Any shared non-empty
  identifier merges records.
- **Path A preprint↔journal collapse**: also inherited. Records whose
  `referenced_works` / `relation.has-preprint` carry cross-refs get
  β-merged without extra HTTP.
- **NOT** done: Path B / Path C arxiv upgrade (would add tens of HTTPs
  per invocation), post-hoc Crossref verification, cross-source
  reconciliation of DOI casing beyond `str.lower()`.
- DataCite arxiv self-DOIs (`10.48550/arxiv.*`) inside a Crossref
  `reference[]` entry are treated as arxiv ids, not journal DOIs, so
  the preprint and journal versions β-merge.

## Defaults & limits

| Setting | `refs` | `cited-by` |
|---|---|---|
| Default cap | **all** references (bounded — a paper cites what it cites) | `--top 100` |
| `--top N` | Truncate to first N (source-natural order) | Truncate after sort |
| `--all` | (not offered — refs are bounded) | Opt-in unbounded fetch |
| `--sort` | (n/a — order preserved from sources) | `citations:desc` (default) or `year:desc` |

`--top` and `--all` are mutually exclusive on `cited-by`. Passing both
exits 2.

## Metadata fill

Some sources return only identifiers for citation-graph entries:
OpenAlex `referenced_works[]` is a list of W-ids; PubMed elink returns a
PMID list. To make each output record useful (title / authors /
year / venue), the orchestrator **batch-fetches missing metadata**:

- OpenAlex — `works?filter=openalex_id:W1|W2|…&per-page=50` (chunk 50)
- PubMed — efetch XML (chunk 200)

This costs 1–2 additional HTTPs per invocation typically. Pass
`--no-fetch-meta` to skip; output records will then be identifier-only
for those sources.

## Output — resolved records

Main NDJSON stream. Every line has an `identifier` — records without any
identifier are diverted to `--out-unresolved`.

```jsonc
{
  "index": 0,
  "direction": "refs",                          // or "cited-by"
  "seed": "10.1038/s41586-020-2649-2",          // as normalized by the classifier
  "identifier": "10.1109/cvpr.2019.00297",      // best available: DOI > PMID > arxiv_id > openalex_id > s2_id
  "sources_hit": ["openalex", "s2"],            // which sources reported this edge
  "meta": {
    "title":      "…",
    "authors":    ["…", "…"],
    "year":       2019,
    "doi":        "10.1109/cvpr.2019.00297",
    "pmid":       null,
    "pmcid":      null,
    "arxiv_id":   null,
    "openalex_id":"W…",
    "s2_id":      null,
    "url":        "https://doi.org/…",
    "journal":    "CVPR",
    "abstract":   "…",
    "citation_count": 4211,
    "is_oa":      true
  }
}
```

Trailing summary line:

```jsonc
{
  "summary": {
    "seed": "10.1038/…", "seed_kind": "doi", "direction": "refs",
    "resolved": 34, "unresolved": 2,
    "sources_ok":     {"openalex": 34, "s2": 30, "crossref": 32, "pubmed": 0},
    "sources_failed": {}
  }
}
```

## Output — unresolved stream

Present **only** if `--out-unresolved PATH` is passed. NDJSON, same
overall shape as main records but with `identifier: null` and a
`raw_citation` string. These are typically Crossref `reference[]`
entries that carry only a `unstructured` free-text bibliographic string
with no DOI:

```jsonc
{
  "index": 42, "direction": "refs", "seed": "10.1038/…",
  "identifier": null,
  "sources_hit": ["crossref"],
  "meta": {"title": null, "authors": [], "year": null, "…": "…"},
  "raw_citation": "J. Smith, Chem. Rev., 2019, 119, 5–20"
}
```

Unresolved records **never** appear in the main NDJSON stream (or in the
`bib` / `ris` / `ids` formats — those all require an identifier).
Split-stream is the only way to see them without breaking the "every
main record pipes cleanly into `sf-download` / `sf-lit add`" contract.

## Alternate output formats

`--format` picks the wire format (default `ndjson`):

| Format | Content |
|---|---|
| `ndjson` | One JSON record per line + summary line |
| `ids`    | One `identifier` per line — pipes to `sf-download --from-file -` |
| `table`  | Aligned columns for human eyeballing |
| `bib`    | BibTeX entries; unresolved records skipped |
| `ris`    | RIS entries; unresolved records skipped |

## Exit codes

| Code | Cause |
|---|---|
| `0` | Success — including a legitimately empty result set (paper has no refs / no citations) |
| `2` | Invalid input: unrecognized seed format, `--top` combined with `--all`, malformed `--sources` |
| `3` | Seed not resolvable: unknown sciforge citekey, `sf-lit` not on PATH for a URI seed, or the DOI/PMID/arxiv id / …  is not found in any of the pooled sources |
| `5` | All citation sources HTTP-failed |

Empty results are exit 0. `refs / cited-by` are set-valued queries; an
editorial with zero refs, or a new preprint with zero incoming
citations, is factual, not an error.

## Examples

```bash
# All refs of the transformer paper, resolved to NDJSON
sf-search refs 10.1038/s41586-020-2649-2

# Top 5 refs, human table
sf-search refs 1706.03762 --top 5 --format table

# Refs as BibTeX, ready for a LaTeX include
sf-search refs 10.1038/nature12373 --format bib > refs.bib

# Fan out into sf-download for PDFs
sf-search refs 10.1038/nature12373 --format ids | sf-download --from-file -

# Fan out into sf-lit add for metadata-only cataloguing
sf-search refs 10.1038/nature12373 | sf-lit add --meta-json -

# Top-100 incoming citations, sorted by citation count
sf-search cited-by 10.1038/s41586-020-2649-2

# All citers (may be tens of thousands — piped to disk)
sf-search cited-by 10.1038/s41586-020-2649-2 --all --out citers.ndjson

# Newest citers first
sf-search cited-by W3082521543 --sort year:desc --top 20

# Seed from local library
sf-search refs sciforge://literature/vaswani2017attention --format ids | sf-download --from-file -

# Refs with unresolved bibliographic strings written aside
sf-search refs 10.1038/nature12373 \
    --out refs.ndjson \
    --out-unresolved refs.unresolved.ndjson
```

## Presentation contract (agent-facing)

When an LLM agent surfaces `refs` / `cited-by` results in conversation,
it MUST follow the simplified rendering rules in the memory file
`sf-search-citation-presentation`. The core rules:

- No 5-recommendation section (citation graphs have no relevance rank —
  recommending "the 5 most relevant refs" would be fabrication).
- No 4-group section (same reason).
- Section 0 is a citation-specific summary line: seed title/year, resolved
  vs unresolved counts, per-source coverage.
- Section 3 is a density-A full list; cap at 20 for `cited-by`, 100 for
  `refs`; overflow points at `--out FILE`.

See the memory file for exact templates.

## What this does NOT do

- **Post-hoc DOI verification** — trust each source's DOI. Use a future
  `sf-cite-verify` skill for audit-grade verification.
- **Caching** — every invocation hits the network. Stateless, same as main
  `sf-search`.
- **Batch mode (`--from-file`)** — one seed per call. Shell loops for
  multi-seed workflows.
- **Custom graph formats (`edges`, `graphml`)** — output is per-paper
  records, same shape as main `sf-search`. Use `jq` to derive edge lists.
- **arXiv upgrade Path B/C** — refs/cited-by uses Path A only (the free
  preprint↔journal collapse from cross-refs already in the responses).
