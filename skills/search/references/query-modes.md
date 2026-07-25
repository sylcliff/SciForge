# Query modes

`sf-search` accepts four **mutually exclusive** input modes. Passing
more than one → exit 2 with an error.

## 1. Positional (default — transparent relevance)

```bash
sf-search "graph neural network drug discovery"
```

- The string is sent to each source's default relevance search.
- No field qualifiers, no boolean rewriting.
- Every source gets the same string; each source's own relevance
  ranker decides the order.
- **Use when**: you want the maximum recall / minimum surprise. Most
  common everyday mode.

**Per-source translation:**
| Source | Sent as |
|---|---|
| PubMed | `esearch.fcgi?db=pubmed&term=<string>` |
| Crossref | `/works?query=<string>` |
| arXiv | `?search_query=all:<string>` |
| OpenAlex | `/works?search=<string>` |
| S2 | `/paper/search?query=<string>` |

## 2. `--query STR` — raw pass-through

```bash
sf-search --query '("graph neural network"[tiab] OR GNN) AND drug[tiab]'
```

- The string is sent **unchanged** to every source. No parsing, no
  translation.
- PubMed interprets `[tiab]` / `[MeSH]` / `[au]` / etc. as field
  qualifiers — this gives it precise semantics.
- Other sources treat the string as free text. Parentheses and
  operators are usually preserved but the field qualifiers are
  ignored.
- **Use when**: you know PubMed syntax and are willing to accept that
  non-PubMed sources will treat the query approximately.

Deliberate design: **no AST parsing, no per-source translation of
boolean operators.** If cross-source precision matters, use
`--from-strategy` (mode 4) with per-source `compiled` strings.

## 3. `--fields` — structured

```bash
sf-search --title "attention is all you need" --author "Vaswani"
sf-search --year 2020..2024 --journal Nature --title "protein folding"
```

Supported field flags (any subset, combined with implicit AND):

| Flag | Type | Compiles to |
|---|---|---|
| `--title STR` | phrase | PubMed `<str>[TI]`, Crossref `query.title=`, arXiv `ti:"<str>"`, OpenAlex `title.search=`, S2 title match |
| `--author STR` | phrase | PubMed `<str>[AU]`, Crossref `query.author=`, arXiv `au:"<str>"`, OpenAlex `author.display_name.search=`, S2 author match |
| `--year N` or `--year FROM..TO` | int / range | PubMed `<year>[DP]`, Crossref `filter=from-pub-date,until-pub-date`, arXiv date range, OpenAlex `publication_year=` or `from_publication_date=`, S2 `year=` or `year=<from>-<to>` |
| `--journal STR` | phrase | PubMed `<str>[TA]`, Crossref `query.container-title=`, OpenAlex `primary_location.source.display_name.search=` (arXiv/S2: applied post-hoc as filter) |

Multiple `--author` are OR'd (any author matches). Everything else is
AND'd.

## 4. `--from-strategy PATH`

```bash
sf-search --from-strategy strategy.json --top 50
```

Reads a `strategy.json` produced by `sf-search mesh build`. See
[mesh-strategy.md](mesh-strategy.md) for schema.

- The file's `compiled.<source>` block, if present, is used as the
  final per-source query string (bypasses the fields translation).
- If a source is missing a `compiled` entry, the skill falls back to
  the flattened concept list as a keyword query.
- **Use when**: doing a PRISMA-grade systematic review, or when the
  agent has built a MeSH strategy interactively.

## Filters that apply to all modes

```bash
--year 2020..2024          # post-hoc year filter (even in --query mode)
--sources pubmed,crossref  # narrow which sources to hit
--top N                    # visible result count (default 30)
--per-source-limit N       # per-source recall (default top*2, cap 100)
--sort relevance|year:desc|citations:desc
--format ndjson|ids|table|bib|ris
```

## Ambiguity rules

- If both positional and `--query` are present → exit 2.
- If `--fields` flags are present alongside positional → exit 2.
- If `--from-strategy` is present alongside anything else → exit 2.
- If **no** input is given (and not `mesh`/`doctor` subcommand) → exit 2.
