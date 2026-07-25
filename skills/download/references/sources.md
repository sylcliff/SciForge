# Sources — API endpoints and field mapping

This document is the ground truth for how `sf-download` talks to each of
its five sources. If a field name disagrees between this doc and the
code, the doc is the intent — file a bug or update the doc.

Every source module (`scripts/sources/<name>.py`) exposes a single
async function that takes normalized identifier(s) + a config bundle,
makes exactly one API call (plus at most one polite retry), and returns
a `SourceResult` with `{meta, pdf_url_hint, raw_status}`. Errors are
returned, never raised across the module boundary — the orchestrator
maps them to status codes.

## arXiv

**Purpose**: PDF + metadata for arXiv IDs.

**Endpoint**:
```
GET http://export.arxiv.org/api/query?id_list=<arxiv-id>
```

- **No auth**. No email required.
- Returns Atom XML (not JSON) — parsed with stdlib `xml.etree.ElementTree`.
- Throttle: arXiv asks for ≥3s between calls per IP. Concurrency 4 with
  batches of ~10 stays comfortably under (spread naturally by network
  jitter); if we ever hit sustained 429/5xx, add a per-source semaphore.

**Fields extracted → ingest schema**:

| arXiv Atom | `meta` field |
|---|---|
| `<title>` | `title` (strip newlines + collapse whitespace) |
| `<author>/<name>` list | `authors` (in order) |
| `<summary>` | `abstract` |
| `<published>` YYYY-MM | `year` (parse integer) |
| id (`<id>`) tail | `arxiv_id` (strip `v1/v2/…` version suffix) |
| `<link rel="alternate" type="text/html">` | `url` |
| `arxiv:doi` (when present) | `doi` |
| `<arxiv:primary_category>` | (not exposed in ingest schema; ignored) |

**PDF URL**: constructed directly, no HEAD probe:
```
https://arxiv.org/pdf/<arxiv-id>.pdf
```

**404 handling**: when `id_list` matches no paper, arXiv returns an
Atom feed with `<opensearch:totalResults>0</opensearch:totalResults>`.
Map to `identifier_not_found` for arXiv-only invocations; otherwise
just skip arXiv in the fallback chain.

---

## Crossref

**Purpose**: authoritative DOI metadata + potential OA links.

**Endpoint**:
```
GET https://api.crossref.org/works/<doi>[?mailto=<email>]
```

- **No auth**. `mailto` query param puts you in the polite pool.
- Returns JSON. Response envelope: `{"status":"ok","message":{...}}` —
  metadata is under `message`.

**Fields extracted → ingest schema**:

| Crossref path | `meta` field |
|---|---|
| `message.title[0]` | `title` |
| `message.author[]` → `given + " " + family` | `authors` |
| `message.abstract` (HTML-tagged) | `abstract` (strip `<jats:*>` tags) |
| `message.issued.date-parts[0][0]` | `year` |
| `message.container-title[0]` | `venue_full` |
| `message.short-container-title[0]` | `venue` |
| `message.DOI` (lowercased) | `doi` |
| `message.URL` | `url` |

**PDF URL hint**: iterate `message.link[]`, keep the first entry with
`content-type = application/pdf`. Report it as a candidate; the
orchestrator only actually fetches it if all higher-priority sources
missed. Many Crossref link entries are TDM-only and will 403 — that
manifests as `pdf_link_broken` if we tried it, or is bypassed if
Unpaywall/S2 already gave us a PDF.

**404 handling**: Crossref returns HTTP 404 for unknown DOIs. Map to
`identifier_not_found` when Crossref is the sole source (rare —
usually OpenAlex still knows).

---

## Unpaywall

**Purpose**: definitive OA PDF URL for a DOI.

**Endpoint**:
```
GET https://api.unpaywall.org/v2/<doi>?email=<email>
```

- **Email is mandatory.** Requests without `email=` return HTTP 400.
  If `polite_email` is unset, `sf-download` skips Unpaywall entirely
  (no request made) and emits a warning in `doctor`.
- Returns JSON.

**Decision logic**:

1. If `is_oa == false`, treat as authoritative "no OA copy":
   - If no other source provided a PDF, status becomes `paywalled`.
   - Metadata (title/authors/year) may still be harvested from other sources.

2. If `is_oa == true`, pick the PDF URL from `best_oa_location.url_for_pdf`.
   If that is null, try `best_oa_location.url` (may be a landing page
   with an inline PDF viewer — the PDF verifier will catch it and
   downgrade to `pdf_link_broken` if bytes aren't `%PDF`).

**Fields extracted → ingest schema** (used only to fill gaps left by Crossref/S2):

| Unpaywall path | `meta` field |
|---|---|
| `title` | `title` |
| `doi` | `doi` |
| `year` | `year` |
| `journal_name` | `venue_full` |
| `oa_locations[].url` (best) | `url` |

**404 handling**: unknown DOI → HTTP 404. Skip Unpaywall in the
fallback chain.

---

## OpenAlex

**Purpose**: metadata fallback for DOI / OpenAlex ID; title search.

**Endpoints**:

```
GET https://api.openalex.org/works/https://doi.org/<doi>[?mailto=<email>]
GET https://api.openalex.org/works/<openalex-id>[?mailto=<email>]
GET https://api.openalex.org/works?search=<title>&per_page=5[&mailto=<email>]
```

- **No auth**. `mailto` param → polite pool.
- OpenAlex accepts DOI URL, bare DOI, or bare OpenAlex ID as the path segment.
- Returns JSON.

**Fields extracted → ingest schema**:

| OpenAlex path | `meta` field |
|---|---|
| `title` | `title` |
| `authorships[].author.display_name` | `authors` |
| `abstract_inverted_index` (reconstructed) | `abstract` |
| `publication_year` | `year` |
| `host_venue.display_name` (v1) / `primary_location.source.display_name` (v2) | `venue_full` |
| `host_venue.publisher` | (unused) |
| `doi` (may be prefixed with `https://doi.org/`) | `doi` (canonicalize) |
| `id` (URL form) | (unused; not stored) |

**Abstract reconstruction**: OpenAlex encodes abstracts as an inverted
index `{word: [positions...]}`. The module walks positions and
reconstructs the plain-text form. If reconstruction produces < 40
characters, drop the field rather than emit a fragment.

**PDF URL hint**: OpenAlex sometimes exposes
`primary_location.pdf_url`. Report it. Same "orchestrator decides
whether to try" rule as Crossref.

**Title search behaviour**: results are ranked by internal relevance.
`sf-download` inspects only the **top-1** hit and applies strict
normalized-equality (lowercase, strip punctuation and whitespace) —
see [output-schema.md](output-schema.md#title-fallback). If the top-1
does not match, up to 3 candidates are surfaced in the response and no
PDF is fetched.

**404 handling**: `works/<unknown>` → HTTP 404. Skip in the fallback
chain. `works?search=<no-hits>` → returns `results: []` and
`meta.count: 0`; report `identifier_not_found`.

---

## Semantic Scholar

**Purpose**: OA PDF via `openAccessPdf`, plus rich metadata.

**Endpoint**:
```
GET https://api.semanticscholar.org/graph/v1/paper/<id>?fields=<field-list>
```

`<id>` accepts many forms; we use:

- `DOI:<doi>` for DOI lookups
- `arXiv:<id>` for arXiv IDs
- `<40-char hash>` for S2 paper IDs (as-is)

**Fields to request**:

```
title,authors,abstract,year,venue,publicationVenue,externalIds,
openAccessPdf,url,paperId,tldr
```

- **No auth required**, but `x-api-key: <key>` header is strongly
  recommended (anonymous shares a ~100 req/5min global pool).
- Returns JSON.

**Fields extracted → ingest schema**:

| S2 path | `meta` field |
|---|---|
| `title` | `title` |
| `authors[].name` | `authors` |
| `abstract` (or `tldr.text` if abstract absent) | `abstract` |
| `year` | `year` |
| `publicationVenue.name` (fallback: `venue`) | `venue_full` |
| `externalIds.DOI` | `doi` |
| `externalIds.ArXiv` | `arxiv_id` |
| `paperId` | `s2_paper_id` |
| `url` | `url` |

**PDF URL hint**: `openAccessPdf.url` when non-null. Trusted; the
orchestrator will attempt this before Crossref links.

**404 handling**: HTTP 404 → skip in fallback chain.

---

## Fallback and merge (orchestrator contract)

The orchestrator (`scripts/fetch.py`) drives sources in this order:

**Stage 1 — identifier resolution**

Given a normalized identifier:

- `arxiv-id` alone: query arXiv → if hit, we already have PDF + basic
  metadata. Optionally enrich with OpenAlex if the paper has a DOI.
- `DOI`: query Crossref, Unpaywall, S2, OpenAlex (all can consume DOI).
- `openalex-id` / `s2-hash`: query the corresponding source first to
  resolve to a DOI; then proceed as if it had been a DOI.
- `title` only: query OpenAlex search → strict top-1 match yields a
  DOI → proceed. Otherwise `title_ambiguous`.

**Stage 2 — PDF acquisition (first hit wins)**

Try the sources' PDF URL hints in this order:

1. arXiv (if we have an arXiv ID)
2. Unpaywall's `best_oa_location.url_for_pdf` (skipped if no email or
   `is_oa=false`)
3. Semantic Scholar's `openAccessPdf.url`
4. Any Crossref `link[]` entry with `content-type = application/pdf`

On the first that yields a real PDF (bytes start with `%PDF`, size >
0, and `Content-Type` is `application/pdf` when present), stop. Set
`source_used` accordingly. If all four are absent, status becomes
`metadata_only`. If at least one yielded a URL but every URL failed
verification, status is `pdf_link_broken`.

**Stage 3 — metadata union**

For each ingest-schema field, walk priority order **Crossref > S2 >
OpenAlex > arXiv** and keep the first non-empty value. For list
fields (`authors`, `tags`), the highest-priority non-empty list wins
(no interleaving — cleaner, easier to reason about).

**Rate limits & retries** — see [`references/status-codes.md`](status-codes.md)
for how 429 / 5xx / timeout map to status codes.
