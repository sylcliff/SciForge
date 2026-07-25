"""arXiv source — Atom XML query, no auth.

Public API: `fetch(arxiv_id, client) -> SourceResult`.

References/sources.md §arXiv is the ground truth for endpoint + field
mapping. This module never raises across its boundary; all failures
become fields on the returned SourceResult.
"""

from __future__ import annotations

import re
from typing import Optional
from xml.etree import ElementTree as ET

import httpx

from output import Meta
from sources import SourceResult

API_ROOT = "http://export.arxiv.org/api/query"

# Atom + arXiv namespace map for ElementTree
_NS = {
    "a": "http://www.w3.org/2005/Atom",
    "arxiv": "http://arxiv.org/schemas/atom",
    "opensearch": "http://a9.com/-/spec/opensearch/1.1/",
}


async def fetch(arxiv_id: str, client: httpx.AsyncClient) -> SourceResult:
    """Query arXiv for one arxiv_id (canonical form, no version)."""
    params = {"id_list": arxiv_id, "max_results": "1"}
    result = SourceResult(source_name="arxiv")
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
        # 400 from arXiv on this endpoint means malformed id; treat as
        # not_found so the orchestrator moves on cleanly.
        result.not_found = True
        return result

    try:
        root = ET.fromstring(r.text)
    except ET.ParseError:
        result.transport_error = "parse_error"
        return result

    total = _find_text(root, "opensearch:totalResults")
    entry = root.find("a:entry", _NS)
    if total == "0" or entry is None:
        result.not_found = True
        return result

    result.meta = _parse_entry(entry)
    result.pdf_url_hint = f"https://arxiv.org/pdf/{arxiv_id}.pdf"
    return result


# --------------------------------------------------------------------------- #
# XML → Meta
# --------------------------------------------------------------------------- #


def _parse_entry(entry: ET.Element) -> Optional[Meta]:
    title = _find_text(entry, "a:title")
    if not title:
        return None
    title = _collapse_ws(title)

    authors: list[str] = []
    for a in entry.findall("a:author", _NS):
        name = _find_text(a, "a:name")
        if name:
            authors.append(_collapse_ws(name))

    abstract = _find_text(entry, "a:summary")
    if abstract:
        abstract = _collapse_ws(abstract)
        if len(abstract) < 40:
            abstract = None

    published = _find_text(entry, "a:published")
    year: Optional[int] = None
    if published and len(published) >= 4 and published[:4].isdigit():
        year = int(published[:4])

    # arXiv id lives in `<id>`; strip URL prefix and version suffix.
    id_url = _find_text(entry, "a:id") or ""
    arxiv_id = _extract_arxiv_from_url(id_url)

    # DOI is optionally in <arxiv:doi>
    doi_val = _find_text(entry, "arxiv:doi")
    doi = doi_val.lower() if doi_val else None

    # Landing URL — <link rel="alternate" type="text/html">
    url = None
    for link in entry.findall("a:link", _NS):
        if link.get("rel") == "alternate" and link.get("type") == "text/html":
            url = link.get("href")
            break

    return Meta(
        title=title,
        authors=authors or None,
        abstract=abstract,
        year=year,
        doi=doi,
        arxiv_id=arxiv_id,
        url=url,
    )


def _find_text(elem: ET.Element, path: str) -> Optional[str]:
    found = elem.find(path, _NS)
    if found is None or found.text is None:
        return None
    return found.text


_ARXIV_ID_TAIL = re.compile(r"([A-Za-z0-9\.\-/]+?)(v\d+)?$")


def _extract_arxiv_from_url(url: str) -> Optional[str]:
    # url looks like http://arxiv.org/abs/1706.03762v7
    tail = url.rsplit("/", 1)[-1]
    m = _ARXIV_ID_TAIL.match(tail)
    if not m:
        return None
    return m.group(1)


def _collapse_ws(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


__all__ = ["fetch", "API_ROOT"]
