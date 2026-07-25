"""Unit tests for dedup / merge / rank — offline, no network."""

from __future__ import annotations

import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(_SCRIPTS))

import dedup  # noqa: E402
import merge  # noqa: E402
import rank  # noqa: E402


def _mk(source, rank_i, **fields):
    base = {
        "source": source, "rank": rank_i,
        "doi": None, "pmid": None, "arxiv_id": None,
        "openalex_id": None, "s2_id": None,
        "title": None, "authors": [], "year": None,
        "journal": None, "volume": None, "issue": None, "pages": None,
        "abstract": None, "citation_count": None, "type": None, "url": None,
    }
    base.update(fields)
    return base


# --------------------------------------------------------------------------- #
# dedup
# --------------------------------------------------------------------------- #


def test_dedup_by_doi():
    recs = [
        _mk("pubmed", 1, doi="10.1/x", title="A"),
        _mk("crossref", 3, doi="10.1/X", title="A"),  # case-insensitive
        _mk("arxiv", 5, arxiv_id="2101.00001", title="B"),
    ]
    groups = dedup.group_by_identifier(recs)
    assert len(groups) == 2
    sizes = sorted(len(g) for g in groups)
    assert sizes == [1, 2]


def test_dedup_transitive_by_multiple_ids():
    """A(doi) — B(doi+pmid) — C(pmid)  → all in one group."""
    recs = [
        _mk("crossref", 1, doi="10.1/x"),
        _mk("openalex", 2, doi="10.1/x", pmid="123"),
        _mk("pubmed",   3, pmid="123"),
    ]
    groups = dedup.group_by_identifier(recs)
    assert len(groups) == 1
    assert len(groups[0]) == 3


def test_dedup_no_shared_ids_stay_separate():
    recs = [
        _mk("pubmed", 1, pmid="1"),
        _mk("pubmed", 2, pmid="2"),
    ]
    groups = dedup.group_by_identifier(recs)
    assert len(groups) == 2


def test_dedup_empty_strings_ignored():
    """Empty/None ids must not create false unions."""
    recs = [
        _mk("a", 1, doi=""),
        _mk("b", 2, doi=None),
        _mk("c", 3),
    ]
    groups = dedup.group_by_identifier(recs)
    assert len(groups) == 3


# --------------------------------------------------------------------------- #
# merge
# --------------------------------------------------------------------------- #


def test_merge_title_priority():
    # Crossref wins over arxiv
    group = [
        _mk("arxiv", 1, title="original arxiv title"),
        _mk("crossref", 2, title="polished journal title"),
    ]
    merged = merge.merge_group(group)
    assert merged["meta"]["title"] == "polished journal title"


def test_merge_authors_priority():
    group = [
        _mk("arxiv", 1, authors=["a b"]),
        _mk("pubmed", 2, authors=["A B", "C D"]),
        _mk("crossref", 3, authors=["Alice B", "Carol D"]),
    ]
    merged = merge.merge_group(group)
    assert merged["meta"]["authors"] == ["Alice B", "Carol D"]


def test_merge_year_earliest():
    group = [
        _mk("crossref", 1, year=2021),
        _mk("arxiv", 2, year=2020),
        _mk("pubmed", 3, year=2022),
    ]
    merged = merge.merge_group(group)
    assert merged["meta"]["year"] == 2020


def test_merge_citations_max():
    group = [
        _mk("s2", 1, citation_count=100),
        _mk("openalex", 2, citation_count=150),
    ]
    merged = merge.merge_group(group)
    assert merged["meta"]["citation_count"] == 150


def test_merge_sources_hit_and_rank():
    group = [
        _mk("crossref", 1, doi="10.1/x"),
        _mk("pubmed", 4, doi="10.1/x"),
    ]
    merged = merge.merge_group(group)
    assert set(merged["sources_hit"]) == {"crossref", "pubmed"}
    assert merged["rank_by_source"] == {"crossref": 1, "pubmed": 4}


def test_merge_identifier_prefers_doi():
    group = [
        _mk("s2", 1, s2_id="abc", pmid="123", doi="10.1/x"),
    ]
    merged = merge.merge_group(group)
    assert merged["identifier"] == "10.1/x"


def test_merge_identifier_falls_back_to_pmid():
    group = [_mk("pubmed", 1, pmid="99999")]
    merged = merge.merge_group(group)
    assert merged["identifier"] == "99999"


# --------------------------------------------------------------------------- #
# is_oa merge (only OpenAlex is authoritative)
# --------------------------------------------------------------------------- #


def test_is_oa_from_openalex_true():
    group = [
        _mk("crossref", 1, doi="10.1/x"),
        _mk("openalex", 2, doi="10.1/x"),
    ]
    group[1]["is_oa"] = True
    merged = merge.merge_group(group)
    assert merged["meta"]["is_oa"] is True


def test_is_oa_from_openalex_false():
    group = [
        _mk("openalex", 1, doi="10.1/x"),
    ]
    group[0]["is_oa"] = False
    merged = merge.merge_group(group)
    assert merged["meta"]["is_oa"] is False


def test_is_oa_null_when_no_openalex_hit():
    group = [
        _mk("crossref", 1, doi="10.1/x"),
        _mk("pubmed", 2, pmid="1"),
    ]
    merged = merge.merge_group(group)
    assert merged["meta"]["is_oa"] is None


def test_is_oa_ignores_non_openalex_source_is_oa():
    """Even if a non-OpenAlex record carries an is_oa field, merge only
    trusts OpenAlex's value (per contract)."""
    group = [
        _mk("crossref", 1, doi="10.1/x"),
    ]
    group[0]["is_oa"] = True  # crossref record wrongly claims OA
    merged = merge.merge_group(group)
    # No OpenAlex source in group → is_oa stays None
    assert merged["meta"]["is_oa"] is None


# --------------------------------------------------------------------------- #
# rank
# --------------------------------------------------------------------------- #


def test_rrf_prefers_multi_source_hit():
    """A hit in 3 sources at rank 5 should beat a rank-1 in only 1 source
    if enough overlap — actually 1/(60+1) = 0.0164 > 3 × 1/(60+5) = 0.0462.
    Wait, let me pick numbers that make the claim true."""
    # 3 sources each at rank 3 → score = 3 * 1/63 = 0.04762
    # 1 source at rank 1        → score = 1/61      = 0.01639
    multi = {
        "identifier": "M",
        "sources_hit": ["a", "b", "c"],
        "rank_by_source": {"a": 3, "b": 3, "c": 3},
        "meta": {"year": 2020, "citation_count": 0},
    }
    single = {
        "identifier": "S",
        "sources_hit": ["a"],
        "rank_by_source": {"a": 1},
        "meta": {"year": 2020, "citation_count": 0},
    }
    out = rank.rank([single, multi], "relevance")
    assert out[0]["identifier"] == "M"
    assert out[0]["index"] == 0


def test_sort_by_year_desc():
    recs = [
        {"identifier": "A", "sources_hit": [], "rank_by_source": {},
         "meta": {"year": 2020}},
        {"identifier": "B", "sources_hit": [], "rank_by_source": {},
         "meta": {"year": 2023}},
        {"identifier": "C", "sources_hit": [], "rank_by_source": {},
         "meta": {"year": None}},
    ]
    out = rank.rank(recs, "year:desc")
    assert [r["identifier"] for r in out[:2]] == ["B", "A"]


def test_sort_by_citations_desc():
    recs = [
        {"identifier": "A", "sources_hit": [], "rank_by_source": {},
         "meta": {"citation_count": 10}},
        {"identifier": "B", "sources_hit": [], "rank_by_source": {},
         "meta": {"citation_count": 100}},
    ]
    out = rank.rank(recs, "citations:desc")
    assert out[0]["identifier"] == "B"
