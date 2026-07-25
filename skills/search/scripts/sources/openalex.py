"""OpenAlex REST adapter.

Endpoint: https://api.openalex.org/works
Free-text: ?search=<terms>
Field-qualified: ?filter=title.search:<t>,author.display_name.search:<a>,publication_year:2020-2024
Polite pool: mailto=<email>
"""

from __future__ import annotations

import re
from typing import Any

from config import SearchConfig, build_url, http_get_json
from query_obj import QueryObject

SOURCE = "openalex"
_BASE = "https://api.openalex.org/works"


# https://openalex.org/W2741809807  →  W2741809807
_OPENALEX_ID_RE = re.compile(r"(W\d+)$")

# arXiv id from URLs like https://arxiv.org/abs/1706.03762 or /abs/hep-th/9901001
_ARXIV_URL_RE = re.compile(
    r"arxiv\.org/abs/(?:([a-z\-]+/\d{7})|(\d{4}\.\d{4,5}))(?:v\d+)?",
    re.IGNORECASE,
)


def _extract_openalex_id(url_or_id: str | None) -> str | None:
    if not url_or_id:
        return None
    m = _OPENALEX_ID_RE.search(url_or_id)
    return m.group(1) if m else None


def _extract_arxiv_id_from_locations(item: dict[str, Any]) -> str | None:
    """Scan locations[*].landing_page_url + primary_location for an arxiv URL."""
    candidates: list[str] = []
    primary = item.get("primary_location") or {}
    if isinstance(primary, dict) and primary.get("landing_page_url"):
        candidates.append(primary["landing_page_url"])
    for loc in item.get("locations") or []:
        if not isinstance(loc, dict):
            continue
        u = loc.get("landing_page_url") or loc.get("pdf_url")
        if u:
            candidates.append(u)
    # Also check open_access.oa_url — sometimes points at arxiv
    oa = (item.get("open_access") or {}).get("oa_url")
    if oa:
        candidates.append(oa)

    for u in candidates:
        m = _ARXIV_URL_RE.search(u)
        if m:
            return (m.group(1) or m.group(2))
    return None


def _build_params(q: QueryObject, limit: int, polite_email: str) -> dict[str, Any]:
    params: dict[str, Any] = {"per-page": min(limit, 200)}
    if polite_email:
        params["mailto"] = polite_email

    filters: list[str] = []

    if q.mode == "keyword":
        params["search"] = q.text or ""
    elif q.mode == "raw":
        params["search"] = q.text or ""
    elif q.mode == "strategy" and q.per_source and "openalex" in q.per_source:
        params["search"] = q.per_source["openalex"]
    elif q.mode == "fields" and q.fields:
        if q.fields.get("title"):
            filters.append(f"title.search:{q.fields['title']}")
        for a in q.fields.get("authors") or []:
            filters.append(f"author.display_name.search:{a}")
        if q.fields.get("journal"):
            filters.append(f"primary_location.source.display_name.search:{q.fields['journal']}")
    else:
        params["search"] = q.free_text()

    if q.year_from and q.year_to:
        filters.append(f"publication_year:{q.year_from}-{q.year_to}")
    elif q.year_from:
        filters.append(f"from_publication_date:{q.year_from}-01-01")
    elif q.year_to:
        filters.append(f"to_publication_date:{q.year_to}-12-31")

    if filters:
        params["filter"] = ",".join(filters)

    return params


def _parse_item(item: dict[str, Any]) -> dict[str, Any]:
    doi_url = item.get("doi") or ""
    doi = doi_url.replace("https://doi.org/", "").lower() if doi_url else None

    authors: list[str] = []
    for a in item.get("authorships", []) or []:
        name = ((a.get("author") or {}).get("display_name")) or ""
        if name:
            authors.append(name)

    # OpenAlex abstract is an inverted index
    abstract: str | None = None
    inv = item.get("abstract_inverted_index")
    if isinstance(inv, dict) and inv:
        positions: list[tuple[int, str]] = []
        for word, ps in inv.items():
            for p in ps:
                positions.append((p, word))
        positions.sort()
        abstract = " ".join(w for _, w in positions)

    primary_location = item.get("primary_location") or {}
    source_info = primary_location.get("source") or {}
    journal = source_info.get("display_name")

    biblio = item.get("biblio") or {}

    ids = item.get("ids") or {}
    pmid_url = ids.get("pmid") or ""
    pmid_match = re.search(r"/(\d+)$", pmid_url) if pmid_url else None
    pmid = pmid_match.group(1) if pmid_match else None

    # PMCID — same paper in PubMed Central. Strong dedup signal.
    # Format: "https://www.ncbi.nlm.nih.gov/pmc/articles/PMC1234567"
    pmcid_url = ids.get("pmcid") or ""
    pmcid_match = re.search(r"(PMC\d+)", pmcid_url) if pmcid_url else None
    pmcid = pmcid_match.group(1) if pmcid_match else None

    return {
        "source": SOURCE,
        "doi": doi,
        "pmid": pmid,
        "pmcid": pmcid,
        "arxiv_id": _extract_arxiv_id_from_locations(item),
        "openalex_id": _extract_openalex_id(item.get("id")),
        "s2_id": None,
        "title": item.get("title") or item.get("display_name"),
        "authors": authors,
        "year": item.get("publication_year"),
        "journal": journal,
        "volume": biblio.get("volume"),
        "issue": biblio.get("issue"),
        "pages": (
            f"{biblio.get('first_page', '')}-{biblio.get('last_page', '')}".strip("-")
            if biblio.get("first_page") or biblio.get("last_page")
            else None
        ),
        "abstract": abstract,
        "citation_count": item.get("cited_by_count"),
        "type": item.get("type"),
        "is_oa": (item.get("open_access") or {}).get("is_oa"),
        "url": primary_location.get("landing_page_url") or (f"https://doi.org/{doi}" if doi else None),
    }


def search(
    q: QueryObject,
    *,
    limit: int,
    cfg: SearchConfig,
    respect_rate_limit: bool = False,
) -> list[dict[str, Any]]:
    params = _build_params(q, limit, cfg.polite_email)
    url = build_url(_BASE, params)
    data = http_get_json(
        url,
        source=SOURCE,
        cfg=cfg,
        respect_rate_limit=respect_rate_limit,
    )

    items = data.get("results") or []
    out: list[dict[str, Any]] = []
    for rank, item in enumerate(items[:limit], start=1):
        rec = _parse_item(item)
        rec["rank"] = rank
        out.append(rec)
    return out


def lookup_by_arxiv(arxiv_id: str, *, cfg: SearchConfig,
                    respect_rate_limit: bool = False) -> dict[str, Any] | None:
    """Look up a paper by its arXiv id, preferring the journal-article version.

    OpenAlex assigns TWO separate work IDs to a preprint-then-published paper:
    one for the arxiv preprint (DataCite DOI 10.48550/arxiv.*), one for the
    journal article. Filtering by the DataCite DOI returns the preprint,
    which is useless for upgrade — we want the journal version.

    Strategy:
      1. Filter by DataCite DOI to get the preprint's title.
      2. Search by that title, filter `type:article` to prefer journal
         versions; also allow book-chapter/proceedings-article.
      3. Return the first non-preprint article whose title matches the
         preprint's title (Jaccard ≥ 0.85) OR contains it as substring.

    Never raises — returns None on any failure so upgrade stays best-effort.
    """
    if not arxiv_id or not arxiv_id.strip():
        return None
    aid = arxiv_id.strip()

    # Step 1: fetch the preprint record itself to get title.
    params: dict[str, Any] = {"filter": f"doi:10.48550/arxiv.{aid}"}
    if cfg.polite_email:
        params["mailto"] = cfg.polite_email
    url = build_url(_BASE, params)
    try:
        data = http_get_json(
            url, source=SOURCE, cfg=cfg,
            respect_rate_limit=respect_rate_limit,
        )
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(data, dict):
        return None
    preprint_results = data.get("results") or []
    if not preprint_results:
        return None
    preprint_item = preprint_results[0]
    if not isinstance(preprint_item, dict):
        return None
    preprint_title = preprint_item.get("title") or preprint_item.get("display_name")
    if not preprint_title:
        # Can't do title-based hop — return the preprint record as best-effort.
        return _parse_item(preprint_item)

    # Step 2: search by title, filter type:article to prefer journal
    # versions. `types` filter accepts article, book-chapter, dataset, etc.
    search_params: dict[str, Any] = {
        "search": preprint_title[:200],  # trim to keep URL reasonable
        "filter": "type:article|book-chapter|proceedings-article",
        "per-page": 5,
    }
    if cfg.polite_email:
        search_params["mailto"] = cfg.polite_email
    search_url = build_url(_BASE, search_params)
    try:
        search_data = http_get_json(
            search_url, source=SOURCE, cfg=cfg,
            respect_rate_limit=respect_rate_limit,
        )
    except Exception:  # noqa: BLE001
        # Search hop failed — fall back to preprint parse.
        return _parse_item(preprint_item)
    if not isinstance(search_data, dict):
        return _parse_item(preprint_item)

    # Step 3: pick the first search hit whose title matches the preprint.
    import re as _re
    def _norm(s: str) -> str:
        s = (s or "").lower()
        s = _re.sub(r"[^a-z0-9\s]", " ", s)
        return " ".join(s.split())

    pt_norm = _norm(preprint_title)
    pt_tokens = set(pt_norm.split())

    for w in search_data.get("results", []):
        if not isinstance(w, dict):
            continue
        title = w.get("title") or w.get("display_name") or ""
        n = _norm(title)
        if not n:
            continue
        # Match: exact after normalization OR high Jaccard
        if n == pt_norm:
            return _parse_item(w)
        tokens = set(n.split())
        if pt_tokens and tokens:
            jaccard = len(pt_tokens & tokens) / len(pt_tokens | tokens)
            if jaccard >= 0.85:
                return _parse_item(w)

    # No non-preprint title match found; return preprint as best-effort.
    return _parse_item(preprint_item)


__all__ = ["SOURCE", "search", "lookup_by_arxiv"]
