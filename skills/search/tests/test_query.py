"""Query.py unit tests — the four input modes and their mutex."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(_SCRIPTS))

import pytest  # noqa: E402
from query import QueryError, build_from_args  # noqa: E402


def _ns(**kw):
    """Build an argparse-like Namespace with the fields main.py's argparse would set."""
    ns = argparse.Namespace(
        positional=None, query=None, title=None, author=None,
        journal=None, year=None, from_strategy=None,
    )
    for k, v in kw.items():
        setattr(ns, k, v)
    return ns


def test_keyword_mode():
    q = build_from_args(_ns(positional="graph neural networks"))
    assert q.mode == "keyword"
    assert q.text == "graph neural networks"


def test_raw_mode():
    q = build_from_args(_ns(query="(GNN OR graph) AND drug"))
    assert q.mode == "raw"
    assert q.text == "(GNN OR graph) AND drug"


def test_fields_mode():
    q = build_from_args(_ns(title="Attention", author=["Vaswani", "Shazeer"]))
    assert q.mode == "fields"
    assert q.fields["title"] == "Attention"
    assert q.fields["authors"] == ["Vaswani", "Shazeer"]


def test_year_range():
    q = build_from_args(_ns(positional="x", year="2020..2024"))
    assert (q.year_from, q.year_to) == (2020, 2024)

    q = build_from_args(_ns(positional="x", year="2020"))
    assert (q.year_from, q.year_to) == (2020, 2020)

    q = build_from_args(_ns(positional="x", year="2020.."))
    assert (q.year_from, q.year_to) == (2020, None)

    q = build_from_args(_ns(positional="x", year="..2024"))
    assert (q.year_from, q.year_to) == (None, 2024)


def test_no_mode_errors():
    with pytest.raises(QueryError, match="no query"):
        build_from_args(_ns())


def test_multiple_modes_error():
    with pytest.raises(QueryError, match="conflicting"):
        build_from_args(_ns(positional="x", query="y"))
    with pytest.raises(QueryError, match="conflicting"):
        build_from_args(_ns(positional="x", title="y"))
    with pytest.raises(QueryError, match="conflicting"):
        build_from_args(_ns(query="x", from_strategy="strategy.json"))


def test_strategy_mode(tmp_path):
    import json
    p = tmp_path / "s.json"
    p.write_text(json.dumps({
        "compiled": {"pubmed": "a[MeSH]", "crossref": "a"}
    }))
    q = build_from_args(_ns(from_strategy=str(p)))
    assert q.mode == "strategy"
    assert q.per_source == {"pubmed": "a[MeSH]", "crossref": "a"}


def test_strategy_missing_file():
    with pytest.raises(QueryError, match="not found"):
        build_from_args(_ns(from_strategy="/does/not/exist.json"))


def test_strategy_invalid_json(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text("not json {{{")
    with pytest.raises(QueryError, match="valid JSON"):
        build_from_args(_ns(from_strategy=str(p)))


def test_strategy_no_compiled_block(tmp_path):
    import json
    p = tmp_path / "empty.json"
    p.write_text(json.dumps({"concepts": []}))
    with pytest.raises(QueryError, match="compiled"):
        build_from_args(_ns(from_strategy=str(p)))
