"""arXiv Atom API adapter.

Endpoint: `http://export.arxiv.org/api/query?search_query=...&start=0&max_results=...`
Returns Atom XML — parse with xml.etree.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from typing import Any

from config import HTTPError, SearchConfig, build_url, http_get
from query_obj import QueryObject

SOURCE = "arxiv"
_BASE = "http://export.arxiv.org/api/query"

_ATOM_NS = {"atom": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}

# Extract "2101.00001" or "2101.00001v3" from arxiv URLs / DOIs
_ARXIV_ID_RE = re.compile(r"(\d{4}\.\d{4,5}|[a-z\-]+/\d{7})(v\d+)?")

# DOI pattern for scanning journal_ref and comment fields.
# Reject DataCite arxiv self-DOIs (10.48550/arxiv.*) — those aren't journal DOIs.
_DOI_RE = re.compile(r"\b10\.\d{4,}/[^\s\"'<>,;)]+")
_ARXIV_SELF_DOI_RE = re.compile(r"^10\.48550/arxiv\.", re.IGNORECASE)


def _extract_doi_from_text(text: str | None) -> str | None:
    """Scan arbitrary text (arxiv journal_ref or comment) for a DOI-shaped
    substring. Returns the first non-arxiv-self DOI, lowercased, or None."""
    if not text:
        return None
    for m in _DOI_RE.finditer(text):
        candidate = m.group(0).rstrip(".,;)]}\"'").lower()
        if _ARXIV_SELF_DOI_RE.match(candidate):
            continue
        return candidate
    return None


def _compile_arxiv_query(q: QueryObject) -> str:
    if q.mode == "keyword":
        return f"all:{q.text}" if q.text else ""
    if q.mode == "raw":
        return q.text or ""
    if q.mode == "strategy" and q.per_source and "arxiv" in q.per_source:
        return q.per_source["arxiv"]
    if q.mode == "fields" and q.fields:
        parts: list[str] = []
        if q.fields.get("title"):
            parts.append(f'ti:"{q.fields["title"]}"')
        for a in q.fields.get("authors") or []:
            parts.append(f'au:"{a}"')
        # arXiv doesn't have a journal field; fold into abstract
        if q.fields.get("journal"):
            parts.append(f'abs:"{q.fields["journal"]}"')
        return " AND ".join(parts) if parts else f"all:{q.free_text()}"
    return f"all:{q.free_text()}"


def _parse_atom(xml_bytes: bytes) -> list[dict[str, Any]]:
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as e:
        raise HTTPError(SOURCE, f"Atom parse: {e}") from e

    out: list[dict[str, Any]] = []
    for entry in root.findall("atom:entry", _ATOM_NS):
        id_el = entry.find("atom:id", _ATOM_NS)
        raw_id = id_el.text.strip() if id_el is not None and id_el.text else ""
        m = _ARXIV_ID_RE.search(raw_id)
        arxiv_id = m.group(1) if m else raw_id.split("/")[-1].split("v")[0]

        title_el = entry.find("atom:title", _ATOM_NS)
        title = " ".join(title_el.text.split()) if title_el is not None and title_el.text else None

        summary_el = entry.find("atom:summary", _ATOM_NS)
        abstract = " ".join(summary_el.text.split()) if summary_el is not None and summary_el.text else None

        authors: list[str] = []
        for a in entry.findall("atom:author/atom:name", _ATOM_NS):
            if a.text:
                authors.append(a.text.strip())

        pub_el = entry.find("atom:published", _ATOM_NS)
        year: int | None = None
        if pub_el is not None and pub_el.text and len(pub_el.text) >= 4:
            try:
                year = int(pub_el.text[:4])
            except ValueError:
                pass

        doi_el = entry.find("arxiv:doi", _ATOM_NS)
        doi = doi_el.text.strip().lower() if doi_el is not None and doi_el.text else None

        journal_el = entry.find("arxiv:journal_ref", _ATOM_NS)
        journal_ref_txt = journal_el.text.strip() if journal_el is not None and journal_el.text else None

        # If arxiv:doi is empty, fall back to scanning journal_ref and
        # arxiv:comment for an embedded DOI. Authors sometimes forget to
        # fill the structured field but write "Published in ... doi:10..."
        # in the free-text areas.
        if not doi and journal_ref_txt:
            doi = _extract_doi_from_text(journal_ref_txt)
        if not doi:
            comment_el = entry.find("arxiv:comment", _ATOM_NS)
            if comment_el is not None and comment_el.text:
                doi = _extract_doi_from_text(comment_el.text)

        out.append({
            "source": SOURCE,
            "doi": doi,
            "pmid": None,
            "arxiv_id": arxiv_id,
            "openalex_id": None,
            "s2_id": None,
            "title": title,
            "authors": authors,
            "year": year,
            "journal": journal_ref_txt,
            "volume": None,
            "issue": None,
            "pages": None,
            "abstract": abstract,
            "citation_count": None,
            "type": "preprint",
            "url": f"https://arxiv.org/abs/{arxiv_id}",
        })
    return out


def search(
    q: QueryObject,
    *,
    limit: int,
    cfg: SearchConfig,
    respect_rate_limit: bool = False,
) -> list[dict[str, Any]]:
    search_query = _compile_arxiv_query(q)
    if not search_query.strip():
        return []

    # Year filter: arxiv doesn't have a first-class date filter in Atom;
    # sort by relevance and post-filter.
    url = build_url(
        _BASE,
        {
            "search_query": search_query,
            "start": 0,
            "max_results": limit,
            "sortBy": "relevance",
            "sortOrder": "descending",
        },
    )

    xml_bytes = http_get(
        url,
        source=SOURCE,
        cfg=cfg,
        respect_rate_limit=respect_rate_limit,
    )
    records = _parse_atom(xml_bytes)

    # Post-hoc year filter
    if q.year_from or q.year_to:
        lo = q.year_from or 0
        hi = q.year_to or 9999
        records = [r for r in records if r.get("year") and lo <= r["year"] <= hi]

    for rank, rec in enumerate(records[:limit], start=1):
        rec["rank"] = rank
    return records[:limit]


__all__ = ["SOURCE", "search"]
