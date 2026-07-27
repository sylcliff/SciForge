"""Crossref REST adapter.

One-shot: `/works?query=<free text>&rows=<limit>` for keyword mode;
field-qualified params for `--fields` mode. Optional `mailto=` for
polite pool.
"""

from __future__ import annotations

import re
from typing import Any

from config import SearchConfig, build_url, http_get_json
from query_obj import QueryObject

SOURCE = "crossref"
_BASE = "https://api.crossref.org/works"

# arxiv DOI prefix used by Crossref/DataCite: `10.48550/arxiv.<id>` (any case).
_ARXIV_DOI_RE = re.compile(
    r"^10\.48550/arxiv\.(?:([a-z\-]+/\d{7})|(\d{4}\.\d{4,5}))(?:v\d+)?$",
    re.IGNORECASE,
)


def _extract_arxiv_id_from_relation(item: dict[str, Any]) -> str | None:
    """Look in Crossref's `relation.has-preprint` / `is-preprint-of` for an
    arXiv DOI (10.48550/arxiv.*) and return the bare arxiv id."""
    relation = item.get("relation") or {}
    if not isinstance(relation, dict):
        return None
    for key in ("has-preprint", "is-preprint-of"):
        entries = relation.get(key) or []
        for e in entries:
            if not isinstance(e, dict):
                continue
            id_type = (e.get("id-type") or "").lower()
            id_val = (e.get("id") or "").strip()
            if id_type == "doi" and id_val:
                m = _ARXIV_DOI_RE.match(id_val)
                if m:
                    return (m.group(1) or m.group(2))
    return None


def _build_params(q: QueryObject, limit: int, polite_email: str) -> dict[str, Any]:
    params: dict[str, Any] = {"rows": limit}
    if polite_email:
        params["mailto"] = polite_email

    if q.mode == "keyword":
        params["query"] = q.text or ""
    elif q.mode == "raw":
        # Crossref treats it as free text
        params["query"] = q.text or ""
    elif q.mode == "strategy" and q.per_source and "crossref" in q.per_source:
        params["query"] = q.per_source["crossref"]
    elif q.mode == "fields" and q.fields:
        if q.fields.get("title"):
            params["query.title"] = q.fields["title"]
        authors = q.fields.get("authors") or []
        if authors:
            # Crossref supports one query.author; use first, others fall to free text
            params["query.author"] = authors[0]
            if len(authors) > 1:
                # extra authors go into the general query for OR
                params["query"] = " ".join(authors[1:])
        if q.fields.get("journal"):
            params["query.container-title"] = q.fields["journal"]
    else:
        params["query"] = q.free_text()

    # Year filter
    filters: list[str] = []
    if q.year_from:
        filters.append(f"from-pub-date:{q.year_from}-01-01")
    if q.year_to:
        filters.append(f"until-pub-date:{q.year_to}-12-31")
    if filters:
        params["filter"] = ",".join(filters)

    return params


def _parse_item(item: dict[str, Any]) -> dict[str, Any]:
    doi = item.get("DOI")
    doi_lower = doi.lower() if isinstance(doi, str) else None

    titles = item.get("title") or []
    title = titles[0] if titles else None

    authors: list[str] = []
    for a in item.get("author", []) or []:
        given = a.get("given", "") or ""
        family = a.get("family", "") or ""
        name = f"{given} {family}".strip()
        if not name and a.get("name"):
            name = a["name"]
        if name:
            authors.append(name)

    year: int | None = None
    for key in ("published-print", "published-online", "issued", "created"):
        parts = (item.get(key) or {}).get("date-parts") or []
        if parts and parts[0]:
            try:
                year = int(parts[0][0])
                break
            except (TypeError, ValueError):
                pass

    container_titles = item.get("container-title") or []
    journal = container_titles[0] if container_titles else None

    abstract = item.get("abstract")
    # Crossref abstracts sometimes include JATS tags — strip crude
    if abstract and isinstance(abstract, str):
        abstract = re.sub(r"<[^>]+>", "", abstract).strip() or None

    return {
        "source": SOURCE,
        "doi": doi_lower,
        "pmid": None,
        "arxiv_id": _extract_arxiv_id_from_relation(item),
        "openalex_id": None,
        "s2_id": None,
        "title": title,
        "authors": authors,
        "year": year,
        "journal": journal,
        "volume": item.get("volume"),
        "issue": item.get("issue"),
        "pages": item.get("page"),
        "abstract": abstract,
        "citation_count": item.get("is-referenced-by-count"),
        "type": item.get("type"),
        "url": item.get("URL") or (f"https://doi.org/{doi_lower}" if doi_lower else None),
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

    items = (data.get("message") or {}).get("items") or []
    out: list[dict[str, Any]] = []
    for rank, item in enumerate(items[:limit], start=1):
        rec = _parse_item(item)
        rec["rank"] = rank
        out.append(rec)
    return out


def get_by_doi(doi: str, *, cfg: SearchConfig,
               respect_rate_limit: bool = False) -> dict[str, Any] | None:
    """Fetch a single record by DOI via /works/{doi}. Best-effort — never
    raises. Used by arxiv_upgrade for post-hoc verification.

    Returns a parsed record dict, or None on any failure / not-found.
    """
    if not doi or not doi.strip():
        return None
    import urllib.parse
    encoded = urllib.parse.quote(doi.strip(), safe="/")
    params: dict[str, Any] = {}
    if cfg.polite_email:
        params["mailto"] = cfg.polite_email
    url = f"{_BASE}/{encoded}"
    if params:
        url = build_url(url, params)
    try:
        data = http_get_json(
            url, source=SOURCE, cfg=cfg,
            respect_rate_limit=respect_rate_limit,
        )
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(data, dict):
        return None
    msg = data.get("message")
    if not isinstance(msg, dict):
        return None
    return _parse_item(msg)


def get_references(doi: str, *, cfg: SearchConfig,
                   respect_rate_limit: bool = False) -> list[dict[str, Any]]:
    """Fetch outgoing references cited by the paper at `doi`.

    Reads Crossref's `works/{doi}.reference[]` array. Each reference entry
    is heterogeneous: some carry a `DOI`, some only unstructured text.

    Returns a list of source-format records (same shape `_parse_item`
    emits, so β-dedup and merge work uniformly). Each record has:
      - `source` = "crossref"
      - `doi`    = the reference's DOI, lowercased, else None
      - `title`  = `article-title` if present
      - `authors`= parsed from `author` string ("Smith J") when present
      - `year`   = `year` int if present
      - `journal`= `journal-title` if present
      - `raw_citation` = `unstructured` string (only when no DOI extracted)

    Empty list if the DOI is not in Crossref or its record has no
    reference[] array. Best-effort — never raises.
    """
    if not doi or not doi.strip():
        return []
    import urllib.parse
    encoded = urllib.parse.quote(doi.strip(), safe="/")
    params: dict[str, Any] = {}
    if cfg.polite_email:
        params["mailto"] = cfg.polite_email
    url = f"{_BASE}/{encoded}"
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
    msg = data.get("message") or {}
    if not isinstance(msg, dict):
        return []
    refs = msg.get("reference") or []
    if not isinstance(refs, list):
        return []

    out: list[dict[str, Any]] = []
    for rank, entry in enumerate(refs, start=1):
        if not isinstance(entry, dict):
            continue
        rec = _parse_reference_entry(entry)
        if rec is None:
            continue
        rec["rank"] = rank
        out.append(rec)
    return out


def _parse_reference_entry(entry: dict[str, Any]) -> dict[str, Any] | None:
    """Parse one Crossref reference[] entry into a source-format record.

    Crossref reference entries are ad hoc — a `DOI` may be present, or a
    structured `article-title`/`author`/`year`/`journal-title`/`volume`,
    or just a `unstructured` string, or (rarely) nothing useful. We keep
    whatever we can extract.

    Returns None if the entry has no usable content at all (extremely
    rare, but be defensive)."""
    doi_raw = entry.get("DOI")
    doi = doi_raw.strip().lower() if isinstance(doi_raw, str) and doi_raw.strip() else None

    # Reject arxiv self-DOIs — capture as arxiv_id instead.
    arxiv_id: str | None = None
    if doi and _ARXIV_DOI_RE.match(doi):
        m = _ARXIV_DOI_RE.match(doi)
        if m:
            arxiv_id = (m.group(1) or m.group(2))
        doi = None  # scrub — this is not a journal DOI

    title = entry.get("article-title") or None

    authors: list[str] = []
    au = entry.get("author")
    if isinstance(au, str) and au.strip():
        # Crossref reference authors are a single string, sometimes
        # "Smith J", sometimes "Smith J, Doe A". We keep it as one line
        # rather than trying to guess split rules.
        authors = [au.strip()]

    year: int | None = None
    y_raw = entry.get("year")
    if y_raw is not None:
        try:
            year = int(str(y_raw)[:4])
        except (TypeError, ValueError):
            year = None

    journal = entry.get("journal-title") or None
    volume = entry.get("volume") or None
    pages = entry.get("first-page") or None

    unstructured = entry.get("unstructured") or None
    raw_citation = unstructured if isinstance(unstructured, str) else None

    has_content = any([doi, arxiv_id, title, authors, year, journal, raw_citation])
    if not has_content:
        return None

    return {
        "source": SOURCE,
        "doi": doi,
        "pmid": None,
        "arxiv_id": arxiv_id,
        "openalex_id": None,
        "s2_id": None,
        "title": title,
        "authors": authors,
        "year": year,
        "journal": journal,
        "volume": volume,
        "issue": None,
        "pages": pages,
        "abstract": None,
        "citation_count": None,
        "type": None,
        "url": (f"https://doi.org/{doi}" if doi else None),
        # Only present when no identifier was extracted — the orchestrator
        # keys off `raw_citation` to split resolved vs unresolved records.
        "raw_citation": raw_citation if (not doi and not arxiv_id) else None,
    }


def lookup_by_title(title: str, *, cfg: SearchConfig,
                    respect_rate_limit: bool = False,
                    rows: int = 3) -> list[dict[str, Any]]:
    """Query Crossref by title, return up to `rows` candidate records.

    Used by arxiv_upgrade as a third-tier fallback when OpenAlex + S2
    can't find a published-version DOI. Caller is responsible for
    verifying the returned records actually match the arxiv paper
    (see _title_matches / arxiv_upgrade._is_title_match).

    Best-effort: never raises; returns [] on any failure.
    """
    if not title or not title.strip():
        return []
    params: dict[str, Any] = {
        "query.title": title.strip(),
        "rows": rows,
    }
    if cfg.polite_email:
        params["mailto"] = cfg.polite_email
    url = build_url(_BASE, params)
    try:
        data = http_get_json(
            url, source=SOURCE, cfg=cfg,
            respect_rate_limit=respect_rate_limit,
        )
    except Exception:  # noqa: BLE001 — upgrade is best-effort
        return []
    items = (data.get("message") or {}).get("items") or []
    return [_parse_item(item) for item in items[:rows]]


__all__ = ["SOURCE", "search", "lookup_by_title", "get_by_doi", "get_references"]
