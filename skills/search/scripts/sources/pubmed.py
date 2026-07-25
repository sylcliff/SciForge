"""PubMed E-utilities adapter.

Two-step protocol:
  1. esearch → list of PMIDs (up to `limit`)
  2. efetch  → XML records for all PMIDs in one call

MeSH endpoints live in `mesh.py`, not here — this file is only for
paper search.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import Any

from config import HTTPError, SearchConfig, build_url, http_get, http_get_json
from query_obj import QueryObject

SOURCE = "pubmed"
_BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"


def _compile_pubmed_query(q: QueryObject) -> str:
    """Turn a QueryObject into a PubMed-syntax query string."""
    if q.mode == "keyword":
        term = q.text or ""
    elif q.mode == "raw":
        term = q.text or ""
    elif q.mode == "strategy" and q.per_source and "pubmed" in q.per_source:
        term = q.per_source["pubmed"]
    elif q.mode == "fields" and q.fields:
        parts: list[str] = []
        if q.fields.get("title"):
            parts.append(f'{q.fields["title"]}[TI]')
        authors = q.fields.get("authors") or []
        if authors:
            au_terms = " OR ".join(f'{a}[AU]' for a in authors)
            parts.append(f"({au_terms})" if len(authors) > 1 else au_terms)
        if q.fields.get("journal"):
            parts.append(f'{q.fields["journal"]}[TA]')
        term = " AND ".join(parts) if parts else q.free_text()
    else:
        term = q.free_text()

    # Year filter appends regardless of mode
    if q.year_from and q.year_to:
        term = f"({term}) AND ({q.year_from}:{q.year_to}[DP])"
    elif q.year_from:
        term = f"({term}) AND ({q.year_from}:3000[DP])"
    elif q.year_to:
        term = f"({term}) AND (1900:{q.year_to}[DP])"

    return term


def _parse_efetch_xml(xml_bytes: bytes) -> dict[str, dict[str, Any]]:
    """Parse esummary/efetch XML into {pmid: record}."""
    out: dict[str, dict[str, Any]] = {}
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as e:
        raise HTTPError(SOURCE, f"efetch XML parse: {e}") from e

    for art in root.findall(".//PubmedArticle"):
        pmid_el = art.find(".//PMID")
        if pmid_el is None or not pmid_el.text:
            continue
        pmid = pmid_el.text.strip()

        # Title
        title_el = art.find(".//ArticleTitle")
        title = "".join(title_el.itertext()).strip() if title_el is not None else None

        # Abstract (may have multiple sections)
        abstract_parts: list[str] = []
        for ab in art.findall(".//Abstract/AbstractText"):
            label = ab.get("Label")
            text = "".join(ab.itertext()).strip()
            if not text:
                continue
            abstract_parts.append(f"{label}: {text}" if label else text)
        abstract = " ".join(abstract_parts) if abstract_parts else None

        # Authors
        authors: list[str] = []
        for a in art.findall(".//AuthorList/Author"):
            fore = a.findtext("ForeName") or a.findtext("Initials") or ""
            last = a.findtext("LastName") or ""
            name = f"{fore} {last}".strip()
            if name:
                authors.append(name)

        # Year
        year: int | None = None
        for y_el in art.findall(".//PubDate/Year"):
            try:
                year = int(y_el.text or "")
                break
            except (TypeError, ValueError):
                pass
        if year is None:
            for md_el in art.findall(".//PubDate/MedlineDate"):
                text = (md_el.text or "").strip()
                if len(text) >= 4 and text[:4].isdigit():
                    year = int(text[:4])
                    break

        # Journal
        journal = art.findtext(".//Journal/Title")
        volume = art.findtext(".//Journal/JournalIssue/Volume")
        issue = art.findtext(".//Journal/JournalIssue/Issue")
        pages = art.findtext(".//Pagination/MedlinePgn")

        # DOI
        doi: str | None = None
        for aid in art.findall(".//ArticleId"):
            if aid.get("IdType") == "doi" and aid.text:
                doi = aid.text.strip().lower()
                break

        out[pmid] = {
            "source": SOURCE,
            "doi": doi,
            "pmid": pmid,
            "arxiv_id": None,
            "openalex_id": None,
            "s2_id": None,
            "title": title,
            "authors": authors,
            "year": year,
            "journal": journal,
            "volume": volume,
            "issue": issue,
            "pages": pages,
            "abstract": abstract,
            "citation_count": None,
            "type": "journal-article",
            "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
        }
    return out


def search(
    q: QueryObject,
    *,
    limit: int,
    cfg: SearchConfig,
    respect_rate_limit: bool = False,
) -> list[dict[str, Any]]:
    """Return up to `limit` records from PubMed."""
    term = _compile_pubmed_query(q)
    if not term.strip():
        return []

    esearch_url = build_url(
        f"{_BASE}/esearch.fcgi",
        {"db": "pubmed", "term": term, "retmax": limit, "retmode": "json"},
    )
    data = http_get_json(
        esearch_url,
        source=SOURCE,
        cfg=cfg,
        respect_rate_limit=respect_rate_limit,
    )

    pmids = data.get("esearchresult", {}).get("idlist", []) or []
    if not pmids:
        return []

    efetch_url = build_url(
        f"{_BASE}/efetch.fcgi",
        {"db": "pubmed", "id": ",".join(pmids), "retmode": "xml"},
    )
    xml_bytes = http_get(
        efetch_url,
        source=SOURCE,
        cfg=cfg,
        respect_rate_limit=respect_rate_limit,
    )
    records = _parse_efetch_xml(xml_bytes)

    # Preserve esearch ranking
    out: list[dict[str, Any]] = []
    for rank, pmid in enumerate(pmids, start=1):
        rec = records.get(pmid)
        if rec is None:
            continue
        rec["rank"] = rank
        out.append(rec)
    return out


__all__ = ["SOURCE", "search"]
