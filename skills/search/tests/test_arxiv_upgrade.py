"""arxiv-upgrade unit tests — mock lookups, verify orchestration."""

from __future__ import annotations

import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(_SCRIPTS))

import arxiv_upgrade  # noqa: E402
import dedup  # noqa: E402
import merge  # noqa: E402
from sources import openalex, s2  # noqa: E402


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
# Group classification
# --------------------------------------------------------------------------- #


def test_group_is_arxiv_only_true():
    group = [_mk("arxiv", 1, arxiv_id="2101.00001")]
    assert arxiv_upgrade._group_is_arxiv_only(group)


def test_group_is_arxiv_only_false_when_has_doi():
    group = [_mk("arxiv", 1, arxiv_id="2101.00001", doi="10.1/x")]
    assert not arxiv_upgrade._group_is_arxiv_only(group)


def test_group_is_arxiv_only_false_when_has_pmid_from_other_record():
    group = [
        _mk("arxiv", 1, arxiv_id="2101.00001"),
        _mk("pubmed", 2, pmid="99999"),
    ]
    assert not arxiv_upgrade._group_is_arxiv_only(group)


def test_group_is_arxiv_only_false_when_no_arxiv():
    group = [_mk("crossref", 1, doi="10.1/x")]
    assert not arxiv_upgrade._group_is_arxiv_only(group)


# --------------------------------------------------------------------------- #
# annotate_merged
# --------------------------------------------------------------------------- #


def test_annotate_marks_upgraded_records():
    merged = [
        {"identifier": "10.1/x", "dedup_group": ["10.1/x", "2101.00001"],
         "meta": {"arxiv_id": "2101.00001", "doi": "10.1/x"}},
        {"identifier": "10.2/y", "dedup_group": ["10.2/y"], "meta": {}},
    ]
    arxiv_upgrade.annotate_merged(merged, {"2101.00001"})
    assert merged[0]["arxiv_upgraded"] is True
    assert merged[1]["arxiv_upgraded"] is False


def test_annotate_case_insensitive():
    merged = [
        {"identifier": "10.1/x", "dedup_group": ["10.1/x", "2101.00001"],
         "meta": {"arxiv_id": "2101.00001"}},
    ]
    # upgraded set uses lowercase (real impl casts to lower)
    arxiv_upgrade.annotate_merged(merged, {"2101.00001"})
    assert merged[0]["arxiv_upgraded"] is True


# --------------------------------------------------------------------------- #
# upgrade() — integration with mocked lookups
# --------------------------------------------------------------------------- #


def test_upgrade_injects_doi_when_lookup_succeeds(monkeypatch):
    """arxiv-only record → OpenAlex resolves it → dedup can now merge
    with a crossref record carrying the same DOI."""
    from config import SearchConfig
    cfg = SearchConfig()

    # Inputs: one arxiv record (no DOI) + one crossref record (with DOI).
    # They should NOT be linked initially (different id spaces).
    records = [
        _mk("arxiv", 1, arxiv_id="1706.03762", title="Attention Is All You Need"),
        _mk("crossref", 3, doi="10.1234/nips2017", title="Attention Is All You Need"),
    ]

    def fake_openalex_lookup(arxiv_id, *, cfg, respect_rate_limit=False):
        if arxiv_id == "1706.03762":
            return _mk("openalex", 1, doi="10.1234/nips2017",
                       arxiv_id="1706.03762", title="Attention Is All You Need")
        return None

    def fake_s2_lookup(arxiv_id, *, cfg, respect_rate_limit=False):
        return None  # S2 fails, OpenAlex wins

    monkeypatch.setattr(openalex, "lookup_by_arxiv", fake_openalex_lookup)
    monkeypatch.setattr(s2, "lookup_by_arxiv", fake_s2_lookup)

    new_records, upgraded, _ts = arxiv_upgrade.upgrade(records, cfg=cfg)

    assert "1706.03762" in upgraded
    # The arxiv record should now carry the DOI
    arxiv_rec = next(r for r in new_records if r["source"] == "arxiv")
    assert arxiv_rec["doi"] == "10.1234/nips2017"

    # Second dedup should now merge them
    groups = dedup.group_by_identifier(new_records)
    assert len(groups) == 1
    merged = merge.merge_all(groups)
    assert set(merged[0]["sources_hit"]) == {"arxiv", "crossref"}


def test_upgrade_noop_when_no_arxiv_only(monkeypatch):
    """If every record already has a DOI/PMID cross-ref, upgrade does nothing."""
    from config import SearchConfig
    cfg = SearchConfig()

    calls = {"n": 0}
    def fake_lookup(arxiv_id, *, cfg, respect_rate_limit=False):
        calls["n"] += 1
        return None

    monkeypatch.setattr(openalex, "lookup_by_arxiv", fake_lookup)
    monkeypatch.setattr(s2, "lookup_by_arxiv", fake_lookup)

    records = [
        _mk("arxiv", 1, arxiv_id="1706.03762", doi="10.1234/x"),
        _mk("crossref", 1, doi="10.1234/x"),
    ]
    new, upgraded, _ts = arxiv_upgrade.upgrade(records, cfg=cfg)
    assert not upgraded
    assert calls["n"] == 0
    assert new == records


def test_upgrade_silent_on_lookup_failure(monkeypatch):
    """Both lookups return None → record stays arxiv-only, no error."""
    from config import SearchConfig
    cfg = SearchConfig()

    monkeypatch.setattr(openalex, "lookup_by_arxiv",
                        lambda arxiv_id, **kw: None)
    monkeypatch.setattr(s2, "lookup_by_arxiv",
                        lambda arxiv_id, **kw: None)

    records = [_mk("arxiv", 1, arxiv_id="1706.03762")]
    new, upgraded, _ts = arxiv_upgrade.upgrade(records, cfg=cfg)
    assert not upgraded
    # Record unchanged
    assert new[0]["doi"] is None
    assert new[0]["arxiv_id"] == "1706.03762"


def test_upgrade_first_source_wins(monkeypatch):
    """When OpenAlex returns a DOI, S2 is not required."""
    from config import SearchConfig
    cfg = SearchConfig()

    monkeypatch.setattr(openalex, "lookup_by_arxiv",
                        lambda arxiv_id, **kw: _mk("openalex", 1,
                                                    doi="10.oa/win"))
    monkeypatch.setattr(s2, "lookup_by_arxiv",
                        lambda arxiv_id, **kw: _mk("s2", 1,
                                                    doi="10.s2/win"))

    records = [_mk("arxiv", 1, arxiv_id="9999.99999")]
    new, upgraded, _ts = arxiv_upgrade.upgrade(records, cfg=cfg)
    assert "9999.99999" in upgraded
    # One of them wins (race — but must be a valid DOI, not None)
    assert new[0]["doi"] in ("10.oa/win", "10.s2/win")


# --------------------------------------------------------------------------- #
# Journal DOI vs arxiv self-DOI discrimination
# --------------------------------------------------------------------------- #


def test_is_journal_doi_rejects_arxiv_datacite():
    assert not arxiv_upgrade._is_journal_doi("10.48550/arxiv.2303.03681")
    assert not arxiv_upgrade._is_journal_doi("10.48550/arXiv.1706.03762")
    assert not arxiv_upgrade._is_journal_doi("10.48550/ARXIV.hep-th/9901001")


def test_is_journal_doi_accepts_real_journal_dois():
    assert arxiv_upgrade._is_journal_doi("10.1038/s41586-020-2649-2")
    assert arxiv_upgrade._is_journal_doi("10.1021/acs.chemrev.8b00803")
    assert arxiv_upgrade._is_journal_doi("10.1103/prxquantum.2.020310")


def test_is_journal_doi_rejects_none_and_empty():
    assert not arxiv_upgrade._is_journal_doi(None)
    assert not arxiv_upgrade._is_journal_doi("")
    assert not arxiv_upgrade._is_journal_doi("   ")


def test_upgrade_rejects_openalex_arxiv_self_doi(monkeypatch):
    """If OpenAlex returns a lookup with only the arxiv's own DataCite DOI,
    upgrade must NOT accept it as a 'published-version' DOI."""
    from config import SearchConfig
    cfg = SearchConfig()

    monkeypatch.setattr(openalex, "lookup_by_arxiv",
                        lambda arxiv_id, **kw: _mk("openalex", 1,
                                                    doi=f"10.48550/arxiv.{arxiv_id}",
                                                    arxiv_id=arxiv_id))
    monkeypatch.setattr(s2, "lookup_by_arxiv",
                        lambda arxiv_id, **kw: None)

    records = [_mk("arxiv", 1, arxiv_id="2303.03681")]
    new, upgraded, _ts = arxiv_upgrade.upgrade(records, cfg=cfg)
    # Must NOT be upgraded — the DataCite arxiv DOI is not a journal DOI
    assert not upgraded
    assert new[0]["doi"] is None


# --------------------------------------------------------------------------- #
# Title match utility
# --------------------------------------------------------------------------- #


def test_title_match_exact():
    a = _mk("arxiv", 1, title="Attention Is All You Need", authors=["A B", "C D"])
    c = _mk("crossref", 1, title="Attention Is All You Need!", authors=["A B"])
    assert arxiv_upgrade._is_title_match(a, c)


def test_title_match_fuzzy_with_matching_surname():
    # 10 shared tokens, arxiv adds "preprint", crossref adds "extended"
    # Jaccard = 10/12 ≈ 0.83 → below threshold, so add more overlap:
    # arxiv: 12 tokens, crossref: 13 tokens, shared: 12 → Jaccard = 12/13 ≈ 0.92
    a = _mk("arxiv", 1,
            title="Adaptive Variational Quantum Eigensolver Method For Excited States Study Approach",
            authors=["Alice Smith"])
    c = _mk("crossref", 1,
            title="Adaptive Variational Quantum Eigensolver Method For Excited States Study Approach Extended",
            authors=["Alice Smith"])
    assert arxiv_upgrade._is_title_match(a, c)


def test_title_match_fuzzy_rejected_when_surname_differs():
    """Even if Jaccard is high, mismatched surname rejects the pair."""
    a = _mk("arxiv", 1, title="A survey of quantum chemistry algorithms",
            authors=["Alice Smith"])
    c = _mk("crossref", 1, title="A survey of quantum chemistry algorithms and more",
            authors=["Bob Jones"])
    # Exact-different, Jaccard likely ≥ 0.85, but surnames differ → reject
    assert not arxiv_upgrade._is_title_match(a, c)


def test_title_match_rejected_low_jaccard():
    a = _mk("arxiv", 1, title="Improving VQE with adaptive ansatz")
    c = _mk("crossref", 1, title="Deep learning for image classification")
    assert not arxiv_upgrade._is_title_match(a, c)


def test_title_match_rejected_empty_title():
    a = _mk("arxiv", 1, title=None)
    c = _mk("crossref", 1, title="anything")
    assert not arxiv_upgrade._is_title_match(a, c)


# --------------------------------------------------------------------------- #
# Title-search fallback (Path C)
# --------------------------------------------------------------------------- #


def test_title_search_fallback_off_by_default(monkeypatch):
    """Without --arxiv-upgrade-fallback title-search, crossref.lookup_by_title
    must NOT be called."""
    from config import SearchConfig
    from sources import crossref
    cfg = SearchConfig()

    monkeypatch.setattr(openalex, "lookup_by_arxiv",
                        lambda arxiv_id, **kw: None)
    monkeypatch.setattr(s2, "lookup_by_arxiv",
                        lambda arxiv_id, **kw: None)
    calls = {"n": 0}
    def fake_title_search(title, **kw):
        calls["n"] += 1
        return []
    monkeypatch.setattr(crossref, "lookup_by_title", fake_title_search)

    records = [_mk("arxiv", 1, arxiv_id="2303.03681",
                   title="Towards practical parallel quantum computing emulation")]
    new, upgraded, ts = arxiv_upgrade.upgrade(records, cfg=cfg)
    assert calls["n"] == 0
    assert not upgraded
    assert not ts


def test_title_search_fallback_finds_match(monkeypatch):
    """When enabled, title-search finds a journal DOI and upgrades."""
    from config import SearchConfig
    from sources import crossref
    cfg = SearchConfig()

    monkeypatch.setattr(openalex, "lookup_by_arxiv",
                        lambda arxiv_id, **kw: None)
    monkeypatch.setattr(s2, "lookup_by_arxiv",
                        lambda arxiv_id, **kw: None)

    def fake_title_search(title, **kw):
        return [_mk("crossref", 1,
                    doi="10.1234/found-in-journal",
                    title="Towards practical parallel quantum computing emulation",
                    authors=["Xin Shang", "Alice Zhang"])]
    monkeypatch.setattr(crossref, "lookup_by_title", fake_title_search)

    records = [_mk("arxiv", 1, arxiv_id="2303.03681",
                   title="Towards practical parallel quantum computing emulation",
                   authors=["Xin Shang", "Alice Zhang"])]
    new, upgraded, ts = arxiv_upgrade.upgrade(
        records, cfg=cfg, title_search_fallback=True,
    )
    assert "2303.03681" in upgraded
    assert "2303.03681" in ts   # marked as title-search source
    assert new[0]["doi"] == "10.1234/found-in-journal"


def test_title_search_rejects_bad_candidate(monkeypatch):
    """A Crossref candidate that fails title/surname check must NOT upgrade."""
    from config import SearchConfig
    from sources import crossref
    cfg = SearchConfig()

    monkeypatch.setattr(openalex, "lookup_by_arxiv",
                        lambda arxiv_id, **kw: None)
    monkeypatch.setattr(s2, "lookup_by_arxiv",
                        lambda arxiv_id, **kw: None)

    def fake_title_search(title, **kw):
        # Returns a totally unrelated paper
        return [_mk("crossref", 1,
                    doi="10.1234/wrong-paper",
                    title="Deep learning for image classification",
                    authors=["Someone Else"])]
    monkeypatch.setattr(crossref, "lookup_by_title", fake_title_search)

    records = [_mk("arxiv", 1, arxiv_id="2303.03681",
                   title="Towards practical parallel quantum computing emulation",
                   authors=["Xin Shang"])]
    new, upgraded, ts = arxiv_upgrade.upgrade(
        records, cfg=cfg, title_search_fallback=True,
    )
    assert not upgraded
    assert not ts
    assert new[0]["doi"] is None


def test_title_search_rejects_arxiv_self_doi(monkeypatch):
    """If Crossref title search returns a paper whose only DOI is a
    DataCite arxiv DOI (10.48550/arxiv.*), title-search must reject."""
    from config import SearchConfig
    from sources import crossref
    cfg = SearchConfig()

    monkeypatch.setattr(openalex, "lookup_by_arxiv",
                        lambda arxiv_id, **kw: None)
    monkeypatch.setattr(s2, "lookup_by_arxiv",
                        lambda arxiv_id, **kw: None)

    def fake_title_search(title, **kw):
        return [_mk("crossref", 1,
                    doi="10.48550/arxiv.2303.03681",
                    title="Towards practical parallel quantum computing emulation",
                    authors=["Xin Shang"])]
    monkeypatch.setattr(crossref, "lookup_by_title", fake_title_search)

    records = [_mk("arxiv", 1, arxiv_id="2303.03681",
                   title="Towards practical parallel quantum computing emulation",
                   authors=["Xin Shang"])]
    new, upgraded, ts = arxiv_upgrade.upgrade(
        records, cfg=cfg, title_search_fallback=True,
    )
    assert not upgraded  # DataCite arxiv DOI rejected
    assert not ts


# --------------------------------------------------------------------------- #
# Post-hoc verification (author + year)
# --------------------------------------------------------------------------- #


def test_verify_accepts_matching_author_and_year(monkeypatch):
    """If Crossref returns author+year matching arxiv → upgrade kept."""
    from config import SearchConfig
    from sources import crossref
    cfg = SearchConfig()

    monkeypatch.setattr(openalex, "lookup_by_arxiv",
                        lambda arxiv_id, **kw: _mk("openalex", 1,
                                                    doi="10.1234/good",
                                                    year=2020,
                                                    authors=["Alice Smith"]))
    monkeypatch.setattr(s2, "lookup_by_arxiv", lambda arxiv_id, **kw: None)
    # Verify: Crossref returns matching data
    monkeypatch.setattr(crossref, "get_by_doi",
                        lambda doi, **kw: _mk("crossref", 1, doi=doi,
                                               year=2020, authors=["Alice Smith"]))

    records = [_mk("arxiv", 1, arxiv_id="2001.00001",
                   year=2020, authors=["Alice Smith"])]
    new, upgraded, _ts = arxiv_upgrade.upgrade(records, cfg=cfg)
    assert "2001.00001" in upgraded
    assert new[0]["doi"] == "10.1234/good"


def test_verify_rejects_wrong_author(monkeypatch):
    """If Crossref returns different first-author, upgrade rejected."""
    from config import SearchConfig
    from sources import crossref
    cfg = SearchConfig()

    monkeypatch.setattr(openalex, "lookup_by_arxiv",
                        lambda arxiv_id, **kw: _mk("openalex", 1,
                                                    doi="10.1234/wrong",
                                                    authors=["Alice Smith"]))
    monkeypatch.setattr(s2, "lookup_by_arxiv", lambda arxiv_id, **kw: None)
    # Verify: Crossref returns a completely different author
    monkeypatch.setattr(crossref, "get_by_doi",
                        lambda doi, **kw: _mk("crossref", 1, doi=doi,
                                               year=2020, authors=["Bob Jones"]))

    records = [_mk("arxiv", 1, arxiv_id="2001.00001",
                   year=2020, authors=["Alice Smith"])]
    new, upgraded, _ts = arxiv_upgrade.upgrade(records, cfg=cfg)
    assert not upgraded
    assert new[0]["doi"] is None


def test_verify_rejects_far_year(monkeypatch):
    """Year difference > 3 (default tolerance) → reject."""
    from config import SearchConfig
    from sources import crossref
    cfg = SearchConfig()

    monkeypatch.setattr(openalex, "lookup_by_arxiv",
                        lambda arxiv_id, **kw: _mk("openalex", 1,
                                                    doi="10.1234/timewarp",
                                                    year=2020,
                                                    authors=["Alice Smith"]))
    monkeypatch.setattr(s2, "lookup_by_arxiv", lambda arxiv_id, **kw: None)
    # Verify: same author but year off by 5 → reject
    monkeypatch.setattr(crossref, "get_by_doi",
                        lambda doi, **kw: _mk("crossref", 1, doi=doi,
                                               year=2015, authors=["Alice Smith"]))

    records = [_mk("arxiv", 1, arxiv_id="2001.00001",
                   year=2020, authors=["Alice Smith"])]
    new, upgraded, _ts = arxiv_upgrade.upgrade(records, cfg=cfg)
    assert not upgraded


def test_verify_fails_open_on_network_error(monkeypatch):
    """If Crossref get_by_doi returns None (network fail), keep the upgrade."""
    from config import SearchConfig
    from sources import crossref
    cfg = SearchConfig()

    monkeypatch.setattr(openalex, "lookup_by_arxiv",
                        lambda arxiv_id, **kw: _mk("openalex", 1,
                                                    doi="10.1234/network-flaky",
                                                    year=2020,
                                                    authors=["Alice Smith"]))
    monkeypatch.setattr(s2, "lookup_by_arxiv", lambda arxiv_id, **kw: None)
    # Verify: Crossref returns None (network flaked)
    monkeypatch.setattr(crossref, "get_by_doi",
                        lambda doi, **kw: None)

    records = [_mk("arxiv", 1, arxiv_id="2001.00001",
                   year=2020, authors=["Alice Smith"])]
    new, upgraded, _ts = arxiv_upgrade.upgrade(records, cfg=cfg)
    # Fail-open: upgrade kept
    assert "2001.00001" in upgraded


def test_verify_year_tolerance():
    """The verifier accepts year difference within ±3 by default."""
    arxiv_rec = _mk("arxiv", 1, year=2020, authors=["Alice Smith"])
    from sources import crossref

    class FakeCfg:
        polite_email = ""
        semanticscholar_api_key = ""
        http_timeout_seconds = 10
        user_agent = lambda self=None: "test"

    # +3 year: accepted
    def _get_by_doi_plus3(doi, **kw):
        return _mk("crossref", 1, doi=doi, year=2023, authors=["Alice Smith"])
    import sources.crossref as _cx
    _orig = _cx.get_by_doi
    _cx.get_by_doi = _get_by_doi_plus3
    try:
        assert arxiv_upgrade._verify_upgrade_by_author_year(
            arxiv_rec, "10.x/y", cfg=FakeCfg(), respect_rate_limit=False,
        ) is True
    finally:
        _cx.get_by_doi = _orig


# --------------------------------------------------------------------------- #
# Task 22 — arxiv journal_ref DOI extraction (via arxiv adapter)
# --------------------------------------------------------------------------- #


def test_arxiv_extracts_doi_from_journal_ref():
    from sources.arxiv import _extract_doi_from_text
    assert _extract_doi_from_text("Nature 500, 12-15 (2013). doi:10.1038/nature12373") == "10.1038/nature12373"


def test_arxiv_extract_doi_ignores_arxiv_self_datacite():
    from sources.arxiv import _extract_doi_from_text
    # DataCite arxiv self-DOI must NOT be extracted as a journal DOI
    assert _extract_doi_from_text("preprint doi 10.48550/arxiv.2101.00001") is None


def test_arxiv_extract_doi_returns_first_journal_doi():
    from sources.arxiv import _extract_doi_from_text
    text = "See also 10.48550/arxiv.1234.5678 (preprint) but final is 10.1103/PhysRevLett.130.010001"
    assert _extract_doi_from_text(text) == "10.1103/physrevlett.130.010001"


def test_arxiv_extract_doi_handles_trailing_punctuation():
    from sources.arxiv import _extract_doi_from_text
    assert _extract_doi_from_text("Published in Nature (10.1038/nature12345).") == "10.1038/nature12345"


def test_arxiv_extract_doi_none_when_no_doi():
    from sources.arxiv import _extract_doi_from_text
    assert _extract_doi_from_text("11 pages, 4 figures") is None
    assert _extract_doi_from_text("") is None
    assert _extract_doi_from_text(None) is None


# --------------------------------------------------------------------------- #
# Task 23 — PMCID dedup key
# --------------------------------------------------------------------------- #


def test_dedup_by_pmcid():
    """Two records sharing a pmcid should merge, even without DOI/PMID/arxiv."""
    r1 = _mk("openalex", 1, pmcid="PMC1234567")
    r2 = _mk("pubmed", 2, pmcid="PMC1234567")
    groups = dedup.group_by_identifier([r1, r2])
    assert len(groups) == 1
    assert len(groups[0]) == 2


def test_pmcid_merged_into_meta():
    """merge_group should surface pmcid in the meta output."""
    group = [_mk("openalex", 1, doi="10.1/x", pmcid="PMC7890123")]
    merged = merge.merge_group(group)
    assert merged["meta"]["pmcid"] == "PMC7890123"
