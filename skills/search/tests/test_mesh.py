"""Mesh compile — pure local tests (no network)."""

from __future__ import annotations

import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(_SCRIPTS))

from mesh import compile_strategy  # noqa: E402


def test_compile_single_concept():
    concepts = [{"mesh": "Diabetes Mellitus", "synonyms": ["diabetes", "diabetic"]}]
    out = compile_strategy(concepts, op="AND")

    pubmed = out["pubmed"]
    assert '"Diabetes Mellitus"[MeSH]' in pubmed
    assert '"Diabetes Mellitus"[tiab]' in pubmed
    assert "diabetes[tiab]" in pubmed
    assert "diabetic[tiab]" in pubmed

    crossref = out["crossref"]
    assert '"Diabetes Mellitus"' in crossref
    assert "diabetes" in crossref

    arxiv = out["arxiv"]
    assert 'all:"Diabetes Mellitus"' in arxiv
    assert "all:diabetes" in arxiv


def test_compile_two_concepts_AND():
    concepts = [
        {"mesh": "Diabetes Mellitus", "synonyms": ["diabetes"]},
        {"mesh": "Heart Failure", "synonyms": ["cardiac failure"]},
    ]
    out = compile_strategy(concepts, op="AND")
    assert " AND " in out["pubmed"]
    assert " AND " in out["crossref"]
    assert " AND " in out["arxiv"]
    assert "Diabetes Mellitus" in out["pubmed"]
    assert "Heart Failure" in out["pubmed"]


def test_compile_OR():
    concepts = [
        {"mesh": "COVID-19", "synonyms": []},
        {"mesh": "Influenza", "synonyms": []},
    ]
    out = compile_strategy(concepts, op="OR")
    assert " OR " in out["pubmed"]


def test_compile_synonym_with_space_quoted():
    concepts = [{"mesh": "Heart Failure", "synonyms": ["cardiac failure"]}]
    out = compile_strategy(concepts, op="AND")
    # In free-text sources, multi-word synonyms should be quoted
    assert '"cardiac failure"' in out["crossref"]
