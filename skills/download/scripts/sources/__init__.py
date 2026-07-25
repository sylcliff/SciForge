"""Shared types for source modules.

Every source module exposes at least one async function returning a
`SourceResult`. Errors don't cross module boundaries as exceptions —
they become fields on the result. The orchestrator (fetch.py) is the
sole place that maps source results to status codes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from output import Meta


@dataclass
class SourceResult:
    """One source's contribution for one paper.

    Fields:
        source_name       — canonical name, e.g. "arxiv", "unpaywall".
        meta              — Meta object if metadata was harvested; else None.
        pdf_url_hint      — a URL the source thinks points to a PDF.
        is_oa             — Unpaywall's authoritative "is this OA?" signal.
                            None for sources that don't answer this question.
        transport_error   — non-None if the request itself failed (DNS,
                            timeout, 5xx after retries, etc.). Value is a
                            short slug: "timeout", "http_5xx", "dns", …
        not_found         — True if the API definitively said "no such
                            record" (HTTP 404, arXiv totalResults=0, etc.).
        rate_limited      — True if the source returned 429 and we could
                            not satisfy Retry-After within our budget.
        raw_status_code   — the HTTP status code seen, if any (for debug).
    """

    source_name: str
    meta: Optional[Meta] = None
    pdf_url_hint: Optional[str] = None
    is_oa: Optional[bool] = None
    transport_error: Optional[str] = None
    not_found: bool = False
    rate_limited: bool = False
    raw_status_code: Optional[int] = None
    # For title search — candidates when top-1 does not confirm.
    candidates: list[dict] = field(default_factory=list)
    # Best DOI or arxiv-id when the source resolved a title to a real paper.
    resolved_doi: Optional[str] = None
    resolved_arxiv_id: Optional[str] = None


__all__ = ["SourceResult"]
