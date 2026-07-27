"""Tests for citations.py orchestrator — pure offline via monkeypatch."""

from __future__ import annotations

import io
from types import SimpleNamespace

import pytest

import citations
from config import SearchConfig
from seed_resolver import SeedId
from sources import crossref, openalex, pubmed, s2


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _rec(source: str, **fields) -> dict:
    """Build a minimal source-format record."""
    base = {
        "source": source,
        "doi": None, "pmid": None, "arxiv_id": None,
        "openalex_id": None, "s2_id": None, "pmcid": None,
        "title": None, "authors": [], "year": None,
        "journal": None, "volume": None, "issue": None,
        "pages": None, "abstract": None, "citation_count": None,
        "type": None, "url": None,
    }
    base.update(fields)
    return base


def _args(seed, direction="refs", **overrides):
    d = {
        "seed": seed,
        "top": None,
        "all": False,
        "sort": "citations:desc",
        "sources": None,
        "no_fetch_meta": True,  # avoid batch-fill HTTP in tests
        "format": "ndjson",
        "out": "-",
        "out_unresolved": None,
    }
    d.update(overrides)
    return SimpleNamespace(**d)


@pytest.fixture
def cfg():
    return SearchConfig()


@pytest.fixture
def stub_all(monkeypatch):
    """Default: every fetcher returns []. Individual tests override."""
    for mod in (openalex, s2, crossref, pubmed):
        pass
    monkeypatch.setattr(openalex, "get_referenced_works", lambda *a, **kw: [])
    monkeypatch.setattr(openalex, "get_cited_by", lambda *a, **kw: [])
    monkeypatch.setattr(openalex, "get_works_batch", lambda *a, **kw: [])
    monkeypatch.setattr(s2, "get_references", lambda *a, **kw: [])
    monkeypatch.setattr(s2, "get_citations", lambda *a, **kw: [])
    monkeypatch.setattr(crossref, "get_references", lambda *a, **kw: [])
    monkeypatch.setattr(pubmed, "get_refs_elink", lambda *a, **kw: [])
    monkeypatch.setattr(pubmed, "get_citedin_elink", lambda *a, **kw: [])
    monkeypatch.setattr(pubmed, "fetch_by_pmids", lambda *a, **kw: {})


# --------------------------------------------------------------------------- #
# Bad seed → exit 2
# --------------------------------------------------------------------------- #


class TestBadSeed:
    def test_gibberish_returns_exit_2(self, capsys, cfg, stub_all):
        rc = citations.cmd_refs(_args("not-an-id"), cfg)
        assert rc == 2
        err = capsys.readouterr().err
        assert "unrecognized seed id" in err


# --------------------------------------------------------------------------- #
# Empty across sources: exit 3 (seed missing)
# --------------------------------------------------------------------------- #


class TestSeedNotFound:
    def test_all_sources_return_empty_no_failures_is_exit_3(self, capsys, cfg, stub_all):
        rc = citations.cmd_refs(_args("10.9999/does-not-exist"), cfg)
        assert rc == 3


# --------------------------------------------------------------------------- #
# Happy path — refs with OpenAlex hits, resolved output shape
# --------------------------------------------------------------------------- #


class TestRefsHappyPath:
    def test_single_openalex_ref_becomes_one_resolved(self, monkeypatch, capsys, cfg):
        monkeypatch.setattr(openalex, "get_referenced_works",
            lambda *a, **kw: [_rec("openalex", openalex_id="W1", title="Ref One",
                                   doi="10.1/ref-one", year=2020,
                                   authors=["Alice"], citation_count=5)])
        monkeypatch.setattr(s2, "get_references", lambda *a, **kw: [])
        monkeypatch.setattr(crossref, "get_references", lambda *a, **kw: [])
        monkeypatch.setattr(pubmed, "get_refs_elink", lambda *a, **kw: [])
        monkeypatch.setattr(pubmed, "fetch_by_pmids", lambda *a, **kw: {})

        rc = citations.cmd_refs(_args("10.1038/nature12373"), cfg)
        assert rc == 0
        out = capsys.readouterr().out
        lines = [ln for ln in out.splitlines() if ln.strip()]
        # 1 record + 1 summary
        assert len(lines) == 2
        import json as _json
        rec = _json.loads(lines[0])
        assert rec["direction"] == "refs"
        assert rec["seed"] == "10.1038/nature12373"
        assert rec["identifier"] == "10.1/ref-one"
        assert rec["meta"]["title"] == "Ref One"
        assert "rank_by_source" not in rec
        assert "dedup_group" not in rec
        summary = _json.loads(lines[1])["summary"]
        assert summary["direction"] == "refs"
        assert summary["resolved"] == 1
        assert summary["unresolved"] == 0

    def test_multi_source_β_dedup_by_doi(self, monkeypatch, capsys, cfg):
        # Same DOI appears from openalex + s2 → one resolved record
        rec_oa = _rec("openalex", doi="10.1/dup", openalex_id="W7", title="Dup", year=2019)
        rec_s2 = _rec("s2",       doi="10.1/dup", s2_id="abc", title=None, year=2019)
        monkeypatch.setattr(openalex, "get_referenced_works", lambda *a, **kw: [rec_oa])
        monkeypatch.setattr(s2, "get_references", lambda *a, **kw: [rec_s2])
        monkeypatch.setattr(crossref, "get_references", lambda *a, **kw: [])
        monkeypatch.setattr(pubmed, "get_refs_elink", lambda *a, **kw: [])
        monkeypatch.setattr(pubmed, "fetch_by_pmids", lambda *a, **kw: {})

        rc = citations.cmd_refs(_args("10.1038/x"), cfg)
        assert rc == 0
        out = capsys.readouterr().out
        import json as _json
        recs = [_json.loads(ln) for ln in out.splitlines() if ln.strip() and "summary" not in ln]
        assert len(recs) == 1
        rec = recs[0]
        assert rec["identifier"] == "10.1/dup"
        # sources_hit union
        assert set(rec["sources_hit"]) == {"openalex", "s2"}


# --------------------------------------------------------------------------- #
# Unresolved split: Crossref refs without DOI go to --out-unresolved
# --------------------------------------------------------------------------- #


class TestUnresolved:
    def test_crossref_raw_citation_goes_to_unresolved(self, monkeypatch, tmp_path, capsys, cfg):
        monkeypatch.setattr(openalex, "get_referenced_works", lambda *a, **kw: [])
        monkeypatch.setattr(s2, "get_references", lambda *a, **kw: [])
        raw_only = _rec("crossref")
        raw_only["raw_citation"] = "J. Smith, Chem. Rev., 2019, 119, 5..."
        monkeypatch.setattr(crossref, "get_references", lambda *a, **kw: [raw_only])
        monkeypatch.setattr(pubmed, "get_refs_elink", lambda *a, **kw: [])
        monkeypatch.setattr(pubmed, "fetch_by_pmids", lambda *a, **kw: {})

        unresolved_path = tmp_path / "unresolved.ndjson"
        rc = citations.cmd_refs(_args("10.1038/x", out_unresolved=str(unresolved_path)), cfg)
        assert rc == 0

        # Main stream: empty of records, only summary
        out = capsys.readouterr().out
        import json as _json
        main_lines = [ln for ln in out.splitlines() if ln.strip()]
        # 0 records + 1 summary
        assert len(main_lines) == 1
        summary = _json.loads(main_lines[0])["summary"]
        assert summary["resolved"] == 0
        assert summary["unresolved"] == 1

        # Unresolved file has the raw_citation record
        assert unresolved_path.exists()
        u_lines = unresolved_path.read_text(encoding="utf-8").splitlines()
        u = _json.loads(u_lines[0])
        assert u["raw_citation"].startswith("J. Smith")
        assert u["direction"] == "refs"


# --------------------------------------------------------------------------- #
# cited-by: --top default 100 (via args), --all bypass, --sort direction
# --------------------------------------------------------------------------- #


class TestCitedBy:
    def test_default_top_from_args(self, monkeypatch, capsys, cfg):
        # 3 records — the cmd itself does not apply defaults; main.py does.
        # But when args.top is None and args.all is False for cited-by, our
        # orchestrator falls back to 100 internally.
        recs = [_rec("openalex", doi=f"10.1/{i}", openalex_id=f"W{i}",
                     title=f"P{i}", citation_count=i, year=2020) for i in range(5)]
        monkeypatch.setattr(openalex, "get_cited_by", lambda *a, **kw: recs)
        monkeypatch.setattr(s2, "get_citations", lambda *a, **kw: [])
        monkeypatch.setattr(pubmed, "get_citedin_elink", lambda *a, **kw: [])
        monkeypatch.setattr(pubmed, "fetch_by_pmids", lambda *a, **kw: {})

        rc = citations.cmd_cited_by(_args("10.1038/x", top=None, all=False), cfg)
        assert rc == 0
        out = capsys.readouterr().out
        import json as _json
        r = [_json.loads(ln) for ln in out.splitlines()
             if ln.strip() and "summary" not in ln]
        assert len(r) == 5  # all fit under default 100

    def test_top_slice(self, monkeypatch, capsys, cfg):
        recs = [_rec("openalex", doi=f"10.1/{i}", openalex_id=f"W{i}",
                     title=f"P{i}", citation_count=100 - i, year=2020) for i in range(10)]
        monkeypatch.setattr(openalex, "get_cited_by", lambda *a, **kw: recs)
        monkeypatch.setattr(s2, "get_citations", lambda *a, **kw: [])
        monkeypatch.setattr(pubmed, "get_citedin_elink", lambda *a, **kw: [])
        monkeypatch.setattr(pubmed, "fetch_by_pmids", lambda *a, **kw: {})

        rc = citations.cmd_cited_by(_args("10.1038/x", top=3), cfg)
        assert rc == 0
        out = capsys.readouterr().out
        import json as _json
        r = [_json.loads(ln) for ln in out.splitlines()
             if ln.strip() and "summary" not in ln]
        assert len(r) == 3
        # Sorted by citation_count desc
        assert r[0]["meta"]["citation_count"] >= r[1]["meta"]["citation_count"] >= r[2]["meta"]["citation_count"]

    def test_sort_year_desc(self, monkeypatch, capsys, cfg):
        recs = [_rec("openalex", doi=f"10.1/{i}", openalex_id=f"W{i}",
                     title=f"P{i}", citation_count=1, year=2010 + i) for i in range(4)]
        monkeypatch.setattr(openalex, "get_cited_by", lambda *a, **kw: recs)
        monkeypatch.setattr(s2, "get_citations", lambda *a, **kw: [])
        monkeypatch.setattr(pubmed, "get_citedin_elink", lambda *a, **kw: [])
        monkeypatch.setattr(pubmed, "fetch_by_pmids", lambda *a, **kw: {})

        rc = citations.cmd_cited_by(_args("10.1038/x", sort="year:desc"), cfg)
        assert rc == 0
        out = capsys.readouterr().out
        import json as _json
        r = [_json.loads(ln) for ln in out.splitlines()
             if ln.strip() and "summary" not in ln]
        years = [x["meta"]["year"] for x in r]
        assert years == sorted(years, reverse=True)


# --------------------------------------------------------------------------- #
# Exit 5: every source raises
# --------------------------------------------------------------------------- #


class TestAllSourcesFail:
    def test_exit_5(self, monkeypatch, capsys, cfg):
        def _raise(*a, **kw):
            raise RuntimeError("boom")
        monkeypatch.setattr(openalex, "get_referenced_works", _raise)
        monkeypatch.setattr(s2, "get_references", _raise)
        monkeypatch.setattr(crossref, "get_references", _raise)
        # Restrict sources so pubmed isn't in the pool (DOI seed → pubmed
        # returns [] without calling elink, which would leave `n_ok_sources > 0`)
        rc = citations.cmd_refs(
            _args("10.1038/x", sources="openalex,s2,crossref"), cfg)
        assert rc == 5
        err = capsys.readouterr().err
        assert "all citation sources failed" in err


# --------------------------------------------------------------------------- #
# Flag conflict: --top + --all
# --------------------------------------------------------------------------- #


class TestFlagConflicts:
    def test_cited_by_top_and_all_raises_exit_2(self, capsys, cfg):
        def _noop(*a, **kw):
            return []
        # Even with stubs, the flag check fires before any fetcher is called.
        rc = citations.cmd_cited_by(
            _args("10.1038/nature12373", top=10, all=True), cfg)
        assert rc == 2
        err = capsys.readouterr().err
        assert "mutually exclusive" in err
