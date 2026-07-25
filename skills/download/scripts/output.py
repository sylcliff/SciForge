"""Pydantic models + NDJSON writer for sf-download.

This module is the machine-checkable version of
`references/output-schema.md`. Any change to the wire format is a
change here first, then in the docs.

Every field name/type below matches the doc exactly. Callers of
`emit()` are expected to hand a fully-validated PaperResult or
BatchSummary and get back a single-line JSON string ready to write to
stdout.
"""

from __future__ import annotations

import json
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field


# --------------------------------------------------------------------------- #
# Status enum
# --------------------------------------------------------------------------- #


class Status(str, Enum):
    """The 9 canonical status values, per references/status-codes.md."""

    DOWNLOADED = "downloaded"
    METADATA_ONLY = "metadata_only"
    PAYWALLED = "paywalled"
    IDENTIFIER_NOT_FOUND = "identifier_not_found"
    PDF_LINK_BROKEN = "pdf_link_broken"
    TITLE_AMBIGUOUS = "title_ambiguous"
    RATE_LIMITED = "rate_limited"
    NETWORK_ERROR = "network_error"
    INVALID_INPUT = "invalid_input"


# --------------------------------------------------------------------------- #
# Meta — strict subset of the sf-lit ingest schema v2
# --------------------------------------------------------------------------- #


class Meta(BaseModel):
    """Subset of skills/literature/references/ingest-interface.md v2.

    Every field is optional except title; missing fields are omitted
    (not null) when serialized so the object is a clean prefix of the
    ingest schema. Unknown fields are forbidden — if code tries to set
    one, we want it to fail loud rather than silently invent contract.
    """

    model_config = ConfigDict(extra="forbid")

    title: str
    authors: Optional[list[str]] = None
    abstract: Optional[str] = None
    year: Optional[int] = None
    venue: Optional[str] = None
    venue_full: Optional[str] = None
    doi: Optional[str] = None
    arxiv_id: Optional[str] = None
    s2_paper_id: Optional[str] = None
    url: Optional[str] = None

    def to_ingest_json(self) -> dict[str, Any]:
        """Serialize dropping None fields (ingest schema uses omit, not null)."""
        return self.model_dump(exclude_none=True)


# --------------------------------------------------------------------------- #
# Per-paper result
# --------------------------------------------------------------------------- #


class Candidate(BaseModel):
    """One entry in the `candidates` list under status=title_ambiguous."""

    model_config = ConfigDict(extra="forbid")

    title: str
    doi: Optional[str] = None
    year: Optional[int] = None
    first_author: Optional[str] = None


class PdfAttempt(BaseModel):
    """One entry in the `pdf_attempts` list under status=pdf_link_broken."""

    model_config = ConfigDict(extra="forbid")

    source: str  # "arxiv" | "unpaywall" | "semanticscholar" | "crossref"
    url: str
    reason: str  # http_<code> | not_pdf_magic | not_pdf_content_type | zero_bytes | timeout | verify_error


class PaperResult(BaseModel):
    """One NDJSON line describing the outcome for one input identifier."""

    model_config = ConfigDict(extra="forbid")

    index: int
    identifier: str  # exactly as the caller supplied it, not normalized
    status: Status
    pdf_path: Optional[str] = None
    source_used: Optional[str] = None
    sources_queried: list[str] = Field(default_factory=list)
    bytes: Optional[int] = None
    meta: Optional[Meta] = None

    # Present only when relevant (schema.md keeps them optional-and-omitted).
    candidates: Optional[list[Candidate]] = None
    pdf_attempts: Optional[list[PdfAttempt]] = None

    def to_ndjson(self) -> str:
        """One-line JSON representation. Drops None fields entirely."""
        return _dump_json(self.model_dump(exclude_none=True))


# --------------------------------------------------------------------------- #
# Batch summary
# --------------------------------------------------------------------------- #


class Summary(BaseModel):
    """Terminal summary line for a batch run.

    Every status counter is present with default 0 so parsers can rely
    on the shape.
    """

    model_config = ConfigDict(extra="forbid")

    total: int = 0
    downloaded: int = 0
    metadata_only: int = 0
    paywalled: int = 0
    identifier_not_found: int = 0
    pdf_link_broken: int = 0
    title_ambiguous: int = 0
    rate_limited: int = 0
    network_error: int = 0
    invalid_input: int = 0
    elapsed_seconds: float = 0.0
    warnings: list[str] = Field(default_factory=list)

    def to_ndjson(self) -> str:
        """One-line JSON envelope: {"summary": {...}}."""
        return _dump_json({"summary": self.model_dump()})


# --------------------------------------------------------------------------- #
# JSON helper
# --------------------------------------------------------------------------- #


def _dump_json(obj: Any) -> str:
    """Compact one-line JSON with ensure_ascii=False so unicode is readable.

    Sorted keys make output diffable in tests. This is stricter than
    strictly necessary — the wire format doesn't require sorted keys —
    but stability across runs is a nice property.
    """
    return json.dumps(obj, ensure_ascii=False, sort_keys=True)


__all__ = [
    "Status",
    "Meta",
    "Candidate",
    "PdfAttempt",
    "PaperResult",
    "Summary",
]
