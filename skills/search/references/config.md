# Configuration

`sf-search` reads two environment variables. Both are **optional** —
the skill runs without either, but with reduced rate limits.

**Shared with `sf-download`.** Set once, both skills pick up.

## `SCIFORGE_POLITE_EMAIL`

An email address you own. Sent to sources that offer a "polite pool"
(better rate limits, priority queue).

- **Crossref**: `mailto=<email>` query param + User-Agent contact.
  Without it: same endpoint, unpolite pool.
- **OpenAlex**: `mailto=<email>` query param. Without it: `10 req/s`
  hard cap; with it: `100 000 req/day` polite pool.
- **PubMed**: User-Agent contact `sf-search/<ver> (<email>)`. Purely
  a courtesy; NCBI does not gate on it.
- **arXiv / S2**: not used.

Example:
```bash
export SCIFORGE_POLITE_EMAIL=you@example.edu
```

## `SCIFORGE_S2_API_KEY`

A Semantic Scholar API key. Sent as `x-api-key` header.

- **Without**: 1 request per second, shared anonymous pool. Often
  throttled at peak times.
- **With**: 100 req/s per key. Free at
  https://www.semanticscholar.org/product/api

Example:
```bash
export SCIFORGE_S2_API_KEY=abc123...
```

## What `doctor` reports

```bash
sf-search doctor
```

Output:
```
sf-search doctor
================
polite email:         you@example.edu           (from SCIFORGE_POLITE_EMAIL)
S2 API key:           set (32 chars)            (from SCIFORGE_S2_API_KEY)
python:               3.11.5
per-source reachability:
  pubmed              ok  (156 ms)
  crossref            ok  (89 ms)
  arxiv               ok  (312 ms)
  openalex            ok  (94 ms)
  s2                  ok  (201 ms)
```

Doctor **never exits non-zero for missing config** — only for
programming errors (unreadable script, broken imports). Missing polite
email or S2 key is a warning, not an error.

## Non-configuration

These are **hard-coded, not tunable**:

| Value | Where | Why |
|---|---|---|
| HTTP timeout: 10 s | `config.py` | Balances "slow but real source" vs "hung request" |
| arXiv min interval: 3 s | `sources/arxiv.py` | arXiv's own docs mandate this |
| RRF k: 60 | `rank.py` | Standard from TREC literature |
| Dedup key set: DOI + PMID + arXiv-id + S2-hash | `dedup.py` | Any looser (title fuzzy) risks false-positive merges |
| Per-source hard cap: 100 | `main.py` | Above this, most sources start paginating differently and rate limits actually matter |

If any of these needs to change, edit the code and add a test — this is
not a config knob.
