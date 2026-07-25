"""Semantic Scholar source — metadata + openAccessPdf.

References/sources.md §Semantic Scholar. Optional API key raises the
rate limit; anonymous is fine but 429s more often.

Public API: fetch(id_string, client, api_key) where id_string is one of
  - "DOI:10.xxxx/..."     for DOI lookups
  - "ArXiv:1706.03762"    for arXiv lookups
  - <40-hex>              raw S2 paper ID
"""

from __future__ import annotations

from typing import Optional

import httpx

from output import Meta
from sources import SourceResult

API_ROOT = "https://api.semanticscholar.org/graph/v1/paper"

# Requested fields. Compact list — everything we actually consume.
_FIELDS = ",".join([
    "title",
    "authors.name",
    "abstract",
    "year",
    "venue",
    "publicationVenue.name",
    "externalIds",
    "openAccessPdf",
    "url",
    "paperId",
    "tldr",
])


async def fetch(id_string: str, client: httpx.AsyncClient, *, api_key: str = "") -> SourceResult:
    result = SourceResult(source_name="semanticscholar")

    url = f"{API_ROOT}/{id_string}"
    headers: dict[str, str] = {}
    if api_key:
        headers["x-api-key"] = api_key

    try:
        r = await client.get(url, params={"fields": _FIELDS}, headers=headers)
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

    result.meta = _parse(data)
    oa = data.get("openAccessPdf")
    if isinstance(oa, dict) and isinstance(oa.get("url"), str) and oa["url"]:
        result.pdf_url_hint = oa["url"]
    return result


def _parse(data: dict) -> Optional[Meta]:
    title = data.get("title")
    if not isinstance(title, str) or not title:
        return None

    authors_raw = data.get("authors") or []
    authors = [a["name"] for a in authors_raw if isinstance(a, dict) and isinstance(a.get("name"), str)]

    abstract = data.get("abstract") if isinstance(data.get("abstract"), str) else None
    if not abstract or len(abstract) < 40:
        # Fall back to TLDR — a short S2-generated summary, only usable
        # when there's no proper abstract.
        tldr = data.get("tldr")
        if isinstance(tldr, dict) and isinstance(tldr.get("text"), str) and len(tldr["text"]) >= 40:
            abstract = tldr["text"]
        else:
            abstract = None

    year = data.get("year") if isinstance(data.get("year"), int) else None

    venue_full = None
    pv = data.get("publicationVenue")
    if isinstance(pv, dict) and isinstance(pv.get("name"), str):
        venue_full = pv["name"]
    if not venue_full and isinstance(data.get("venue"), str):
        venue_full = data["venue"]

    ext = data.get("externalIds") or {}
    doi = ext.get("DOI") if isinstance(ext, dict) else None
    if isinstance(doi, str):
        doi = doi.lower()
    else:
        doi = None
    arxiv_id = ext.get("ArXiv") if isinstance(ext, dict) else None
    if not isinstance(arxiv_id, str):
        arxiv_id = None

    s2_paper_id = data.get("paperId") if isinstance(data.get("paperId"), str) else None
    url = data.get("url") if isinstance(data.get("url"), str) else None

    return Meta(
        title=title,
        authors=authors or None,
        abstract=abstract,
        year=year,
        venue_full=venue_full,
        doi=doi,
        arxiv_id=arxiv_id,
        s2_paper_id=s2_paper_id,
        url=url,
    )


__all__ = ["fetch", "API_ROOT"]
