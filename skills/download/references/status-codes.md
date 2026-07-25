# Status codes

Every per-paper NDJSON line has exactly one `status` value from this
list of nine. Each has a precise trigger; overlaps are avoided by
construction. Callers can branch on `status` without inspecting any
other field.

The design principle (borrowed from `nature-downloader`): **three
different failure modes should not collapse to one status**, because
each implies a completely different next action.

## `downloaded`

**Trigger**: A source produced a PDF URL, we fetched bytes, the first
four bytes are `%PDF`, size is non-zero, and (when a `Content-Type`
header was returned) it is `application/pdf`.

**Fields**: `pdf_path`, `bytes`, `source_used`, `sources_queried`,
`meta` all populated.

**Next action** (for `sf-lit`): pipe `meta` and `pdf_path` into
`sf-lit add --meta-json - --pdf-path P` (optionally with `--move-pdf`
so the inbox copy doesn't linger).

## `metadata_only`

**Trigger**: At least one metadata source returned a valid record,
but **no** source produced any PDF URL. This is the "author has an
Elsevier paper, no preprint, no OA" case.

**Fields**: `pdf_path = null`, `bytes = null`, `source_used = null`,
`sources_queried` lists everything tried, `meta` populated.

**Not the same as `paywalled`.** `metadata_only` says "nobody I asked
had a PDF URL." `paywalled` says "Unpaywall specifically told me the
article is closed access."

**Next action**: caller can still ingest the metadata into `sf-lit`
with `--manual` (no PDF); user can chase the PDF elsewhere.

## `paywalled`

**Trigger**: Unpaywall was queried, responded with a record, and
`is_oa == false`. This overrides `metadata_only` — if Unpaywall is
authoritative about closedness, use that stronger signal.

If Unpaywall was **not** queried (no email configured, or Unpaywall
skipped), we do **not** emit `paywalled` even if the paper is in fact
closed — status is `metadata_only` instead. `paywalled` requires a
positive `is_oa=false` signal.

**Fields**: `pdf_path = null`, `meta` populated (probably by other
sources; Unpaywall itself has minimal metadata).

**Next action**: try an institutional access route (future
`sf-download-via-browser` or `nature-downloader`), or ILL.

## `identifier_not_found`

**Trigger**: **Every** source consulted returned HTTP 404 (or
equivalent "no such paper" signal — arXiv's `totalResults=0`, OpenAlex
search with empty `results`, etc.).

**Fields**: `pdf_path = null`, `meta = null`, `sources_queried` lists
everything tried. No candidates, no partial data.

**Not**: `identifier_not_found` is reserved for "the ID doesn't exist
anywhere." A DOI that Crossref knows but Unpaywall doesn't is
**not** this — that would be `metadata_only` or `downloaded`.

**Next action**: caller should double-check the identifier.

## `pdf_link_broken`

**Trigger**: One or more sources produced a candidate PDF URL, we
tried it, and **every attempt** failed one of:

- HTTP 4xx/5xx from the PDF host
- Bytes do not start with `%PDF`
- `Content-Type` is present and is not `application/pdf`
- Downloaded file is 0 bytes

Metadata may be complete; only the PDF fetch failed.

**Fields**: `pdf_path = null`, `bytes = null`, `meta` populated,
`sources_queried` reflects which URLs were attempted.

**Not**: `pdf_link_broken` is stricter than `network_error` — this
signals a **content problem** with URLs the sources swore had a PDF.
Transient network problems on the metadata query itself become
`network_error`.

**Next action**: check the publisher landing page manually, or wait
and retry (publisher CDN may be updating).

## `title_ambiguous`

**Trigger**: Invocation used `--title` (or a batch item whose
identifier could not be parsed to a DOI/arxiv-id/etc.), OpenAlex title
search returned results, but the top-1 result's title did not match
the input after normalization (lowercase, strip punctuation and
whitespace).

**Fields**: `pdf_path = null`, `meta = null`. A `candidates` array is
included with up to 3 top-ranked alternatives:

```json
{"index": 0, "identifier": "attention",
 "status": "title_ambiguous",
 "candidates": [
   {"title": "Attention Is All You Need", "doi": "10.48550/arxiv.1706.03762",
    "year": 2017, "first_author": "Ashish Vaswani"},
   {"title": "Attention", "doi": null, "year": 2020, "first_author": "..."}
 ]}
```

**Not**: this is not `identifier_not_found` — there *were* hits, just
none confident enough. And it is not `invalid_input` — the query was
syntactically fine.

**Next action**: caller picks the intended DOI from `candidates` and
retries with that.

## `rate_limited`

**Trigger**: A source returned HTTP 429 and one of:

- No `Retry-After` header
- `Retry-After` > 30 seconds (we do not block that long)
- We already waited once and got 429 again

The paper we were fetching gets this status; batch processing
continues on other papers (they use different sources or arrive later
after the transient burst clears). If ≥1 `rate_limited` events touched
Semantic Scholar during a batch, the summary line includes
`"warnings": ["s2_no_key_seen_429"]`.

**Fields**: whatever partial data we had before the 429; usually
`meta` filled by earlier successful sources, `pdf_path = null`.

**Next action**: retry the paper later; set a Semantic Scholar API
key if this is repeated.

## `network_error`

**Trigger**: After 3 retries with exponential backoff (1s → 2s → 4s),
a source still failed with:

- DNS resolution failure
- TCP connection refused / reset
- Read timeout > `http_timeout_seconds` (default 30)
- HTTP 5xx (any of 500-599)
- TLS handshake failure

If this happened on the *metadata* query for a source, that source is
dropped from the fallback chain for this paper and the next source is
tried. Only when *every* source in the chain fails this way does the
paper's final status become `network_error`.

**Fields**: whatever partial data we had (likely `meta = null` if the
first source failed at once).

**Next action**: check internet / DNS; retry.

## `invalid_input`

**Trigger** (CLI-level, not per-source):

- Identifier does not match any known pattern (DOI / arxiv-id /
  OpenAlex `W…` / 40-hex S2 / URL)
- Conflicting flags (e.g. `--title` and `--ids` together)
- `--from-file` at a path that doesn't exist or contains only blanks
- `--out DIR` at a path that is a file, not a directory (or not writable)
- Malformed input on stdin (only relevant to future streaming modes)

**Fields**: `meta = null`, `pdf_path = null`, no sources contacted.

**Exit code**: `2` (both single and batch mode — this is startup-time
validation, not per-paper).

**Next action**: caller fixes the invocation.

---

## Status vs. exit code

Batch mode (`--ids` / `--from-file`) **always exits 0**, unless a
startup-level `invalid_input` or an uncaught exception. Per-paper
failures are visible only in NDJSON.

Single-paper mode maps status → exit code as follows:

| Status | Exit code |
|---|---|
| `downloaded` | 0 |
| `metadata_only` | 0 |
| `paywalled` | 0 |
| `pdf_link_broken` | 0 |
| `title_ambiguous` | 0 |
| `rate_limited` | 0 |
| `network_error` | 0 |
| `identifier_not_found` | **3** |
| `invalid_input` | **2** |

Rationale: the shell distinction is "did we produce a useful result at
all?" A `paywalled` paper still produced authoritative metadata (exit
0 → caller can decide what to do). `identifier_not_found` produced
nothing (exit 3, matching ADR-0006 "resource not found"). `invalid_input`
never even started (exit 2). This aligns with `sf-lit`'s exit code
table exactly.
