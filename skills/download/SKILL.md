---
name: download
description: Use when the user wants to fetch a paper's metadata and (when legally available) its OA PDF from public APIs — arXiv, Crossref, Unpaywall, OpenAlex, Semantic Scholar. Takes a DOI / arXiv ID / OpenAlex ID / S2 ID / paper URL, or a batch list, or an exact title. Outputs NDJSON that pipes directly into `sf-lit add`.
---

# download — API-first paper fetcher

Micro / companion skill. Given one or more paper identifiers, queries a
fixed set of public APIs, downloads a legally-available PDF if one
exists, and prints an NDJSON stream where each line describes one paper
(status + metadata + local PDF path). Designed to feed
`sf-lit add --meta-json -`.

## Scope

- **In scope**: DOI / arXiv-id / OpenAlex ID / S2 hash / paper URL →
  metadata + OA PDF (when available), via public APIs only.
- **Out of scope**: institutional / paywalled access (no Chrome / CDP /
  CARSI / library proxy), keyword or topic search, local library
  management, PDF→Markdown conversion, BibTeX export, SI scraping.
  See [Non-goals](#non-goals) below.

## Data sources

| Source | Auth | Role |
|---|---|---|
| arXiv API | none | PDF + metadata for arXiv IDs |
| Crossref | mailto (polite pool) | DOI metadata + potential OA links |
| Unpaywall | **email required** | best OA PDF URL per DOI |
| OpenAlex | mailto (polite pool) | metadata fallback + title search |
| Semantic Scholar | API key recommended | metadata + `openAccessPdf` |

**PDF fallback (first hit wins)**:
`arXiv → Unpaywall → Semantic Scholar → Crossref links`.

**Metadata union (higher priority wins)**:
`Crossref > Semantic Scholar > OpenAlex > arXiv`.

Missing credentials degrade gracefully — no `polite_email` skips
Unpaywall, no `s2_api_key` runs S2 anonymously. See
[references/config.md](references/config.md).

## When to invoke

- User has a DOI / arXiv ID / OpenAlex / S2 ID / paper URL and wants
  the PDF + metadata
- User has a batch of the above (comma list, or file, one per line)
- User has only an exact title (arXiv preprints without DOI, conference
  papers, …) and wants a best-effort resolve

Do **not** invoke this skill for:

- "Find papers about X" — that's discovery. Use `paperhound` /
  `deep-research` / a future search skill and pipe *their* identifier
  list into `sf-download`.
- Anything behind an institution's paywall — this skill returns
  `paywalled` and stops. Use a browser-based skill (or `nature-downloader`)
  for those.

## First-run check

```bash
scripts/sf-download doctor
```

`doctor` reports the polite email, S2 key presence, download directory,
and per-source reachability. It never fails hard; missing config is
reported as a warning.

## Routing

Every invocation is one of these:

| User asks for | Command |
|---|---|
| Fetch one paper by identifier | `scripts/sf-download <id-or-url> [--emit-json]` |
| Fetch a comma-separated list | `scripts/sf-download --ids "id1,id2,..."` |
| Fetch a list from a file | `scripts/sf-download --from-file ids.txt` |
| Fetch by exact title (no DOI) | `scripts/sf-download --title "Attention Is All You Need"` |
| Self-check | `scripts/sf-download doctor` |

`<id-or-url>` is smart-parsed. Any of these work:

```
1706.03762                          # bare arXiv ID
arXiv:1706.03762                    # arXiv-style prefix
10.1038/s41586-020-2649-2           # bare DOI
https://doi.org/10.1038/...         # DOI URL
https://arxiv.org/abs/1706.03762    # arXiv URL
W2741809807                         # OpenAlex work ID
649def34...abc123                   # Semantic Scholar paper ID (40-hex)
```

See [references/output-schema.md](references/output-schema.md) for the
identifier normalization rules and the resulting filename convention.

## Output

Every invocation emits **NDJSON on stdout**: one line per paper, plus
one final `{"summary": ...}` line for batch mode.

Per-paper line:

```json
{
  "index": 0,
  "identifier": "10.1038/s41586-020-2649-2",
  "status": "downloaded",
  "pdf_path": "/home/user/.sciforge/inbox/10.1038_s41586-020-2649-2.pdf",
  "source_used": "unpaywall",
  "sources_queried": ["crossref", "unpaywall"],
  "bytes": 3521117,
  "meta": {
    "title": "Array programming with NumPy",
    "authors": ["Charles R. Harris", "..."],
    "year": 2020,
    "doi": "10.1038/s41586-020-2649-2",
    "url": "https://www.nature.com/articles/s41586-020-2649-2",
    "abstract": "..."
  }
}
```

The `meta` object is a strict subset of
[`skills/literature/references/ingest-interface.md`](../literature/references/ingest-interface.md)
v2 and can be piped directly into `sf-lit add --meta-json -`. See
[references/output-schema.md](references/output-schema.md) for the full
schema (including failure lines and summary format).

PDFs are saved to `~/.sciforge/inbox/` by default, filename
`<safe-identifier>.pdf`. Override with `SCIFORGE_DOWNLOAD_DIR` env or
`--out DIR`. The inbox is not auto-cleaned — pair with
`sf-lit add --move-pdf` when ingesting.

## Status codes

Nine values, defined precisely in
[references/status-codes.md](references/status-codes.md):

| Status | Meaning |
|---|---|
| `downloaded` | PDF saved to disk, `%PDF` header verified |
| `metadata_only` | Metadata resolved but no OA PDF found from any source |
| `paywalled` | Unpaywall explicitly says `is_oa = false` |
| `identifier_not_found` | No source recognises the ID |
| `pdf_link_broken` | A source gave a PDF URL but bytes could not be fetched or verified |
| `title_ambiguous` | `--title` didn't produce a confident top-1 hit; candidates returned |
| `rate_limited` | Source returned 429 and could not be satisfied within the timeout budget |
| `network_error` | Timeout / DNS / connection / 5xx after 3 retries |
| `invalid_input` | Identifier failed to parse or the CLI args conflict |

`paywalled`, `metadata_only`, and `pdf_link_broken` are deliberately
distinct — they imply different next actions for the caller.

## Companion contract

`sf-download` is stateless. It produces:

1. **NDJSON on stdout** — one line per paper, per the schema above
2. **PDFs on disk** — at `pdf_path` in each `downloaded` line

Typical pipe into `sf-lit`:

```bash
scripts/sf-download 1706.03762 --emit-json > /tmp/r.jsonl
jq -c 'select(.status=="downloaded") | .meta' /tmp/r.jsonl \
  | scripts/sf-lit add --meta-json - \
      --pdf-path "$(jq -r 'select(.status=="downloaded") | .pdf_path' /tmp/r.jsonl)" \
      --move-pdf --and-convert
```

See [references/recipes.md](references/recipes.md) for batch,
`--from-file`, and title-fallback patterns.

A follow-up on the `sf-lit` side (letting `add --meta-json -` read a
top-level `pdf_path` field) will simplify this to a single pipe. That
change is independent of `sf-download` shipping.

## Interaction rules

- **API-first, no browser.** No Chrome control, no session reuse, no
  CAS/CARSI handling. If the article is paywalled and no OA copy exists,
  report `paywalled` and stop.
- **Metadata union is deterministic.** Higher-priority source wins for
  overlapping fields; lower-priority fills gaps. Order:
  `Crossref > S2 > OpenAlex > arXiv`.
- **PDF is verified.** Every downloaded file must start with the bytes
  `%PDF`; size must be non-zero. If `Content-Type` is available and is
  not `application/pdf`, downgrade to `pdf_link_broken`. No further
  content check (page count, OCR, text extraction) — that is
  `sf-lit convert`'s job.
- **`--title` is strict.** OpenAlex title search's top-1 result must
  match the input after normalization (lowercase, strip punctuation and
  whitespace). Otherwise the status is `title_ambiguous` and up to 3
  candidates are returned; no PDF is fetched. See
  [references/output-schema.md](references/output-schema.md).
- **Graceful degradation, not hard failure.** Missing polite email
  skips Unpaywall; missing S2 key runs S2 anonymously. `doctor` reports
  these as warnings.
- **Concurrency: 4.** Batch mode uses `asyncio.Semaphore(4)`. NDJSON
  lines flush as each paper completes, so order ≠ input order — every
  line carries an `index` matching the position of that identifier in
  the input.

## Non-goals

The following are deliberately **not** handled by `sf-download`:

1. **Sci-Hub / LibGen / any pirated mirror.** Only legal, public sources.
2. **CDP / browser control / institutional session reuse.** Future
   `sf-download-via-browser` or an external tool.
3. **Topic / keyword search.** Discovery is `paperhound`,
   `deep-research`, or a future `sf-search` skill; those produce
   identifier lists that this skill consumes.
4. **Local library management / metadata indexing / full-text search.**
   That is `sf-lit`'s domain.
5. **BibTeX / RIS export.** Use `sf-lit export`.
6. **PDF → Markdown conversion.** Use `sf-lit convert`.
7. **Supplementary Information (SI) scraping.** Not part of this pipeline.
8. **Metadata correction / manual DOI mapping.** The five sources are
   the source of truth; `sf-download` reports what they say.
9. **PDF content validation beyond the byte header.** No page counts,
   OCR, or text extraction — that is `sf-lit convert`'s job. This skill
   only checks: `%PDF` magic bytes, non-zero size, and `Content-Type`
   (when the server sends one).

## Exit codes

Per [ADR-0006](../../docs/adr/0006-minimum-output-contract.md):

| Code | Meaning | When |
|---|---|---|
| `0` | Success | Batch mode: always (unless startup crashes); single mode: `downloaded / paywalled / metadata_only / pdf_link_broken` |
| `2` | Invalid user input | Unparseable identifier, conflicting flags, `--from-file` missing |
| `3` | Resource not found | Single mode: `identifier_not_found`, or `--title` produced no candidates at all |
| `1`, `≥64` | Runtime error | Uncaught exception, config file corrupt |

Batch mode **never propagates single-paper failure** to exit code —
callers inspect the per-line `status`.

## Configuration

Config lives in `SCIFORGE_CONFIG` (same file `sf-lit` reads), under a
new `[download]` section:

```toml
[download]
polite_email = "you@example.com"    # optional; used for Unpaywall + Crossref + OpenAlex
semanticscholar_api_key = ""        # optional; anonymous S2 works but hits 429 sooner
http_timeout_seconds = 30
```

Environment overrides (env > config > default):

- `SCIFORGE_POLITE_EMAIL`
- `SCIFORGE_S2_API_KEY`
- `SCIFORGE_HTTP_TIMEOUT`
- `SCIFORGE_DOWNLOAD_DIR`

Details in [references/config.md](references/config.md).

## Reference documents

Read on demand:

- [references/config.md](references/config.md) — full config schema, env vars, degradation rules
- [references/sources.md](references/sources.md) — per-source API endpoints, request shapes, field mapping
- [references/status-codes.md](references/status-codes.md) — precise triggers for each of the 9 status codes
- [references/output-schema.md](references/output-schema.md) — pydantic models, NDJSON structure, filename sanitization
- [references/recipes.md](references/recipes.md) — invocation patterns including `sf-lit` pipe examples

## Verification

Requires network access to the 5 API endpoints. Set at least a polite
email to exercise Unpaywall:

```bash
export SCIFORGE_POLITE_EMAIL="you@example.com"

# 1. Environment self-check
scripts/sf-download doctor

# 2. Single arXiv (works with no config)
scripts/sf-download 1706.03762 --emit-json
# expect: status=downloaded, source_used=arxiv, pdf at ~/.sciforge/inbox/1706.03762.pdf

# 3. Single DOI (goes through Unpaywall / S2 / Crossref)
scripts/sf-download 10.1038/s41586-020-2649-2 --emit-json
# expect: status=downloaded (OA), source_used one of unpaywall/s2/crossref

# 4. Paywalled article
scripts/sf-download 10.1038/nature12373 --emit-json
# expect: status=paywalled

# 5. Nonexistent DOI
scripts/sf-download 10.9999/definitely-not-a-real-doi --emit-json
# expect: status=identifier_not_found, exit 3

# 6. Batch mode (NDJSON stream + summary)
printf "1706.03762\n10.1038/s41586-020-2649-2\n10.9999/bad\n" > /tmp/ids.txt
scripts/sf-download --from-file /tmp/ids.txt --emit-json
# expect: 3 per-paper lines with index 0..2 (any order) + a summary line, exit 0

# 7. End-to-end pipe to sf-lit
scripts/sf-download 1706.03762 --emit-json > /tmp/r.jsonl
jq -c 'select(.status=="downloaded") | .meta' /tmp/r.jsonl \
  | ../literature/scripts/sf-lit add --meta-json - \
      --pdf-path "$(jq -r 'select(.status=="downloaded") | .pdf_path' /tmp/r.jsonl)" \
      --move-pdf --and-convert
```

Unit tests:

```bash
pip install pytest respx
pytest skills/download/tests/
```

## Modules

Modules live under `scripts/`:

- Entry point: [scripts/sf-download](scripts/sf-download)
- CLI dispatch: [scripts/main.py](scripts/main.py)
- Identifier normalization: [scripts/identifiers.py](scripts/identifiers.py)
- Per-source clients: [scripts/sources/](scripts/sources/) (`arxiv.py`, `crossref.py`, `unpaywall.py`, `openalex.py`, `semanticscholar.py`)
- Orchestrator (fallback chain + metadata union): [scripts/fetch.py](scripts/fetch.py)
- PDF download + header check + filename: [scripts/pdf.py](scripts/pdf.py)
- Environment self-check: [scripts/doctor.py](scripts/doctor.py)
- Configuration loader: [scripts/config.py](scripts/config.py)
- Pydantic models + NDJSON writer: [scripts/output.py](scripts/output.py)
