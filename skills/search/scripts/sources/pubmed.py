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

    records = fetch_by_pmids(pmids, cfg=cfg, respect_rate_limit=respect_rate_limit)

    # Preserve esearch ranking
    out: list[dict[str, Any]] = []
    for rank, pmid in enumerate(pmids, start=1):
        rec = records.get(pmid)
        if rec is None:
            continue
        rec["rank"] = rank
        out.append(rec)
    return out


# --------------------------------------------------------------------------- #
# Citation-graph helpers
# --------------------------------------------------------------------------- #


def fetch_by_pmids(pmids: list[str], *, cfg: SearchConfig,
                   respect_rate_limit: bool = False,
                   chunk_size: int = 200) -> dict[str, dict[str, Any]]:
    """Batch-fetch full records for a list of PMIDs.

    Uses efetch with `retmode=xml`, chunks at `chunk_size` per request.
    Returns a dict {pmid: record_dict} — same shape as `_parse_efetch_xml`
    produces. Best-effort — skips chunks that fail.
    """
    if not pmids:
        return {}
    out: dict[str, dict[str, Any]] = {}
    for i in range(0, len(pmids), chunk_size):
        chunk = pmids[i : i + chunk_size]
        efetch_url = build_url(
            f"{_BASE}/efetch.fcgi",
            {"db": "pubmed", "id": ",".join(chunk), "retmode": "xml"},
        )
        try:
            xml_bytes = http_get(
                efetch_url,
                source=SOURCE,
                cfg=cfg,
                respect_rate_limit=respect_rate_limit,
            )
        except Exception:  # noqa: BLE001
            continue
        try:
            records = _parse_efetch_xml(xml_bytes)
        except Exception:  # noqa: BLE001
            continue
        out.update(records)
    return out


def _elink_simple(pmid: str, linkname: str, *, cfg: SearchConfig,
                  respect_rate_limit: bool = False) -> list[str]:
    """Run elink with the given `linkname` and return a list of PMIDs.

    E-utilities `elink.fcgi` returns a JSON or XML structure with linked
    PMIDs. We use `retmode=json` for simplicity.

    An empty list means no links (either the paper has no PMC version or
    there are no links in the requested direction). Best-effort — never
    raises.
    """
    if not pmid or not pmid.strip():
        return []
    url = build_url(
        f"{_BASE}/elink.fcgi",
        {
            "dbfrom": "pubmed",
            "db": "pubmed",
            "linkname": linkname,
            "id": pmid.strip(),
            "retmode": "json",
        },
    )
    try:
        data = http_get_json(
            url, source=SOURCE, cfg=cfg,
            respect_rate_limit=respect_rate_limit,
        )
    except Exception:  # noqa: BLE001
        return []
    if not isinstance(data, dict):
        return []
    linksets = data.get("linksets") or []
    if not isinstance(linksets, list) or not linksets:
        return []
    for ls in linksets:
        links = ls.get("linksetdbs") or []
        for dbl in links:
            if isinstance(dbl, dict) and dbl.get("linkname") == linkname:
                return dbl.get("links") or []
    return []


def get_refs_elink(pmid: str, *, cfg: SearchConfig,
                   respect_rate_limit: bool = False) -> list[str]:
    """Return PMIDs of papers cited by `pmid`.

    Uses `elink.fcgi?linkname=pubmed_pubmed_refs` — only returns
    PMIDs when the paper is in PubMed Central. Empty list otherwise.
    """
    return _elink_simple(pmid, "pubmed_pubmed_refs", cfg=cfg,
                         respect_rate_limit=respect_rate_limit)


def get_citedin_elink(pmid: str, *, cfg: SearchConfig,
                      respect_rate_limit: bool = False) -> list[str]:
    """Return PMIDs of papers that cite `pmid`.

    Uses `elink.fcgi?linkname=pubmed_pubmed_citedin`.
    """
    return _elink_simple(pmid, "pubmed_pubmed_citedin", cfg=cfg,
                         respect_rate_limit=respect_rate_limit)


__all__ = ["SOURCE", "search", "fetch_by_pmids",
           "get_refs_elink", "get_citedin_elink"]
