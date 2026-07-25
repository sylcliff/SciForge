"""Normalized query object shared across adapters.

Adapters read this to build their per-source URL. The `query.py` module
constructs one QueryObject per CLI invocation.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

Mode = Literal["keyword", "raw", "fields", "strategy"]


@dataclass
class QueryObject:
    mode: Mode
    text: str | None = None                # keyword or raw
    fields: dict[str, Any] | None = None   # fields mode: {title, author, journal, ...}
    per_source: dict[str, str] | None = None  # strategy mode: {pubmed: "...", ...}

    year_from: int | None = None
    year_to: int | None = None

    def free_text(self) -> str:
        """Best-effort plain string, used as fallback for sources missing
        a compiled strategy entry or for building 'search' params."""
        if self.mode in ("keyword", "raw"):
            return self.text or ""
        if self.mode == "fields" and self.fields:
            parts = []
            if self.fields.get("title"):
                parts.append(str(self.fields["title"]))
            for a in self.fields.get("authors", []) or []:
                parts.append(str(a))
            if self.fields.get("journal"):
                parts.append(str(self.fields["journal"]))
            return " ".join(parts)
        if self.mode == "strategy" and self.per_source:
            # Use any compiled string as fallback (they're all similar)
            for src in ("openalex", "crossref", "s2", "pubmed", "arxiv"):
                if src in self.per_source:
                    return self.per_source[src]
            return next(iter(self.per_source.values()), "")
        return ""
