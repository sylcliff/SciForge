"""Output serializer tests — pure local, no network."""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(_SCRIPTS))

import output  # noqa: E402


def _sample_record(**overrides):
    rec = {
        "index": 0,
        "identifier": "10.1038/s41586-020-2649-2",
        "sources_hit": ["crossref", "pubmed"],
        "score": 0.033,
        "rank_by_source": {"crossref": 1, "pubmed": 4},
        "dedup_group": ["10.1038/s41586-020-2649-2", "32939066"],
        "meta": {
            "title": "Array programming with NumPy",
            "authors": ["Charles Harris", "K. Millman"],
            "year": 2020,
            "doi": "10.1038/s41586-020-2649-2",
            "pmid": "32939066",
            "arxiv_id": None,
            "journal": "Nature",
            "volume": "585",
            "issue": "7825",
            "pages": "357-362",
            "abstract": "Array programming provides...",
            "citation_count": 4211,
            "type": "journal-article",
            "url": "https://www.nature.com/...",
        },
    }
    rec["meta"].update(overrides.get("meta", {}))
    return rec


def _capture(fmt, records, **kw):
    buf = io.StringIO()
    old = sys.stdout
    sys.stdout = buf
    try:
        output.emit(records, fmt=fmt, out="-", **kw)
    finally:
        sys.stdout = old
    return buf.getvalue()


def test_ndjson_one_line_per_record():
    text = _capture("ndjson", [_sample_record(), _sample_record()])
    lines = [l for l in text.split("\n") if l]
    assert len(lines) == 2
    for line in lines:
        obj = json.loads(line)
        assert obj["identifier"] == "10.1038/s41586-020-2649-2"


def test_ndjson_summary_line():
    text = _capture("ndjson", [_sample_record()], summary={"queries_ran": 1})
    lines = [l for l in text.split("\n") if l]
    assert len(lines) == 2
    last = json.loads(lines[-1])
    assert "summary" in last


def test_ids_one_per_line():
    text = _capture("ids", [_sample_record(), _sample_record()])
    lines = [l for l in text.split("\n") if l]
    assert lines == ["10.1038/s41586-020-2649-2", "10.1038/s41586-020-2649-2"]


def test_table_has_header():
    text = _capture("table", [_sample_record()])
    assert "YEAR" in text
    assert "TITLE" in text
    assert "2020" in text
    assert "Nature" in text


def test_bib_valid_entry():
    text = _capture("bib", [_sample_record()])
    assert text.startswith("@article{")
    assert "title    = {" in text
    assert "doi      = {10.1038/s41586-020-2649-2}" in text
    assert text.rstrip().endswith("}")


def test_bib_preprint_becomes_misc():
    rec = _sample_record()
    rec["meta"]["type"] = "preprint"
    text = _capture("bib", [rec])
    assert text.startswith("@misc{")


def test_ris_journal_tag():
    text = _capture("ris", [_sample_record()])
    assert "TY  - JOUR" in text
    assert "AU  - Charles Harris" in text
    assert "AU  - K. Millman" in text
    assert "DO  - 10.1038/s41586-020-2649-2" in text
    assert "ER  - " in text


def test_ris_preprint_unpd():
    rec = _sample_record()
    rec["meta"]["type"] = "preprint"
    text = _capture("ris", [rec])
    assert "TY  - UNPD" in text
