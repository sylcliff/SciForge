# Sources

Every source is a public REST API reachable over HTTPS with no auth
required (though polite email / API keys unlock better rate limits).
Adapters live in `scripts/sources/<name>.py` — each exports one
function `search(query_obj, limit) -> list[Record]`.

## Common record shape

Each adapter returns a list of dicts with these keys (any may be
`None`):

```python
{
  "source": "pubmed",              # constant per adapter
  "rank": 3,                       # 1-based within this source's returned list
  "doi": "10.1038/...",
  "pmid": "32939066",
  "arxiv_id": None,
  "openalex_id": None,
  "s2_id": None,
  "title": "...",
  "authors": ["A B", "C D"],
  "year": 2020,
  "journal": "Nature",
  "volume": "585",
  "issue": "7825",
  "pages": "357-362",
  "abstract": "...",
  "citation_count": 4211,
  "type": "journal-article",
  "url": "https://...",
}
```

## PubMed E-utilities

Base: `https://eutils.ncbi.nlm.nih.gov/entrez/eutils/`

- `esearch.fcgi?db=pubmed&term=<query>&retmax=<limit>&retmode=json`
  → list of PMIDs + total count
- `efetch.fcgi?db=pubmed&id=<pmid,pmid,...>&retmode=xml&rettype=abstract`
  → full records (XML), one round-trip for all PMIDs

Rate limit: **3 req/s** without key, 10 req/s with `api_key=` param.
This skill does not read an NCBI key (only polite email); rate limiter
honors 3 req/s.

**MeSH endpoints** (used by `mesh` subcommands):
- `esearch.fcgi?db=mesh&term=<term>` → MeSH UIDs
- `efetch.fcgi?db=mesh&id=<uid>&retmode=xml` → tree (parents / children / synonyms)
- `espell.fcgi?db=pubmed&term=<query>` → spelling suggestions

**Query features** (via `--query` pass-through):
- `[tiab]` — title / abstract
- `[MeSH]` — MeSH term
- `[au]` — author
- `[DP]` — date of publication
- `[TA]` — journal (title abbreviation)
- Full boolean: `AND`, `OR`, `NOT`, parens

## Crossref

Base: `https://api.crossref.org/works`

- `?query=<string>&rows=<limit>` — free-text
- `?query.title=<t>&query.author=<a>&query.container-title=<j>` — field
- `?filter=from-pub-date:2020-01-01,until-pub-date:2024-12-31` — dates

Rate limit: **50 req/s** anonymous; polite pool with `mailto=<email>`
gets priority queue. No hard block, honor
[Crossref's polite guidelines](https://api.crossref.org/swagger-ui/index.html).

User-Agent header: `sf-search/<ver> (mailto:<polite_email>)`.

## arXiv

Base: `http://export.arxiv.org/api/query`

- `?search_query=all:<terms>&start=0&max_results=<limit>` — free-text
- `?search_query=ti:"<title>"+AND+au:"<author>"` — field-qualified
- `sortBy=relevance|submittedDate|lastUpdatedDate&sortOrder=descending`

Returns **Atom XML**. Parse with `xml.etree.ElementTree` (stdlib).

Rate limit: **3-second minimum interval between requests** (arXiv is
explicit). Token bucket enforces 1 req / 3 s in batch mode; in
single-query mode the adapter fires once and doesn't loop.

## OpenAlex

Base: `https://api.openalex.org/works`

- `?search=<string>&per-page=<limit>` — free-text
- `?filter=title.search:<t>,author.display_name.search:<a>,publication_year:2020-2024`

Rate limit: **10 req/s** anonymous, 100 000/day; polite pool with
`mailto=<email>` unlocks faster limits. No hard cap in single-query
mode.

**Bonus fields** OpenAlex gives us: `cited_by_count`, `type`,
`concepts` (topic tags), `open_access.oa_url`.

## Semantic Scholar Graph API

Base: `https://api.semanticscholar.org/graph/v1/paper/search`

- `?query=<string>&limit=<limit>&fields=title,authors,year,citationCount,externalIds,abstract,venue`
- `x-api-key: <SCIFORGE_S2_API_KEY>` header if set

Rate limit: **1 req/s anonymous**, 100 req/s with key.

`externalIds` gives us `DOI`, `PubMed`, `ArXiv`, `CorpusId` — the
richest cross-ref information of any source.

## Source failure semantics

Each adapter can raise `SourceError(source, reason)`. `main.py`:

- Catches per-source, records in `sources_failed`
- Continues with the remaining sources
- If **all** requested sources fail → exit 5
- If some sources return 0 hits — normal; not a failure

Timeouts: **10 s per HTTP call**, no retries. A slow source is a failed
source (better than blocking the whole search on one straggler).

## Per-source query compilation

The `query.py` module compiles a normalized `QueryObject`:

```python
@dataclass
class QueryObject:
    mode: Literal["keyword", "raw", "fields", "strategy"]
    text: str | None                     # keyword / raw
    fields: dict[str, Any] | None        # fields
    per_source: dict[str, str] | None    # strategy — per-source compiled
    year_from: int | None
    year_to: int | None
```

Each source adapter reads `query_obj` and constructs its request URL.
Fields → per-source syntax translation lives inline in each adapter
(not centralized), because each source's query DSL is idiosyncratic.
