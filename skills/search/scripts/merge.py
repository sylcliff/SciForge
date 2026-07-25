"""Merge a group of duplicate records into a single result record.

Field priority policy is spelled out in `references/output-schema.md`.
Also emits the auxiliary fields for the NDJSON wire format:
- sources_hit (union)
- rank_by_source
- dedup_group (list of the strongest ID from each source)
- identifier (best-available primary key)
"""

from __future__ import annotations

from typing import Any

# Priority ordering per field. Head → highest priority.
_PRIORITY: dict[str, list[str]] = {
    "title":    ["crossref", "openalex", "pubmed", "s2", "arxiv"],
    "abstract": ["crossref", "openalex", "pubmed", "s2", "arxiv"],
    "authors":  ["crossref", "pubmed", "openalex", "s2", "arxiv"],
    "journal":  ["crossref", "openalex", "pubmed", "s2", "arxiv"],
    "volume":   ["crossref", "openalex", "pubmed"],
    "issue":    ["crossref", "openalex", "pubmed"],
    "pages":    ["crossref", "openalex", "pubmed"],
    "type":     ["crossref", "openalex", "pubmed", "s2", "arxiv"],
    "url":      ["crossref", "openalex", "pubmed", "s2", "arxiv"],
}

_ID_FIELDS = ("doi", "pmid", "pmcid", "arxiv_id", "openalex_id", "s2_id")


def _pick_by_priority(group: list[dict[str, Any]], field: str) -> Any:
    """Return the first non-empty value for `field` following priority."""
    priority = _PRIORITY.get(field, [])
    by_source = {r["source"]: r for r in group}
    for src in priority:
        if src in by_source:
            v = by_source[src].get(field)
            if v not in (None, "", [], 0) or (field == "authors" and v):
                return v
    # Fallback: any non-empty
    for r in group:
        v = r.get(field)
        if v not in (None, "", []):
            return v
    return None


def _earliest_year(group: list[dict[str, Any]]) -> int | None:
    years = [r.get("year") for r in group if isinstance(r.get("year"), int)]
    return min(years) if years else None


def _max_citations(group: list[dict[str, Any]]) -> int | None:
    counts = [r.get("citation_count") for r in group
              if isinstance(r.get("citation_count"), int)]
    return max(counts) if counts else None


def _union_id(group: list[dict[str, Any]], field: str) -> str | None:
    for r in group:
        v = r.get(field)
        if v:
            return str(v).strip()
    return None


def _best_identifier(meta: dict[str, Any]) -> str | None:
    for f in ("doi", "pmid", "arxiv_id", "openalex_id", "s2_id"):
        if meta.get(f):
            return str(meta[f])
    return None


def _is_oa(group: list[dict[str, Any]]) -> bool | None:
    """OpenAlex is the only source that publishes an authoritative is_oa
    flag; if any OpenAlex record in the group has a non-null value, use
    it. Otherwise None (unknown)."""
    for r in group:
        if r.get("source") == "openalex":
            v = r.get("is_oa")
            if isinstance(v, bool):
                return v
    return None


def merge_group(group: list[dict[str, Any]]) -> dict[str, Any]:
    """Merge a duplicate-group into one output record."""
    sources_hit = sorted({r["source"] for r in group})
    rank_by_source = {r["source"]: r.get("rank") for r in group}

    meta: dict[str, Any] = {
        "title":         _pick_by_priority(group, "title"),
        "authors":       _pick_by_priority(group, "authors") or [],
        "year":          _earliest_year(group),
        "doi":           _union_id(group, "doi"),
        "pmid":          _union_id(group, "pmid"),
        "pmcid":         _union_id(group, "pmcid"),
        "arxiv_id":      _union_id(group, "arxiv_id"),
        "openalex_id":   _union_id(group, "openalex_id"),
        "s2_id":         _union_id(group, "s2_id"),
        "url":           _pick_by_priority(group, "url"),
        "journal":       _pick_by_priority(group, "journal"),
        "volume":        _pick_by_priority(group, "volume"),
        "issue":         _pick_by_priority(group, "issue"),
        "pages":         _pick_by_priority(group, "pages"),
        "abstract":      _pick_by_priority(group, "abstract"),
        "citation_count": _max_citations(group),
        "type":          _pick_by_priority(group, "type"),
        "is_oa":         _is_oa(group),
    }

    identifier = _best_identifier(meta) or f"nomatch:{hash(str(group)) & 0xffffffff:x}"

    dedup_group = [v for v in (
        meta.get("doi"), meta.get("pmid"), meta.get("arxiv_id"),
        meta.get("openalex_id"), meta.get("s2_id"),
    ) if v]

    return {
        "identifier": identifier,
        "sources_hit": sources_hit,
        "rank_by_source": rank_by_source,
        "dedup_group": dedup_group,
        "meta": meta,
    }


def merge_all(groups: list[list[dict[str, Any]]]) -> list[dict[str, Any]]:
    return [merge_group(g) for g in groups]


__all__ = ["merge_group", "merge_all"]
