"""JSON contract stability tests.

Locks the wire format the way references/output-schema.md describes it.
If someone silently renames or reshapes a field, these tests break.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(_SCRIPTS))

from output import (  # noqa: E402
    Candidate,
    Meta,
    PaperResult,
    PdfAttempt,
    Status,
    Summary,
)


# --------------------------------------------------------------------------- #
# Status enum
# --------------------------------------------------------------------------- #


def test_status_has_exactly_nine_values() -> None:
    """The spec says 9. Any drift is a contract change."""
    assert len(Status) == 9


def test_status_values_are_the_documented_slugs() -> None:
    """Wire-level strings must not change."""
    expected = {
        "downloaded",
        "metadata_only",
        "paywalled",
        "identifier_not_found",
        "pdf_link_broken",
        "title_ambiguous",
        "rate_limited",
        "network_error",
        "invalid_input",
    }
    assert {s.value for s in Status} == expected


# --------------------------------------------------------------------------- #
# Meta — subset of sf-lit ingest schema v2
# --------------------------------------------------------------------------- #


def test_meta_required_field() -> None:
    """title is the only required field per output-schema.md."""
    m = Meta(title="X")
    d = m.to_ingest_json()
    assert d == {"title": "X"}


def test_meta_serialization_omits_none() -> None:
    """The ingest schema uses 'omit' semantics, not 'null'."""
    m = Meta(title="X", year=2020, doi=None, authors=None)
    d = m.to_ingest_json()
    assert d == {"title": "X", "year": 2020}
    assert "doi" not in d
    assert "authors" not in d


def test_meta_forbids_extra_fields() -> None:
    """extra=forbid guards the ingest contract from silent additions."""
    with pytest.raises(Exception):
        Meta(title="X", not_a_field="oops")  # type: ignore[call-arg]


def test_meta_field_names_match_ingest_v2() -> None:
    """Field names must match skills/literature/references/ingest-interface.md v2."""
    m = Meta(
        title="X",
        authors=["A"],
        abstract="A" * 50,
        year=2020,
        venue="Nature",
        venue_full="Nature (long)",
        doi="10.1/x",
        arxiv_id="1706.03762",
        s2_paper_id="abc",
        url="https://x",
    )
    d = m.to_ingest_json()
    # All fields present and named exactly per the spec.
    assert set(d.keys()) == {
        "title", "authors", "abstract", "year",
        "venue", "venue_full",
        "doi", "arxiv_id", "s2_paper_id", "url",
    }


# --------------------------------------------------------------------------- #
# PaperResult (per-paper NDJSON line)
# --------------------------------------------------------------------------- #


def test_paper_result_downloaded_shape() -> None:
    pr = PaperResult(
        index=0,
        identifier="1706.03762",
        status=Status.DOWNLOADED,
        pdf_path="/tmp/1706.03762.pdf",
        source_used="arxiv",
        sources_queried=["arxiv"],
        bytes=12345,
        meta=Meta(title="Attention Is All You Need"),
    )
    obj = json.loads(pr.to_ndjson())
    assert obj["index"] == 0
    assert obj["status"] == "downloaded"
    assert obj["pdf_path"] == "/tmp/1706.03762.pdf"
    assert obj["source_used"] == "arxiv"
    assert obj["sources_queried"] == ["arxiv"]
    assert obj["bytes"] == 12345
    assert obj["meta"]["title"] == "Attention Is All You Need"


def test_paper_result_failure_drops_null_fields() -> None:
    """Serialization should exclude None so the JSON matches the doc."""
    pr = PaperResult(
        index=1,
        identifier="10.9999/notfound",
        status=Status.IDENTIFIER_NOT_FOUND,
        sources_queried=["crossref", "openalex"],
    )
    obj = json.loads(pr.to_ndjson())
    assert "pdf_path" not in obj
    assert "bytes" not in obj
    assert "meta" not in obj
    assert obj["status"] == "identifier_not_found"


def test_paper_result_title_ambiguous_carries_candidates() -> None:
    pr = PaperResult(
        index=0,
        identifier="attention",
        status=Status.TITLE_AMBIGUOUS,
        sources_queried=["openalex"],
        candidates=[
            Candidate(title="Attention Is All You Need", doi="10.1/x", year=2017, first_author="A B"),
            Candidate(title="Attention", year=2020, first_author="C D"),
        ],
    )
    obj = json.loads(pr.to_ndjson())
    assert len(obj["candidates"]) == 2
    assert obj["candidates"][0]["doi"] == "10.1/x"
    assert "doi" not in obj["candidates"][1]  # None fields omitted


def test_paper_result_pdf_link_broken_carries_attempts() -> None:
    pr = PaperResult(
        index=0,
        identifier="10.1/x",
        status=Status.PDF_LINK_BROKEN,
        sources_queried=["crossref"],
        pdf_attempts=[
            PdfAttempt(source="crossref", url="https://…", reason="http_403"),
            PdfAttempt(source="unpaywall", url="https://…", reason="not_pdf_magic"),
        ],
    )
    obj = json.loads(pr.to_ndjson())
    assert len(obj["pdf_attempts"]) == 2
    assert obj["pdf_attempts"][0]["reason"] == "http_403"


# --------------------------------------------------------------------------- #
# Summary line
# --------------------------------------------------------------------------- #


def test_summary_shape() -> None:
    s = Summary(total=10, downloaded=7, paywalled=1, identifier_not_found=1, rate_limited=1)
    s.elapsed_seconds = 8.4
    s.warnings = ["s2_no_key_seen_429"]
    obj = json.loads(s.to_ndjson())
    assert set(obj.keys()) == {"summary"}
    body = obj["summary"]
    # All 9 status counters must be present, defaulting to 0.
    for k in [
        "downloaded", "metadata_only", "paywalled",
        "identifier_not_found", "pdf_link_broken",
        "title_ambiguous", "rate_limited",
        "network_error", "invalid_input",
    ]:
        assert k in body
    assert body["total"] == 10
    assert body["downloaded"] == 7
    assert body["warnings"] == ["s2_no_key_seen_429"]
    assert body["elapsed_seconds"] == 8.4


def test_summary_line_never_contains_index() -> None:
    """Parser rule: per-paper lines have `index`, summary line does not."""
    body = json.loads(Summary().to_ndjson())
    assert "index" not in body
    assert "summary" in body


def test_paper_result_line_never_contains_summary_key() -> None:
    pr = PaperResult(index=0, identifier="x", status=Status.INVALID_INPUT)
    obj = json.loads(pr.to_ndjson())
    assert "summary" not in obj
    assert "index" in obj


# --------------------------------------------------------------------------- #
# NDJSON discipline — one line, valid JSON
# --------------------------------------------------------------------------- #


def test_ndjson_is_one_line() -> None:
    pr = PaperResult(index=0, identifier="x", status=Status.DOWNLOADED, meta=Meta(title="Multi\nline\ntitle"))
    line = pr.to_ndjson()
    # Even if the meta title contains newlines, JSON encodes them as \n
    # and the emitted line stays a single physical line.
    assert "\n" not in line
    assert json.loads(line)["meta"]["title"] == "Multi\nline\ntitle"


if __name__ == "__main__":  # pragma: no cover
    sys.exit(pytest.main([__file__, "-v"]))
