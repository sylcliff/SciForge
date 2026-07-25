"""Crossref source — DOI metadata + potential OA links.

References/sources.md §Crossref is the ground truth for endpoint +
field mapping.
"""

from __future__ import annotations

import re
from typing import Optional

import httpx

from output import Meta
from sources import SourceResult

API_ROOT = "https://api.crossref.org/works"

# Strip JATS-style tags from abstracts (Crossref returns them raw).
_JATS_TAG = re.compile(r"</?[a-zA-Z][^>]*>")


async def fetch(doi: str, client: httpx.AsyncClient, *, polite_email: str = "") -> SourceResult:
    """Query Crossref for one DOI."""
    url = f"{API_ROOT}/{doi}"
    params: dict[str, str] = {}
    if polite_email:
        params["mailto"] = polite_email

    result = SourceResult(source_name="crossref")
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

    msg = data.get("message") if isinstance(data, dict) else None
    if not isinstance(msg, dict):
        result.not_found = True
        return result

    result.meta = _parse_message(msg)
    result.pdf_url_hint = _first_pdf_link(msg)
    return result


def _parse_message(m: dict) -> Optional[Meta]:
    title_list = m.get("title")
    title = _first_str(title_list)
    if not title:
        return None

    authors_list = []
    for a in m.get("author") or []:
        given = a.get("given") or ""
        family = a.get("family") or ""
        name = f"{given} {family}".strip()
        if not name and a.get("name"):
            name = a["name"]  # institutional author
        if name:
            authors_list.append(name)

    abstract = m.get("abstract")
    if isinstance(abstract, str):
        abstract = _JATS_TAG.sub("", abstract).strip()
        if len(abstract) < 40:
            abstract = None
    else:
        abstract = None

    year = _extract_year(m)

    venue_full = _first_str(m.get("container-title"))
    venue = _first_str(m.get("short-container-title"))

    doi_val = m.get("DOI")
    doi = doi_val.lower() if isinstance(doi_val, str) else None
    url = m.get("URL") if isinstance(m.get("URL"), str) else None

    return Meta(
        title=title,
        authors=authors_list or None,
        abstract=abstract,
        year=year,
        venue=venue,
        venue_full=venue_full,
        doi=doi,
        url=url,
    )


def _first_str(v) -> Optional[str]:
    if isinstance(v, list) and v:
        first = v[0]
        return first if isinstance(first, str) and first else None
    if isinstance(v, str) and v:
        return v
    return None


def _extract_year(m: dict) -> Optional[int]:
    # issued.date-parts[0][0] is the canonical publication year in Crossref.
    for key in ("issued", "published-print", "published-online", "published", "created"):
        node = m.get(key)
        if not isinstance(node, dict):
            continue
        dp = node.get("date-parts")
        if isinstance(dp, list) and dp and isinstance(dp[0], list) and dp[0]:
            try:
                return int(dp[0][0])
            except (TypeError, ValueError):
                continue
    return None


def _first_pdf_link(m: dict) -> Optional[str]:
    links = m.get("link")
    if not isinstance(links, list):
        return None
    for link in links:
        if not isinstance(link, dict):
            continue
        if link.get("content-type") == "application/pdf" and link.get("URL"):
            return link["URL"]
    return None


__all__ = ["fetch", "API_ROOT"]
