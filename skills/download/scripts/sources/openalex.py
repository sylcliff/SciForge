"""OpenAlex source — metadata + title search.

References/sources.md §OpenAlex is ground truth. Exposes:
  - fetch_by_doi(doi, client, polite_email)   → SourceResult
  - fetch_by_openalex_id(oaid, client, ...)   → SourceResult
  - title_search(title, client, ...)          → SourceResult

Title search populates `candidates` when the top-1 doesn't strictly
match; it populates `resolved_doi` / `resolved_arxiv_id` when it does.
"""

from __future__ import annotations

import re
from typing import Optional

import httpx

from output import Meta
from sources import SourceResult

API_ROOT = "https://api.openalex.org/works"


# --------------------------------------------------------------------------- #
# Public entry points
# --------------------------------------------------------------------------- #


async def fetch_by_doi(doi: str, client: httpx.AsyncClient, *, polite_email: str = "") -> SourceResult:
    """Look up one work by DOI. OpenAlex accepts the raw DOI in the path segment."""
    # OpenAlex specifically wants `https://doi.org/<doi>` for DOI lookups.
    return await _fetch_one(f"{API_ROOT}/https://doi.org/{doi}", client, polite_email)


async def fetch_by_openalex_id(oaid: str, client: httpx.AsyncClient, *, polite_email: str = "") -> SourceResult:
    return await _fetch_one(f"{API_ROOT}/{oaid}", client, polite_email)


async def title_search(title: str, client: httpx.AsyncClient, *, polite_email: str = "") -> SourceResult:
    """Title search. Populates `resolved_doi`/`resolved_arxiv_id` on strict
    top-1 match, else `candidates` with up to 3 alternatives.
    """
    result = SourceResult(source_name="openalex")
    params: dict[str, str] = {"search": title, "per_page": "5"}
    if polite_email:
        params["mailto"] = polite_email

    try:
        r = await client.get(API_ROOT, params=params)
    except httpx.TimeoutException:
        result.transport_error = "timeout"
        return result
    except httpx.RequestError:
        result.transport_error = "network"
        return result

    result.raw_status_code = r.status_code
    if r.status_code == 429:
        result.rate_limited = True
        return result
    if r.status_code >= 500:
        result.transport_error = f"http_{r.status_code}"
        return result
    if r.status_code >= 400:
        result.not_found = True
        return result

    try:
        data = r.json()
    except ValueError:
        result.transport_error = "parse_error"
        return result

    results = data.get("results") if isinstance(data, dict) else None
    if not isinstance(results, list) or not results:
        result.not_found = True
        return result

    top = results[0]
    top_title = top.get("title") if isinstance(top, dict) else None

    if isinstance(top_title, str) and _normalize_title(top_title) == _normalize_title(title):
        # Confirmed strict match. Resolve to whatever identifier we can.
        doi_val = top.get("doi")
        if isinstance(doi_val, str) and doi_val:
            result.resolved_doi = _strip_doi_url(doi_val).lower()
        arxiv_val = _extract_arxiv_from_openalex_work(top)
        if arxiv_val:
            result.resolved_arxiv_id = arxiv_val
        result.meta = _parse_work(top)
        return result

    # No confident match — surface candidates.
    result.candidates = [_candidate_from(w) for w in results[:3] if isinstance(w, dict)]
    return result


# --------------------------------------------------------------------------- #
# Shared single-work fetch
# --------------------------------------------------------------------------- #


async def _fetch_one(url: str, client: httpx.AsyncClient, polite_email: str) -> SourceResult:
    result = SourceResult(source_name="openalex")
    params: dict[str, str] = {}
    if polite_email:
        params["mailto"] = polite_email
    try:
        r = await client.get(url, params=params)
    except httpx.TimeoutException:
        result.transport_error = "timeout"
        return result
    except httpx.RequestError:
        result.transport_error = "network"
        return result

    result.raw_status_code = r.status_code
    if r.status_code == 429:
        result.rate_limited = True
        return result
    if r.status_code >= 500:
        result.transport_error = f"http_{r.status_code}"
        return result
    if r.status_code == 404:
        result.not_found = True
        return result
    if r.status_code >= 400:
        return result

    try:
        data = r.json()
    except ValueError:
        result.transport_error = "parse_error"
        return result

    if not isinstance(data, dict):
        result.not_found = True
        return result

    result.meta = _parse_work(data)
    result.pdf_url_hint = _extract_pdf_url(data)
    return result


# --------------------------------------------------------------------------- #
# Field extraction
# --------------------------------------------------------------------------- #


def _parse_work(w: dict) -> Optional[Meta]:
    title = w.get("title") or w.get("display_name")
    if not isinstance(title, str) or not title:
        return None

    authors = []
    for a in w.get("authorships") or []:
        if isinstance(a, dict):
            au = a.get("author")
            if isinstance(au, dict) and isinstance(au.get("display_name"), str):
                authors.append(au["display_name"])

    abstract = _reconstruct_abstract(w.get("abstract_inverted_index"))

    year = w.get("publication_year") if isinstance(w.get("publication_year"), int) else None

    # OpenAlex v2 uses primary_location.source.display_name; v1 used host_venue.
    venue_full = None
    pl = w.get("primary_location")
    if isinstance(pl, dict) and isinstance(pl.get("source"), dict):
        n = pl["source"].get("display_name")
        if isinstance(n, str):
            venue_full = n
    if not venue_full:
        hv = w.get("host_venue")
        if isinstance(hv, dict) and isinstance(hv.get("display_name"), str):
            venue_full = hv["display_name"]

    doi_val = w.get("doi")
    doi = _strip_doi_url(doi_val).lower() if isinstance(doi_val, str) and doi_val else None

    arxiv_id = _extract_arxiv_from_openalex_work(w)

    url = None
    if isinstance(pl, dict) and isinstance(pl.get("landing_page_url"), str):
        url = pl["landing_page_url"]

    return Meta(
        title=title,
        authors=authors or None,
        abstract=abstract,
        year=year,
        venue_full=venue_full,
        doi=doi,
        arxiv_id=arxiv_id,
        url=url,
    )


def _extract_pdf_url(w: dict) -> Optional[str]:
    pl = w.get("primary_location")
    if isinstance(pl, dict) and isinstance(pl.get("pdf_url"), str):
        return pl["pdf_url"]
    # Fall back to best_oa_location
    boa = w.get("best_oa_location")
    if isinstance(boa, dict) and isinstance(boa.get("pdf_url"), str):
        return boa["pdf_url"]
    return None


def _candidate_from(w: dict) -> dict:
    title = w.get("title") or w.get("display_name") or ""
    doi_val = w.get("doi")
    doi = _strip_doi_url(doi_val).lower() if isinstance(doi_val, str) and doi_val else None
    year = w.get("publication_year") if isinstance(w.get("publication_year"), int) else None
    first_author = None
    aus = w.get("authorships")
    if isinstance(aus, list) and aus:
        au = aus[0].get("author") if isinstance(aus[0], dict) else None
        if isinstance(au, dict) and isinstance(au.get("display_name"), str):
            first_author = au["display_name"]
    return {"title": title, "doi": doi, "year": year, "first_author": first_author}


def _reconstruct_abstract(inv_idx) -> Optional[str]:
    """OpenAlex stores abstracts as {word: [positions]}; reconstruct plain text."""
    if not isinstance(inv_idx, dict) or not inv_idx:
        return None
    positioned: list[tuple[int, str]] = []
    for word, positions in inv_idx.items():
        if not isinstance(positions, list):
            continue
        for p in positions:
            if isinstance(p, int):
                positioned.append((p, word))
    if not positioned:
        return None
    positioned.sort(key=lambda t: t[0])
    text = " ".join(w for _, w in positioned)
    if len(text) < 40:
        return None
    return text


def _strip_doi_url(v: str) -> str:
    return re.sub(r"^https?://(dx\.)?doi\.org/", "", v, flags=re.IGNORECASE)


def _extract_arxiv_from_openalex_work(w: dict) -> Optional[str]:
    """Some OpenAlex records expose arXiv IDs via ids or landing URLs."""
    ids = w.get("ids") if isinstance(w.get("ids"), dict) else {}
    doi_val = ids.get("doi") if isinstance(ids, dict) else None
    if isinstance(doi_val, str) and "arxiv." in doi_val.lower():
        # DOI of form 10.48550/arxiv.NNNN.NNNNN
        m = re.search(r"arxiv\.([0-9\.]+)", doi_val, re.IGNORECASE)
        if m:
            return m.group(1)
    # landing_page_url can carry arxiv.org/abs/…
    pl = w.get("primary_location") or {}
    if isinstance(pl, dict):
        url = pl.get("landing_page_url") or ""
        if isinstance(url, str):
            m = re.search(r"arxiv\.org/(?:abs|pdf)/([0-9]{4}\.[0-9]{4,5})", url)
            if m:
                return m.group(1)
    return None


def _normalize_title(t: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace. For strict title match."""
    lowered = t.lower()
    stripped = re.sub(r"[^\w\s]", " ", lowered, flags=re.UNICODE)
    return re.sub(r"\s+", " ", stripped).strip()


__all__ = ["fetch_by_doi", "fetch_by_openalex_id", "title_search", "API_ROOT"]
