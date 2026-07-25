# Output schema

Wire format `sf-search` produces. Anything not documented here is
unstable and may change without notice.

## NDJSON stream

Every invocation writes one JSON object per line to stdout. Batch
invocations (`--from-file`) end with a single `{"summary": {...}}`
line. Single-query mode emits per-paper lines only, **no summary**.

Lines flush as soon as the paper is deduped and ranked. Line order
matches final rank (best first). The `index` field is 0-based rank.

## Per-paper line

```jsonc
{
  // 0-based rank in the final list (after dedup + sort).
  "index": 0,

  // Best available identifier, in preference order:
  // DOI > PMID > arXiv ID > S2 hash. Downstream consumers should
  // use this as the primary key.
  "identifier": "10.1038/s41586-020-2649-2",

  // Every source that returned this paper (may be 1..5).
  "sources_hit": ["crossref", "openalex", "pubmed"],

  // Ranking score. Interpretation depends on --sort:
  //   relevance (default) → RRF score, higher = more relevant
  //   year:desc           → year (integer, higher = newer)
  //   citations:desc      → citation_count (integer)
  "score": 0.048,

  // Each source's own rank for this paper (1-based).
  "rank_by_source": {"crossref": 1, "openalex": 2, "pubmed": 3},

  // All identifiers that were unioned into this record. Useful for
  // debugging dedup decisions. First element == identifier.
  "dedup_group": ["10.1038/s41586-020-2649-2", "32939066"],

  // True if this record's DOI was obtained via the arxiv-upgrade phase
  // (an arxiv-only search hit resolved to its published-version DOI via
  // OpenAlex or Semantic Scholar lookup, then re-merged with the journal
  // record in the search results). False / absent means the record
  // reached its final form through primary search hits only.
  "arxiv_upgraded": false,

  // Merged metadata. Schema below matches sf-download's meta object
  // exactly, so `sf-search ... | sf-lit add --meta-json -` works.
  "meta": {
    "title": "Array programming with NumPy",
    "authors": ["Charles R. Harris", "K. Jarrod Millman", "..."],
    "year": 2020,
    "doi": "10.1038/s41586-020-2649-2",
    "pmid": "32939066",
    "arxiv_id": null,
    "openalex_id": "W3082521543",
    "s2_id": null,
    "url": "https://www.nature.com/articles/s41586-020-2649-2",
    "journal": "Nature",
    "volume": "585",
    "issue": "7825",
    "pages": "357-362",
    "abstract": "Array programming provides a powerful...",
    "citation_count": 4211,
    "type": "journal-article",
    "is_oa": true
  }
}
```

### Meta object field rules

| Field | Type | Missing → | Merge rule |
|---|---|---|---|
| `title` | str | `null` | Crossref → OpenAlex → PubMed → S2 → arXiv |
| `authors` | list[str] | `[]` | Crossref → PubMed → OpenAlex → S2 → arXiv |
| `year` | int | `null` | Earliest non-null |
| `doi` / `pmid` / `arxiv_id` / `openalex_id` / `s2_id` | str | `null` | First non-null |
| `url` | str | `null` | Prefer publisher URL (Crossref) > OpenAlex `landing_page_url` |
| `journal` | str | `null` | Crossref → OpenAlex → PubMed |
| `volume` / `issue` / `pages` | str | `null` | Crossref → OpenAlex → PubMed |
| `abstract` | str | `null` | Crossref → OpenAlex → PubMed → S2 → arXiv |
| `citation_count` | int | `null` | `max(S2, OpenAlex)` |
| `type` | str | `null` | Crossref `type` (e.g. `journal-article`, `preprint`) |
| `is_oa` | bool | `null` | Only OpenAlex records carry this authoritatively. `true` = open access confirmed; `false` = closed; `null` = unknown / no OpenAlex hit in group |

## Summary line (batch mode only)

```jsonc
{"summary": {
  "queries_ran": 3,
  "queries_failed": 0,
  "papers_returned": 87,
  "sources_queried": ["pubmed", "crossref", "arxiv", "openalex", "s2"],
  "sources_failed": [],
  "duration_sec": 4.2,
  "dedup_shrink_pct": 22.3    // (before - after) / before
}}
```

## Failure lines

**Per-query failure** (in `--from-file` mode, one line per failed query):

```jsonc
{"query_index": 5, "query": "malformed[[[query",
 "status": "error", "error": "PubMed refused query: bad syntax"}
```

**Whole-run failure** (all sources down): no per-paper lines, exit code 5.

## Alternate formats

`--format ids` outputs one identifier per line — no JSON:
```
10.1038/s41586-020-2649-2
32939066
arXiv:1706.03762
```

`--format table` outputs a fixed-width aligned table to stdout:
```
#  YEAR  CITED   VENUE               TITLE                                       DOI
1  2020  4211    Nature              Array programming with NumPy                10.1038/...
```

`--format bib` emits BibTeX entries (citekey = surname+year+first-title-word):
```
@article{harris2020array,
  title = {Array programming with NumPy},
  ...
}
```

`--format ris` emits RIS entries per RFC — `TY - JOUR` for
`journal-article`, `TY - GEN` for preprints, etc.

## Downstream contracts

- `sf-download --from-file -` accepts `--format ids` or `--format ndjson`
  (parses `identifier` from each line if JSON).
- `sf-lit add --meta-json -` reads NDJSON, extracts each `meta` object.
- Zotero / EndNote import `--format bib` / `--format ris` directly.
