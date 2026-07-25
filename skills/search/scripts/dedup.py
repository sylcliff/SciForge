"""Cross-source deduplication using union-find on identifiers.

Any record sharing a non-empty (DOI, PMID, arxiv_id, s2_id, openalex_id)
value is placed in the same group. Groups are then handed to `merge.py`.

Deliberate design: **no title-based fuzzy matching**. An arxiv preprint
whose parent journal Crossref record lacks an `arxiv_id` cross-ref will
remain a separate group — that's a known trade-off in favor of zero
false-positive merges.
"""

from __future__ import annotations

from typing import Any

_ID_FIELDS = ("doi", "pmid", "pmcid", "arxiv_id", "openalex_id", "s2_id")


class _UnionFind:
    def __init__(self) -> None:
        self._parent: dict[int, int] = {}

    def add(self, x: int) -> None:
        self._parent.setdefault(x, x)

    def find(self, x: int) -> int:
        p = self._parent[x]
        while p != self._parent[p]:
            self._parent[p] = self._parent[self._parent[p]]
            p = self._parent[p]
        self._parent[x] = p
        return p

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self._parent[rb] = ra


def group_by_identifier(records: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """Return records partitioned into duplicate-groups.

    Each returned group is a list of source records that should be
    merged into one final paper. Ordering of records within a group
    follows input order.
    """
    n = len(records)
    uf = _UnionFind()
    for i in range(n):
        uf.add(i)

    # For each id field, map value → first-seen index; union subsequent
    # indices with that first index.
    for field in _ID_FIELDS:
        seen: dict[str, int] = {}
        for i, rec in enumerate(records):
            v = rec.get(field)
            if not v:
                continue
            key = str(v).strip().lower()
            if not key:
                continue
            if key in seen:
                uf.union(seen[key], i)
            else:
                seen[key] = i

    groups: dict[int, list[int]] = {}
    for i in range(n):
        root = uf.find(i)
        groups.setdefault(root, []).append(i)

    return [[records[i] for i in idxs] for idxs in groups.values()]


__all__ = ["group_by_identifier"]
