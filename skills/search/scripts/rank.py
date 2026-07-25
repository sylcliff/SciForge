"""Ranking of merged records.

Default: Reciprocal Rank Fusion (RRF) with k=60 — the TREC standard.
For each record:
    score = Σ over sources_hit  1 / (k + rank_in_source)

`--sort year:desc` and `--sort citations:desc` override by taking the
year / citation_count value directly.
"""

from __future__ import annotations

from typing import Any, Literal

RRF_K = 60

SortKey = Literal["relevance", "year:desc", "citations:desc"]


def _rrf_score(record: dict[str, Any]) -> float:
    total = 0.0
    for src, rank in (record.get("rank_by_source") or {}).items():
        if isinstance(rank, int) and rank > 0:
            total += 1.0 / (RRF_K + rank)
    return total


def rank(records: list[dict[str, Any]], sort_key: SortKey) -> list[dict[str, Any]]:
    """Return `records` sorted (best first), with 'score' and 'index' set."""
    if sort_key == "relevance":
        scored = [(r, _rrf_score(r)) for r in records]
        scored.sort(key=lambda x: (-x[1], x[0]["identifier"]))
        for i, (r, s) in enumerate(scored):
            r["score"] = round(s, 6)
            r["index"] = i
        return [r for r, _ in scored]

    if sort_key == "year:desc":
        # Missing year sinks to the bottom
        def key(r: dict[str, Any]) -> tuple[int, str]:
            y = r["meta"].get("year")
            return (-(y if isinstance(y, int) else -9999), r["identifier"])
        records.sort(key=key)
        for i, r in enumerate(records):
            r["score"] = r["meta"].get("year") or 0
            r["index"] = i
        return records

    if sort_key == "citations:desc":
        def key(r: dict[str, Any]) -> tuple[int, str]:
            c = r["meta"].get("citation_count")
            return (-(c if isinstance(c, int) else -1), r["identifier"])
        records.sort(key=key)
        for i, r in enumerate(records):
            r["score"] = r["meta"].get("citation_count") or 0
            r["index"] = i
        return records

    raise ValueError(f"unknown sort key: {sort_key!r}")


__all__ = ["rank", "RRF_K"]
