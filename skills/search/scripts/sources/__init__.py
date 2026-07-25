"""Registry of source adapters, keyed by source name.

Each adapter module exposes:
- SOURCE: str (its canonical name)
- search(q, *, limit, cfg, respect_rate_limit) -> list[dict]

Adapters are imported here so `sources.ALL_SOURCES` gives one-stop
access; `main.py` picks which to dispatch based on --sources.
"""

from __future__ import annotations

from sources import arxiv, crossref, openalex, pubmed, s2

ALL_SOURCES: dict[str, object] = {
    pubmed.SOURCE: pubmed,
    crossref.SOURCE: crossref,
    arxiv.SOURCE: arxiv,
    openalex.SOURCE: openalex,
    s2.SOURCE: s2,
}

DEFAULT_ORDER: list[str] = ["pubmed", "crossref", "arxiv", "openalex", "s2"]
