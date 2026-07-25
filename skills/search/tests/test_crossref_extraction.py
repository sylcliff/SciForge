"""Path A: cross-ref extraction from openalex + crossref adapter internals."""

from __future__ import annotations

import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(_SCRIPTS))

from sources import crossref, openalex  # noqa: E402


# --------------------------------------------------------------------------- #
# OpenAlex: extract arxiv_id from locations
# --------------------------------------------------------------------------- #


def test_openalex_extract_arxiv_from_primary_location():
    item = {
        "primary_location": {
            "landing_page_url": "https://arxiv.org/abs/1706.03762v5"
        }
    }
    assert openalex._extract_arxiv_id_from_locations(item) == "1706.03762"


def test_openalex_extract_arxiv_from_secondary_location():
    item = {
        "primary_location": {"landing_page_url": "https://www.nature.com/x"},
        "locations": [
            {"landing_page_url": "https://arxiv.org/abs/2101.00001"},
        ],
    }
    assert openalex._extract_arxiv_id_from_locations(item) == "2101.00001"


def test_openalex_extract_arxiv_old_style():
    item = {
        "locations": [
            {"landing_page_url": "https://arxiv.org/abs/hep-th/9901001v2"},
        ],
    }
    assert openalex._extract_arxiv_id_from_locations(item) == "hep-th/9901001"


def test_openalex_extract_arxiv_from_oa_url():
    item = {
        "open_access": {"oa_url": "https://arxiv.org/abs/2401.12345"},
    }
    assert openalex._extract_arxiv_id_from_locations(item) == "2401.12345"


def test_openalex_extract_arxiv_none_when_no_arxiv_url():
    item = {
        "primary_location": {"landing_page_url": "https://www.nature.com/x"},
    }
    assert openalex._extract_arxiv_id_from_locations(item) is None


def test_openalex_full_parse_populates_arxiv_id():
    """End-to-end: _parse_item should set arxiv_id."""
    item = {
        "id": "https://openalex.org/W2741809807",
        "title": "Attention Is All You Need",
        "publication_year": 2017,
        "primary_location": {
            "landing_page_url": "https://arxiv.org/abs/1706.03762v5"
        },
        "doi": "https://doi.org/10.abc/xyz",
        "authorships": [],
        "ids": {},
    }
    rec = openalex._parse_item(item)
    assert rec["arxiv_id"] == "1706.03762"
    assert rec["openalex_id"] == "W2741809807"


# --------------------------------------------------------------------------- #
# Crossref: extract arxiv_id from relation.has-preprint
# --------------------------------------------------------------------------- #


def test_crossref_extract_arxiv_from_has_preprint():
    item = {
        "relation": {
            "has-preprint": [
                {"id-type": "doi", "id": "10.48550/arXiv.1706.03762"}
            ]
        }
    }
    assert crossref._extract_arxiv_id_from_relation(item) == "1706.03762"


def test_crossref_extract_arxiv_case_insensitive():
    item = {
        "relation": {
            "has-preprint": [
                {"id-type": "doi", "id": "10.48550/ARXIV.2101.00001"}
            ]
        }
    }
    assert crossref._extract_arxiv_id_from_relation(item) == "2101.00001"


def test_crossref_extract_arxiv_from_is_preprint_of():
    item = {
        "relation": {
            "is-preprint-of": [
                {"id-type": "doi", "id": "10.48550/arxiv.hep-th/9901001v3"}
            ]
        }
    }
    assert crossref._extract_arxiv_id_from_relation(item) == "hep-th/9901001"


def test_crossref_extract_arxiv_none_when_no_relation():
    assert crossref._extract_arxiv_id_from_relation({}) is None
    assert crossref._extract_arxiv_id_from_relation({"relation": {}}) is None


def test_crossref_extract_arxiv_none_when_non_arxiv_doi():
    item = {
        "relation": {
            "has-preprint": [
                {"id-type": "doi", "id": "10.1234/some.other.doi"}
            ]
        }
    }
    assert crossref._extract_arxiv_id_from_relation(item) is None


def test_crossref_full_parse_populates_arxiv_id():
    item = {
        "DOI": "10.1234/nips2017",
        "title": ["Attention Is All You Need"],
        "author": [],
        "relation": {
            "has-preprint": [
                {"id-type": "doi", "id": "10.48550/arXiv.1706.03762"}
            ]
        },
    }
    rec = crossref._parse_item(item)
    assert rec["arxiv_id"] == "1706.03762"
    assert rec["doi"] == "10.1234/nips2017"
