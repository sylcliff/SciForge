# Output schema

This is the wire format `sf-download` produces. Anything not documented
here is unstable and may change without notice.

## NDJSON stream

Every invocation writes one JSON object per line to stdout, followed
by a single `{"summary": {...}}` line for batch invocations. Each line
is a self-contained JSON object (Newline-Delimited JSON /
`application/x-ndjson`). Lines flush as soon as the paper completes,
which means **line order ≠ input order** under concurrent batch
mode — every per-paper line carries `index` for reconstruction.

Single-paper mode still emits a valid one-line NDJSON stream: one
per-paper line, **no summary line**.

## Per-paper line

```jsonc
{
  // Position in the input (0-based). Same identifier appearing twice
  // in the input gets two separate lines with different indices.
  "index": 0,

  // The identifier exactly as the caller supplied it. Not normalized.
  "identifier": "https://arxiv.org/abs/1706.03762",

  // One of the 9 status values (see references/status-codes.md).
  "status": "downloaded",

  // Absolute path on the local filesystem, or null if no PDF was
  // saved. Present when status is "downloaded"; null otherwise.
  "pdf_path": "/home/user/.sciforge/inbox/1706.03762.pdf",

  // Which of the 5 sources produced the PDF. One of "arxiv" /
  // "unpaywall" / "semanticscholar" / "crossref", or null.
  "source_used": "arxiv",

  // Every source we consulted, in order. Useful for debugging why a
  // paper was reported metadata_only ("did we even try Unpaywall?").
  "sources_queried": ["arxiv"],

  // File size in bytes, or null when no PDF was saved.
  "bytes": 2158340,

  // Union of metadata across sources, matching the sf-lit ingest
  // interface v2. See "Meta object" below. null on identifier_not_found
  // and title_ambiguous.
  "meta": {
    "title": "Attention Is All You Need",
    "authors": ["Ashish Vaswani", "Noam Shazeer", "..."],
    "year": 2017,
    "arxiv_id": "1706.03762",
    "doi": "10.48550/arxiv.1706.03762",
    "url": "https://arxiv.org/abs/1706.03762",
    "abstract": "..."
  }
}
```

### Failure lines

```jsonc
// Nothing found anywhere
{"index": 2, "identifier": "10.9999/notfound",
 "status": "identifier_not_found",
 "pdf_path": null, "meta": null, "source_used": null,
 "sources_queried": ["crossref","openalex","unpaywall","semanticscholar"],
 "bytes": null}

// Closed access confirmed by Unpaywall
{"index": 1, "identifier": "10.1234/paywalled",
 "status": "paywalled",
 "pdf_path": null, "meta": {...}, "source_used": null,
 "sources_queried": ["crossref","unpaywall","semanticscholar","openalex"],
 "bytes": null}

// URLs given by sources but bytes didn't verify as PDF
{"index": 3, "identifier": "10.5678/link-rot",
 "status": "pdf_link_broken",
 "pdf_path": null, "meta": {...}, "source_used": null,
 "sources_queried": ["crossref","unpaywall"],
 "bytes": null,
 "pdf_attempts": [
   {"source": "unpaywall", "url": "https://...", "reason": "http_403"},
   {"source": "crossref", "url": "https://...", "reason": "not_pdf_magic"}
 ]}

// Title fallback couldn't confidently pick one
{"index": 0, "identifier": "attention mechanism",
 "status": "title_ambiguous",
 "pdf_path": null, "meta": null, "source_used": null,
 "sources_queried": ["openalex"],
 "bytes": null,
 "candidates": [
   {"title": "Attention Is All You Need",
    "doi": "10.48550/arxiv.1706.03762",
    "year": 2017,
    "first_author": "Ashish Vaswani"},
   {"title": "Attention", "doi": "...", "year": 2020, "first_author": "..."}
 ]}
```

`pdf_attempts` (only present when `status = pdf_link_broken`) lists at
most 5 attempts in the order they were tried. Each entry is `{source,
url, reason}` where `reason` is one of `http_<code>`, `not_pdf_magic`,
`not_pdf_content_type`, `zero_bytes`, `timeout`, `verify_error`.

## Summary line (batch mode only)

```json
{
  "summary": {
    "total": 10,
    "downloaded": 7,
    "metadata_only": 0,
    "paywalled": 1,
    "identifier_not_found": 1,
    "pdf_link_broken": 0,
    "title_ambiguous": 0,
    "rate_limited": 1,
    "network_error": 0,
    "invalid_input": 0,
    "elapsed_seconds": 8.4,
    "warnings": ["s2_no_key_seen_429"]
  }
}
```

Every status has its own counter, always present, defaulting to 0.
`warnings` is a possibly-empty array of stable string tokens; the only
one currently defined is `s2_no_key_seen_429` (emitted when the run
saw at least one Semantic Scholar 429 and no API key was configured).

Any per-paper line contains `"index"`, never `"summary"`. The final
line contains `"summary"`, never `"index"`. Parsers can distinguish
lines by which key is present.

## Meta object

The `meta` object is a **strict subset** of the schema documented in
`skills/literature/references/ingest-interface.md` v2 — nothing here
that isn't there. Piping `meta` alone into `sf-lit add --meta-json -`
is guaranteed to work.

```jsonc
{
  // required (never emitted with null; if we can't determine title,
  // the whole meta object is null and status reflects the failure)
  "title": "...",

  // all optional — omitted (not null) when unknown
  "authors": ["First Last", ...],       // "First Last" form; ordered
  "abstract": "...",                    // >= 40 chars or dropped
  "year": 2017,
  "venue": "NeurIPS",                   // short name
  "venue_full": "Advances in Neural Information Processing Systems",

  "doi": "10.xxxx/...",                 // lowercased, canonical form
  "arxiv_id": "1706.03762",             // no version suffix
  "s2_paper_id": "…40 hex…",
  "url": "https://..."                  // best canonical URL
}
```

Fields **never** emitted by `sf-download`:

- `notes`, `tags`, `collections` — user-facing; caller adds them
- `github`, `news`, `si` — associations; caller adds them post-ingest
- `citekey` — assigned by `sf-lit add`, not by us

## Identifier normalization

Before use in filenames, HTTP requests, or `sources_queried`,
identifiers are normalized:

| Input | Normalized to |
|---|---|
| `arXiv:1706.03762` | `1706.03762` (arxiv-id) |
| `arxiv:1706.03762v2` | `1706.03762` (strip version) |
| `1706.03762` | `1706.03762` (arxiv-id) |
| `https://arxiv.org/abs/1706.03762` | `1706.03762` (arxiv-id) |
| `https://arxiv.org/pdf/1706.03762v3.pdf` | `1706.03762` (arxiv-id) |
| `10.1038/S41586-020-2649-2` | `10.1038/s41586-020-2649-2` (lowercased doi) |
| `https://doi.org/10.1038/...` | `10.1038/...` (doi) |
| `https://dx.doi.org/10.1038/...` | `10.1038/...` (doi) |
| `W2741809807` | `W2741809807` (openalex, uppercased leading W) |
| 40-hex string | `<hash>` (s2 paper ID, lowercased) |

Anything else → status `invalid_input`.

The `identifier` field in output preserves the original string exactly
as supplied; normalization only affects lookup and filename.

## Filename convention

Downloaded PDFs are named `<safe-identifier>.pdf`. The rule:

1. Start from the **normalized** identifier (see above).
2. Replace path-illegal or query-troublesome characters:
   - `/` → `_`
   - `:` → `-`
   - `\`, `*`, `?`, `"`, `<`, `>`, `|` → `_`
   - Any other character outside `A-Z a-z 0-9 . _ -` → `_`
3. Truncate to at most **200 characters** (leaves room for path
   prefixes on Windows).
4. Append `.pdf`.

Examples:

| Normalized ID | Filename |
|---|---|
| `1706.03762` | `1706.03762.pdf` |
| `10.1038/s41586-020-2649-2` | `10.1038_s41586-020-2649-2.pdf` |
| `10.48550/arxiv.1706.03762` | `10.48550_arxiv.1706.03762.pdf` |
| `W2741809807` | `W2741809807.pdf` |

If an identical filename already exists in the target directory and is
non-zero and starts with `%PDF`, we **do not** re-download — the
existing file is reused, `bytes` reports its actual size, and
`source_used` is set to `cache`. To force re-download, delete the file
or use `--out` with a fresh directory.

## Title fallback

When `--title "..."` is used, or an entry in a batch file cannot be
parsed as an identifier, OpenAlex title search is invoked. The strict
match rule:

1. Normalize both input title and each candidate's title:
   - Lowercase
   - Strip Unicode punctuation and whitespace runs to a single space
   - Trim leading/trailing whitespace
2. Compare normalized input to the **top-1 candidate's** normalized title.
3. If they are exactly equal, treat as a confirmed hit — extract the
   candidate's DOI (or, when the top hit is an arXiv preprint,
   `arxiv_id`) and re-enter the fallback chain as if the caller had
   given us that DOI directly.
4. Otherwise, emit status `title_ambiguous` with up to 3 candidates.

A confirmed hit's `identifier` in the output line is the original
title string (as supplied), not the resolved DOI. The resolved DOI
ends up in `meta.doi`. This preserves round-tripping — the user asked
for that title, and gets an output line keyed to that title.

## Pydantic models

The models live in `scripts/output.py` and are the single source of
truth in code. Every field in this document has a corresponding field
on a pydantic model with the same name and type; adding new fields
should be a docs-first change followed by a model update.
