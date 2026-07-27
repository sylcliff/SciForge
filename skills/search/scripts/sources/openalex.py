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


__all__ = ["SOURCE", "search", "lookup_by_arxiv",
           "get_work", "get_works_batch", "get_referenced_works",
           "get_cited_by"]


# --------------------------------------------------------------------------- #
# Citation-graph helpers
# --------------------------------------------------------------------------- #


def get_work(id_str: str, *, cfg: SearchConfig,
             respect_rate_limit: bool = False) -> dict[str, Any] | None:
    """Fetch one OpenAlex work by DOI or OpenAlex work id.

    Accepted `id_str` shapes:
      - `W\\d+`               — OpenAlex work id
      - `10.…/…`              — DOI (any case; leading `https://doi.org/`
                                stripped by caller if present)

    Returns a source-format record (same shape `_parse_item` emits) or
    None on 404 / network failure. Best-effort — never raises.
    """
    if not id_str or not id_str.strip():
        return None
    ident = id_str.strip()
    if ident.upper().startswith("W") and ident[1:].isdigit():
        openalex_path = f"W{ident[1:]}"
    else:
        # DOI — OpenAlex accepts `works/doi:<encoded>` and `works/https://doi.org/<doi>`;
        # the safest form is `works/https://doi.org/<doi>` unencoded.
        openalex_path = f"https://doi.org/{ident.lower()}"

    params: dict[str, Any] = {}
    if cfg.polite_email:
        params["mailto"] = cfg.polite_email
    url = f"{_BASE}/{openalex_path}"
    if params:
        url = build_url(url, params)
    try:
        data = http_get_json(
            url, source=SOURCE, cfg=cfg,
            respect_rate_limit=respect_rate_limit,
        )
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(data, dict) or not data.get("id"):
        return None
    return _parse_item(data)


def get_works_batch(ids: list[str], *, cfg: SearchConfig,
                    respect_rate_limit: bool = False,
                    chunk_size: int = 50) -> list[dict[str, Any]]:
    """Batch-fetch OpenAlex works by W-ids.

    Uses `works?filter=openalex_id:W1|W2|…&per-page=50`. The filter
    accepts a pipe-separated list; OpenAlex caps `per-page` at 200 but
    the filter itself is capped to 50 values per call (documented on
    the OpenAlex docs; empirically it silently truncates beyond ~50).

    Chunks the input `ids` into `chunk_size` groups, returns a flat list
    of parsed records. Best-effort — skips chunks that fail.
    """
    if not ids:
        return []
    # Dedup while preserving order
    seen: set[str] = set()
    uniq: list[str] = []
    for i in ids:
        if not i:
            continue
        i = i.strip()
        # Canonicalize to bare W-id
        m = _OPENALEX_ID_RE.search(i)
        if not m:
            continue
        wid = m.group(1)
        if wid in seen:
            continue
        seen.add(wid)
        uniq.append(wid)

    out: list[dict[str, Any]] = []
    for i in range(0, len(uniq), chunk_size):
        chunk = uniq[i : i + chunk_size]
        params: dict[str, Any] = {
            "filter": f"openalex_id:{'|'.join(chunk)}",
            "per-page": len(chunk),
        }
        if cfg.polite_email:
            params["mailto"] = cfg.polite_email
        url = build_url(_BASE, params)
        try:
            data = http_get_json(
                url, source=SOURCE, cfg=cfg,
                respect_rate_limit=respect_rate_limit,
            )
        except Exception:  # noqa: BLE001
            continue
        if not isinstance(data, dict):
            continue
        for item in (data.get("results") or []):
            if isinstance(item, dict):
                out.append(_parse_item(item))
    return out


def get_referenced_works(work_id_or_doi: str, *, cfg: SearchConfig,
                         respect_rate_limit: bool = False) -> list[dict[str, Any]]:
    """Fetch outgoing references cited by `work_id_or_doi`.

    Two-step:
      1. Fetch the seed work → read `referenced_works[]` (a list of
         OpenAlex work URLs).
      2. Batch-fetch the referenced works via `get_works_batch`.

    The seed's own record is NOT included in the return. Empty list when
    the seed is not found or has no references. Best-effort — never
    raises.
    """
    if not work_id_or_doi or not work_id_or_doi.strip():
        return []
    ident = work_id_or_doi.strip()
    if ident.upper().startswith("W") and ident[1:].isdigit():
        openalex_path = f"W{ident[1:]}"
    else:
        openalex_path = f"https://doi.org/{ident.lower()}"

    params: dict[str, Any] = {}
    if cfg.polite_email:
        params["mailto"] = cfg.polite_email
    url = f"{_BASE}/{openalex_path}"
    if params:
        url = build_url(url, params)
    try:
        data = http_get_json(
            url, source=SOURCE, cfg=cfg,
            respect_rate_limit=respect_rate_limit,
        )
    except Exception:  # noqa: BLE001
        return []
    if not isinstance(data, dict):
        return []
    ref_urls = data.get("referenced_works") or []
    if not isinstance(ref_urls, list) or not ref_urls:
        return []
    return get_works_batch(ref_urls, cfg=cfg, respect_rate_limit=respect_rate_limit)


def get_cited_by(work_id_or_doi: str, *, cfg: SearchConfig,
                 respect_rate_limit: bool = False,
                 limit: int | None = None) -> list[dict[str, Any]]:
    """Fetch works that cite `work_id_or_doi`.

    Uses `works?filter=cites:<W-id>` with cursor pagination. When `limit`
    is set, stops as soon as `limit` records are collected (the last page
    may over-fetch by up to `per-page` records). When `limit` is None,
    pages to exhaustion.

    Resolves DOI→W-id first via `get_work` if the seed is a DOI. Empty
    list on failure.
    """
    if not work_id_or_doi or not work_id_or_doi.strip():
        return []
    ident = work_id_or_doi.strip()
    if ident.upper().startswith("W") and ident[1:].isdigit():
        wid = f"W{ident[1:]}"
    else:
        seed = get_work(ident, cfg=cfg, respect_rate_limit=respect_rate_limit)
        if not seed or not seed.get("openalex_id"):
            return []
        wid = seed["openalex_id"]

    out: list[dict[str, Any]] = []
    cursor: str = "*"
    per_page = 200
    while True:
        params: dict[str, Any] = {
            "filter": f"cites:{wid}",
            "per-page": per_page,
            "cursor": cursor,
        }
        if cfg.polite_email:
            params["mailto"] = cfg.polite_email
        url = build_url(_BASE, params)
        try:
            data = http_get_json(
                url, source=SOURCE, cfg=cfg,
                respect_rate_limit=respect_rate_limit,
            )
        except Exception:  # noqa: BLE001
            break
        if not isinstance(data, dict):
            break
        items = data.get("results") or []
        for item in items:
            if isinstance(item, dict):
                out.append(_parse_item(item))
            if limit is not None and len(out) >= limit:
                return out
        next_cursor = ((data.get("meta") or {}).get("next_cursor"))
        if not next_cursor or not items:
            break
        cursor = next_cursor
    return out
