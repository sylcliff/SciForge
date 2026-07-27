---
name: search
description: Use when the user wants to discover papers by topic/keywords/boolean query/MeSH strategy across multiple public academic sources (PubMed, Crossref, arXiv, OpenAlex, Semantic Scholar), or to list one paper's outgoing references / incoming citations. Outputs deduped NDJSON that pipes directly into `sf-download` or `sf-lit add`. Also builds MeSH search strategies for PubMed.
---

# search — API-first multi-source discovery

Micro / companion skill. Given a topic, keyword, boolean query, structured
fields, or a MeSH strategy file, queries 3–5 public academic APIs in
parallel, deduplicates across sources, ranks by reciprocal-rank fusion,
and emits NDJSON where each line describes one paper (identifiers +
merged metadata + provenance). Designed to feed `sf-download` (fetch
PDFs) or `sf-lit add` (catalog without PDFs).

## Scope

- **In scope**: multi-source keyword / boolean / field / MeSH-strategy
  literature search over PubMed, Crossref, arXiv, OpenAlex, Semantic
  Scholar; MeSH term lookup and strategy compilation for PubMed;
  **citation-graph queries** — outgoing references and incoming
  citations for a single paper (see [Citation graph](#citation-graph)).
- **Out of scope**: PDF fetching (that's `sf-download`), local library
  management (`sf-lit`), citation verification / strict他引 audits /
  citation-file conversion / reference management — those become their
  own micro skills. Google Scholar / WoS / Scopus / CNKI are also out
  of scope (see [Non-goals](#non-goals)).

## Data sources

| Source | Auth | Best for |
|---|---|---|
| PubMed E-utilities | polite email (recommended) | Biomedical, MeSH indexing |
| Crossref REST | polite mailto (recommended) | Cross-disciplinary, DOIs |
| arXiv Atom API | none (3 s min interval) | Preprints: physics / math / CS / q-bio |
| OpenAlex REST | polite mailto (recommended) | Cross-disciplinary + citation counts |
| Semantic Scholar Graph API | API key (recommended) | Citation counts, field-of-study filters |

**Ranking**: Reciprocal Rank Fusion (RRF, k=60) over each source's own
relevance rank; `--sort` overrides to `year:desc` or `citations:desc`.

**Dedup**: union-find on `(doi, pmid, pmcid, arxiv_id, openalex_id,
s2_id)` — any shared identifier merges two records. Title-based fuzzy
matching is deliberately **not** done.

**arXiv preprint ↔ journal upgrade** happens in three tiers:

- **Path A (zero HTTP)** — always on. Adapters mine cross-refs from
  OpenAlex `locations[*].landing_page_url` / `open_access.oa_url`,
  Crossref `relation.has-preprint` / `is-preprint-of`, and arXiv
  `<arxiv:doi>` / `<arxiv:journal_ref>` / `<arxiv:comment>` (scanned
  for embedded DOIs). Whatever preprint↔journal pairs exist in these
  fields get β-deduped for free.
- **Path B (post-dedup lookup)** — on by default; disable with
  `--no-arxiv-upgrade`. For groups that are still arxiv-only after
  β dedup, concurrent OpenAlex + Semantic Scholar lookup by arxiv id.
  OpenAlex uses a **two-hop query** (preprint DOI → title →
  `type:article`) because OpenAlex represents preprint and journal as
  two separate work IDs. DataCite arxiv self-DOIs (`10.48550/arxiv.*`)
  are explicitly rejected as "journal DOIs". First real journal DOI
  wins; dedup then re-runs so preprint and journal records merge.
- **Path C (optional)** — `--arxiv-upgrade-fallback title-search`.
  When Path B fails, query Crossref by the preprint's title; accept
  only when title Jaccard ≥ 0.85 AND first-author surname matches.

**Post-hoc verification** — after any upgrade returns a DOI, that DOI
is cross-checked against Crossref (`/works/{doi}`) for year (±3
tolerance) + first-author surname agreement. A mismatch drops the
upgrade; a network flake keeps it (fail-open). This catches
plausible-looking but wrong DOIs before injection.

The upgrade sources do **not** enter `sources_hit` — they only inject
a DOI. Final records instead carry `arxiv_upgraded: true` and
`arxiv_upgrade_via: "id-lookup" | "title-search"` for audit.

**Metadata merge (higher priority wins per field)**:
- `title`, `abstract`: Crossref → OpenAlex → PubMed → S2 → arXiv
- `authors`: Crossref → PubMed → OpenAlex → S2 → arXiv
- `journal`, `volume`: Crossref → OpenAlex → PubMed
- `year`: earliest non-empty
- `citation_count`: `max(S2, OpenAlex)`
- `is_oa`: OpenAlex authoritative; `null` when no OpenAlex hit
- `identifiers` (incl. `pmcid`), `sources_hit`: union

Missing credentials degrade gracefully — no `polite_email` still hits
Crossref / PubMed / OpenAlex (unpolite pool), no `s2_api_key` runs S2
anonymously. Shares env vars with `sf-download`:
`SCIFORGE_POLITE_EMAIL`, `SCIFORGE_S2_API_KEY`.

## When to invoke

- User wants to **find** papers by topic / keywords / boolean / MeSH
- User has a batch of queries to run (`--from-file queries.txt`)
- User has a pre-built MeSH strategy JSON to execute
  (`--from-strategy strategy.json`)
- User wants to **build a MeSH search strategy** for PubMed
  (`sf-search mesh lookup / build / check`)
- User wants **a paper's outgoing references** (`sf-search refs <id>`)
  or **incoming citations** (`sf-search cited-by <id>`).

Do **not** invoke this skill for:

- Fetching a PDF for a paper the user *already* identified — that's
  `sf-download`
- Local library management — that's `sf-lit`
- Google Scholar / Web of Science / Scopus / CNKI access — those sources
  are either scraped (fragile) or paywalled (no public API). This skill
  refuses to fake them

## First-run check

```bash
scripts/sf-search doctor
```

`doctor` reports the polite email, S2 API key presence, and per-source
reachability. It never fails hard; missing config is reported as a
warning.

## Routing

Every invocation is one of these:

| User asks for | Command |
|---|---|
| Search by topic (default relevance) | `scripts/sf-search "graph neural network drug discovery"` |
| Search by raw boolean (transparent pass-through) | `scripts/sf-search --query '(GNN OR "graph neural network") AND drug[tiab]'` |
| Search by structured fields | `scripts/sf-search --title "attention" --author "Vaswani" --year 2017` |
| Execute a MeSH strategy file | `scripts/sf-search --from-strategy strategy.json --top 50` |
| Run a batch of queries | `scripts/sf-search --from-file queries.txt` |
| Look up MeSH terms | `scripts/sf-search mesh lookup --concept "diabetes" --concept "heart failure"` |
| Build a strategy file | `scripts/sf-search mesh build --mesh "Diabetes Mellitus" --synonym diabetes --op AND -o strategy.json` |
| Sanity-check a strategy | `scripts/sf-search mesh check strategy.json` |
| One paper's outgoing references | `scripts/sf-search refs <doi\|pmid\|arxiv\|W…\|sciforge://…>` |
| One paper's incoming citations | `scripts/sf-search cited-by <id> [--top N \| --all] [--sort ...]` |
| Self-check | `scripts/sf-search doctor` |

The four **query input modes** are mutually exclusive:

1. Positional query — the plain-text default, transparent-relevance
2. `--query STR` — raw string passed **unchanged** to every source;
   PubMed will interpret `[tiab]` / `[MeSH]`, others treat it as
   free text
3. `--fields` — one or more of `--title`, `--author`, `--year`,
   `--journal`; the skill compiles per-source field-qualified queries
4. `--from-strategy PATH` — read a strategy JSON with a
   `compiled.<source>` block per source

## Output

Every invocation emits **NDJSON on stdout**, one paper per line. Batch
runs (`--from-file`) end with one `{"summary": ...}` line.

Per-paper line (see [references/output-schema.md](references/output-schema.md)
for the full schema):

```jsonc
{
  "index": 0,
  "identifier": "10.1038/s41586-020-2649-2",   // best available ID (DOI > PMID > pmcid > arxiv_id > openalex_id > s2_id)
  "sources_hit": ["crossref", "openalex", "pubmed"],
  "score": 0.048,                              // RRF (or year / citations when --sort overrides)
  "rank_by_source": {"crossref": 1, "openalex": 2, "pubmed": 3},
  "dedup_group": ["10.1038/s41586-020-2649-2", "32939066", "PMC7480694"],
  "arxiv_upgraded": false,                     // true iff DOI came from arxiv-upgrade lookup
  // "arxiv_upgrade_via": "id-lookup" | "title-search"   // only present when arxiv_upgraded=true
  "meta": {
    "title": "Array programming with NumPy",
    "authors": ["Charles R. Harris", "..."],
    "year": 2020,
    "doi": "10.1038/s41586-020-2649-2",
    "pmid": "32939066",
    "pmcid": "PMC7480694",
    "arxiv_id": null,
    "openalex_id": "W3082521543",
    "s2_id": null,
    "url": "https://www.nature.com/articles/s41586-020-2649-2",
    "journal": "Nature",
    "abstract": "...",
    "citation_count": 4211,
    "is_oa": true                              // OpenAlex authoritative; null if no OA hit
  }
}
```

The `meta` object is a strict superset of
[`skills/literature/references/ingest-interface.md`](../literature/references/ingest-interface.md)
v2 and matches [`skills/download/references/output-schema.md`](../download/references/output-schema.md)
exactly — so `sf-search ... | sf-lit add --meta-json -` works for the
"index without PDF" path, and
`sf-search ... --format ids | sf-download --from-file -` works for the
"fetch PDFs too" path.

## Alternate output formats

`--format` picks the wire format (default `ndjson`):

| Format | Purpose |
|---|---|
| `ndjson` | Machine, one JSON per line. Feeds `sf-lit add --meta-json -` |
| `ids`    | One identifier per line. Feeds `sf-download --from-file -` |
| `table`  | Human, aligned columns (title / authors / year / venue / DOI / citations) |
| `bib`    | BibTeX, one entry per result. Feeds Zotero / EndNote / LaTeX |
| `ris`    | RIS, one entry per result. Same downstream audience as `bib` |

## Agent presentation contract

When an agent (Claude / Codex / another LLM) surfaces `sf-search`
results to a human in conversation, it MUST follow the three-section
rendering standard documented in the memory file
[`sf-search-presentation`](../../memory/sf-search-presentation.md).

Summary of the contract (see memory for full detail):

1. **Section 0** — Echo the original CLI command + a 3-line summary of
   counts, sources hit/failed, dedup shrink, arxiv-upgrade count, OA
   estimate, and疑似噪声 estimate. Every number comes from the actual
   NDJSON output.
2. **Section 1** — **Fixed 5 recommendation slots** (顶级综述 / 奠基
   里程碑 / 核心方法 / 新兴前沿 / 应用实证), density C (3-4 lines +
   abstract snippet + reason). Empty slot → `(此查询未见相关论文)`;
   do NOT force-fill.
3. **Section 2** — **Fixed 4 groups** with dynamic naming; the agent
   must print an explicit `📁 分组:...` header. Each group renders its
   top 5 by RRF order (fewer if the group has <5 records), density B.
4. **Section 3** — Full list, density A (1 line/paper), default always
   rendered, hard cap 100 (overflow references `--out FILE`).

Recommendation reasons MUST use the closed 6-tag set
`[综述] / [里程碑] / [方法核心] / [新方向] / [应用] / [疑似噪声]` and
give a *refutable* concrete basis — no `必读 / 经典 / 很重要` and
similar empty praise.

The `--format {ndjson,ids,table,bib,ris}` CLI outputs are **wire
formats for machines** (or human eyeballing via `table`); the
presentation contract is orthogonal — it applies whenever an agent
turns those wire outputs into a chat-facing recap.

## MeSH strategy (workflow)

The MeSH subcommands are **stateless, offline-compatible** where
possible, and produce a durable `strategy.json` artifact that can be
committed to a repo for PRISMA / systematic-review provenance.

```bash
# 1. Discover MeSH terms per concept (touches PubMed einfo + efetch)
scripts/sf-search mesh lookup --concept "diabetes" --concept "heart failure"
  → NDJSON: candidates with parents / children / synonyms per concept

# 2. Build strategy.json (pure local — no network)
scripts/sf-search mesh build \
    --mesh "Diabetes Mellitus" --synonym diabetes --synonym diabetic \
    --mesh "Heart Failure" --synonym "cardiac failure" \
    --op AND \
    -o strategy.json

# 3. Sanity-check (espell + esearch count on PubMed)
scripts/sf-search mesh check strategy.json
  → {"pubmed_count": 3421, "suggested_corrections": [], "compiled": {...}}

# 4. Execute across all sources
scripts/sf-search --from-strategy strategy.json --top 50
```

**Concept splitting is not done by this skill** — it is agent-driven.
The host agent decides how to break "diabetes AND heart failure" into
concepts and passes them to `mesh lookup --concept ...`. This keeps the
CLI zero-LLM-dependency and reproducible.

See [references/mesh-strategy.md](references/mesh-strategy.md) for the
`strategy.json` schema and per-source compilation rules.

## Citation graph

Two sibling subcommands walk the citation graph of one seed paper:

```bash
scripts/sf-search refs      <id> [--top N] [--out FILE]     # outgoing refs
scripts/sf-search cited-by  <id> [--top N | --all]          # incoming citations
                                 [--sort citations:desc|year:desc]
```

`<id>` may be a DOI, PMID, arxiv id (modern or old-style), OpenAlex
`W…` id, Semantic Scholar SHA1, or `sciforge://literature/<citekey>`
(resolved via `sf-lit show --json`).

Coverage summary — set-valued union across sources, no RRF:

| Source | refs | cited-by |
|---|---|---|
| OpenAlex | ✓ | ✓ |
| Semantic Scholar | ✓ | ✓ |
| Crossref | ✓ (via `reference[]`) | — (Event Data API not covered) |
| PubMed | PMID + PMC only | PMID + PMC only |
| arXiv | — | — |

Output records use the same NDJSON shape as main search results
(so they pipe cleanly into `sf-download --from-file -` and
`sf-lit add --meta-json -`), with two additions — `direction` and
`seed` — and three subtractions — `score`, `rank_by_source`,
`dedup_group` (RRF isn't computed here). Records that only have a
free-text bibliographic string (unresolved Crossref `reference[]`
entries) can be diverted to `--out-unresolved FILE`; they never enter
the main stream. Full spec: [references/citations.md](references/citations.md).

**Agent presentation**: when surfacing `refs` / `cited-by` results in
conversation, follow the simplified rendering rules in the memory file
`sf-search-citation-presentation` (Section 0 citation summary + Section 3
full list — no 5-recommendation, no 4-group section, because a citation
edge has no relevance rank).

## Sources, per-source limits, concurrency

Single-query default: **all 5 sources in parallel, no rate-limit**
(each source is called 1–2 times). `--sources pubmed,crossref` narrows.

Batch mode (`--from-file`): a **per-source token bucket** serializes
requests; arXiv's 3-second minimum interval is honored. See
[references/sources.md](references/sources.md) for exact rates and
endpoints.

`--per-source-limit N` caps the results pulled from each source before
dedup (default `top × 2`, capped at 100). For systematic reviews:
`--top 200 --per-source-limit 500`.

## Configuration

| Env var | Effect | Fallback |
|---|---|---|
| `SCIFORGE_POLITE_EMAIL` | Sent as `mailto=` (Crossref, OpenAlex) and User-Agent contact (PubMed) | Unpolite pool |
| `SCIFORGE_S2_API_KEY` | Semantic Scholar auth (`x-api-key` header) | Anonymous (1 req/s) |

Shared with `sf-download` — set once, both skills pick them up.

## Exit codes

Follows SciForge ADR-0006:

| Code | Meaning |
|---|---|
| `0` | Success |
| `2` | Invalid input (mutually exclusive modes given, malformed strategy JSON, bad `--sort`) |
| `3` | No results from any source |
| `5` | All sources failed (network / API error). Individual source failures degrade gracefully |

## Non-goals

- **PDF download** — `sf-download` owns that
- **Local library / conversion / notes** — `sf-lit` owns that
- **Google Scholar / Web of Science / Scopus / CNKI** — no stable public
  API. Adding them would require scraping (fragile, ToS-risky) or
  institutional proxy (not portable). Users needing Scopus / WoS should
  configure the `nature-academic-search` skill's MCP backend instead
- **Citation verification** — future `sf-cite-verify` micro skill
- **Strict other-citation audit** — future `sf-cite-audit` micro skill
- **Reference / .bib / .ris management** — future `sf-refconv` micro
  skill
- **LLM-driven concept splitting** — the host agent handles that

## Interaction rules

- **Read-only externally**. Only HTTP GETs to public APIs. Writes are
  limited to the file passed to `-o` (mesh build) or `--out`.
- **Idempotent**: same query → same result set (up to source-side index
  churn)
- **Never blocks on missing config** — degrades to unpolite pools,
  anonymous S2, reports the degradation in `doctor`
- **Concurrent by default**: 5 threads for the 5 sources; each source
  adapter is self-contained and thread-safe

## Reference documents

- [references/output-schema.md](references/output-schema.md) — NDJSON wire format
- [references/query-modes.md](references/query-modes.md) — the four input modes
- [references/sources.md](references/sources.md) — endpoints, rate limits, query languages
- [references/mesh-strategy.md](references/mesh-strategy.md) — MeSH workflow & strategy schema
- [references/citations.md](references/citations.md) — `refs` / `cited-by` full spec (seed types, coverage matrix, unresolved stream, exit codes)
- [references/config.md](references/config.md) — env vars, polite email, S2 key

## Verification

```bash
scripts/sf-search doctor                                       # per-source reachability
scripts/sf-search "attention is all you need" --top 5          # smoke: transformer paper
scripts/sf-search --title "attention is all you need" --top 3  # field mode
scripts/sf-search "topic" --format ids | head -5               # pipe-into-sf-download shape
scripts/sf-search refs 10.1038/s41586-020-2649-2 --top 5       # citation graph — outgoing
scripts/sf-search cited-by 1706.03762 --top 5                  # citation graph — incoming
```

For the full pipeline:

```bash
scripts/sf-search "graph neural network drug discovery" --top 10 --format ids \
  | ../download/scripts/sf-download --from-file - \
  | ../literature/scripts/sf-lit add --meta-json -
```
