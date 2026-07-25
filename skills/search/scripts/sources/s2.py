"""Semantic Scholar Graph API adapter.

Endpoint: https://api.semanticscholar.org/graph/v1/paper/search
Auth: `x-api-key: <SCIFORGE_S2_API_KEY>` header (optional but strongly
recommended — anonymous is 1 req/s and often throttled).
"""

from __future__ import annotations

from typing import Any

from config import SearchConfig, build_url, http_get_json
from query_obj import QueryObject

SOURCE = "s2"
_BASE = "https://api.semanticscholar.org/graph/v1/paper/search"

# Fields to ask for — comma-separated on the wire.
_FIELDS = ",".join([
    "title", "authors", "year", "citationCount", "externalIds",
    "abstract", "venue", "openAccessPdf", "url", "publicationTypes",
])


def _compile_s2_query(q: QueryObject) -> str:
    """S2 has no boolean; just free text."""
    if q.mode == "keyword":
        return q.text or ""
    if q.mode == "raw":
        return q.text or ""
    if q.mode == "strategy" and q.per_source and "s2" in q.per_source:
        return q.per_source["s2"]
    if q.mode == "fields" and q.fields:
        parts: list[str] = []
        if q.fields.get("title"):
            parts.append(str(q.fields["title"]))
        for a in q.fields.get("authors") or []:
            parts.append(str(a))
        return " ".join(parts) if parts else q.free_text()
    return q.free_text()


def _parse_item(item: dict[str, Any]) -> dict[str, Any]:
    external_ids = item.get("externalIds") or {}
    doi = (external_ids.get("DOI") or "").lower() or None
    pmid = external_ids.get("PubMed") or None
    arxiv_id = external_ids.get("ArXiv") or None

    authors_raw = item.get("authors") or []
    authors = [a.get("name") for a in authors_raw if a.get("name")]

    return {
        "source": SOURCE,
        "doi": doi,
        "pmid": pmid,
        "arxiv_id": arxiv_id,
        "openalex_id": None,
        "s2_id": item.get("paperId"),
        "title": item.get("title"),
        "authors": authors,
        "year": item.get("year"),
        "journal": item.get("venue") or None,
        "volume": None,
        "issue": None,
        "pages": None,
        "abstract": item.get("abstract"),
        "citation_count": item.get("citationCount"),
        "type": (item.get("publicationTypes") or [None])[0],
        "url": item.get("url"),
    }


def search(
    q: QueryObject,
    *,
    limit: int,
    cfg: SearchConfig,
    respect_rate_limit: bool = False,
) -> list[dict[str, Any]]:
    query_str = _compile_s2_query(q)
    if not query_str.strip():
        return []

    params: dict[str, Any] = {
        "query": query_str,
        "limit": min(limit, 100),
        "fields": _FIELDS,
    }
    # S2 supports `year=2020-2024` or `year=2020`
    if q.year_from and q.year_to:
        params["year"] = f"{q.year_from}-{q.year_to}"
    elif q.year_from:
        params["year"] = f"{q.year_from}-"
    elif q.year_to:
        params["year"] = f"-{q.year_to}"

    url = build_url(_BASE, params)
    data = http_get_json(
        url,
        source=SOURCE,
        cfg=cfg,
        respect_rate_limit=respect_rate_limit,
    )

    items = data.get("data") or []
    out: list[dict[str, Any]] = []
    for rank, item in enumerate(items[:limit], start=1):
        rec = _parse_item(item)
        rec["rank"] = rank
        out.append(rec)
    return out


# The /paper/{id} endpoint accepts several id namespaces including ARXIV:
_LOOKUP_BASE = "https://api.semanticscholar.org/graph/v1/paper"


def lookup_by_arxiv(arxiv_id: str, *, cfg: SearchConfig,
                    respect_rate_limit: bool = False) -> dict[str, Any] | None:
    """Look up a paper by arXiv id via `/paper/ARXIV:<id>?fields=externalIds,...`.

    Returns a parsed record or None on any failure. Best-effort: never
    raises so the upgrade phase can't break a search.
    """
    if not arxiv_id or not arxiv_id.strip():
        return None
    url = build_url(
        f"{_LOOKUP_BASE}/ARXIV:{arxiv_id.strip()}",
        {"fields": _FIELDS},
    )
    try:
        item = http_get_json(
            url, source=SOURCE, cfg=cfg,
            respect_rate_limit=respect_rate_limit,
        )
    except Exception:  # noqa: BLE001 — upgrade is best-effort
        return None
    if not isinstance(item, dict) or not item.get("paperId"):
        return None
    return _parse_item(item)


__all__ = ["SOURCE", "search", "lookup_by_arxiv"]
