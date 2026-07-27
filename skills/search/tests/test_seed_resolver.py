"""Tests for seed_resolver.classify — pure offline, monkeypatches subprocess.run
for the sciforge URI branch."""

from __future__ import annotations

import json
import subprocess
from types import SimpleNamespace

import pytest

import seed_resolver as sr


# --------------------------------------------------------------------------- #
# External-ID classification (no subprocess involved)
# --------------------------------------------------------------------------- #


class TestClassifyExternal:
    def test_doi_plain(self) -> None:
        s = sr.classify("10.1038/s41586-020-2649-2")
        assert s.kind == "doi"
        assert s.primary == "10.1038/s41586-020-2649-2"
        assert s.ids == {"doi": "10.1038/s41586-020-2649-2"}

    def test_doi_lowercases(self) -> None:
        s = sr.classify("10.1109/CVPR.2019.00297")
        assert s.kind == "doi"
        assert s.primary == "10.1109/cvpr.2019.00297"

    def test_doi_trims_whitespace(self) -> None:
        s = sr.classify("   10.1038/nature12373  ")
        assert s.kind == "doi"
        assert s.primary == "10.1038/nature12373"

    def test_arxiv_self_doi_downgraded_to_arxiv(self) -> None:
        s = sr.classify("10.48550/arXiv.1706.03762")
        assert s.kind == "arxiv"
        assert s.primary == "1706.03762"
        assert s.ids == {"arxiv_id": "1706.03762"}

    def test_arxiv_self_doi_oldstyle(self) -> None:
        s = sr.classify("10.48550/arxiv.hep-th/9901001")
        assert s.kind == "arxiv"
        assert s.primary == "hep-th/9901001"

    def test_arxiv_modern(self) -> None:
        s = sr.classify("1706.03762")
        assert s.kind == "arxiv"
        assert s.primary == "1706.03762"
        assert s.ids == {"arxiv_id": "1706.03762"}

    def test_arxiv_modern_versioned(self) -> None:
        s = sr.classify("2101.00001v3")
        assert s.kind == "arxiv"
        assert s.primary == "2101.00001"  # v3 stripped

    def test_arxiv_old_style(self) -> None:
        s = sr.classify("hep-th/9901001")
        assert s.kind == "arxiv"
        assert s.primary == "hep-th/9901001"

    def test_pmid_8_digit(self) -> None:
        s = sr.classify("32939066")
        assert s.kind == "pmid"
        assert s.primary == "32939066"
        assert s.ids == {"pmid": "32939066"}

    def test_pmid_7_digit(self) -> None:
        s = sr.classify("1234567")
        assert s.kind == "pmid"
        assert s.primary == "1234567"

    def test_openalex_wid(self) -> None:
        s = sr.classify("W3082521543")
        assert s.kind == "openalex"
        assert s.primary == "W3082521543"
        assert s.ids == {"openalex_id": "W3082521543"}

    def test_openalex_wid_lowercased_input_becomes_upper(self) -> None:
        s = sr.classify("w3082521543")
        assert s.kind == "openalex"
        assert s.primary == "W3082521543"

    def test_s2_sha1(self) -> None:
        sid = "204e3073870fae3d05bcbc2f6a8e263d9b72e776"
        s = sr.classify(sid)
        assert s.kind == "s2"
        assert s.primary == sid
        assert s.ids == {"s2_id": sid}

    def test_s2_sha1_uppercase(self) -> None:
        sid_upper = "204E3073870FAE3D05BCBC2F6A8E263D9B72E776"
        s = sr.classify(sid_upper)
        assert s.kind == "s2"
        assert s.primary == sid_upper.lower()


class TestClassifyRejects:
    def test_empty(self) -> None:
        with pytest.raises(sr.SeedError):
            sr.classify("")

    def test_whitespace_only(self) -> None:
        with pytest.raises(sr.SeedError):
            sr.classify("   ")

    def test_gibberish(self) -> None:
        with pytest.raises(sr.SeedError):
            sr.classify("not-an-id")

    def test_url(self) -> None:
        # Full URL forms are NOT accepted — caller must strip.
        with pytest.raises(sr.SeedError):
            sr.classify("https://doi.org/10.1038/nature12373")

    def test_empty_citekey_in_uri(self) -> None:
        with pytest.raises(sr.SeedError):
            sr.classify("sciforge://literature/")


# --------------------------------------------------------------------------- #
# sciforge URI resolution — subprocess.run monkeypatched
# --------------------------------------------------------------------------- #


def _fake_run(stdout: str = "", stderr: str = "", returncode: int = 0):
    def _runner(*args, **kwargs):
        return SimpleNamespace(stdout=stdout, stderr=stderr, returncode=returncode)
    return _runner


class TestSciforgeURI:
    def test_resolves_via_doi(self, monkeypatch: pytest.MonkeyPatch) -> None:
        stdout = json.dumps({
            "id": "vaswani2017attention",
            "type": "paper",
            "uri": "sciforge://literature/vaswani2017attention",
            "doi": "10.48550/arXiv.1706.03762",  # sf-lit stores the arxiv DOI here
            "arxiv_id": "1706.03762",
            "s2_paper_id": None,
        })
        monkeypatch.setattr(subprocess, "run", _fake_run(stdout=stdout))
        s = sr.classify("sciforge://literature/vaswani2017attention")
        # DOI takes priority even when it's the arxiv self-DOI —
        # classification is by the metadata, not by identifier reinterpretation.
        assert s.kind == "doi"
        assert s.primary == "10.48550/arxiv.1706.03762"
        assert s.ids["doi"] == "10.48550/arxiv.1706.03762"
        assert s.ids["arxiv_id"] == "1706.03762"

    def test_resolves_via_arxiv_when_no_doi(self, monkeypatch: pytest.MonkeyPatch) -> None:
        stdout = json.dumps({
            "doi": None,
            "arxiv_id": "1706.03762",
            "s2_paper_id": None,
        })
        monkeypatch.setattr(subprocess, "run", _fake_run(stdout=stdout))
        s = sr.classify("sciforge://literature/foo")
        assert s.kind == "arxiv"
        assert s.primary == "1706.03762"
        assert s.ids == {"arxiv_id": "1706.03762"}

    def test_resolves_via_s2_when_no_doi_no_arxiv(self, monkeypatch: pytest.MonkeyPatch) -> None:
        stdout = json.dumps({
            "doi": None, "arxiv_id": None,
            "s2_paper_id": "204e3073870fae3d05bcbc2f6a8e263d9b72e776",
        })
        monkeypatch.setattr(subprocess, "run", _fake_run(stdout=stdout))
        s = sr.classify("sciforge://literature/foo")
        assert s.kind == "s2"
        assert s.primary == "204e3073870fae3d05bcbc2f6a8e263d9b72e776"

    def test_no_external_ids_raises_resolve_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        stdout = json.dumps({"doi": None, "arxiv_id": None, "s2_paper_id": None})
        monkeypatch.setattr(subprocess, "run", _fake_run(stdout=stdout))
        with pytest.raises(sr.SeedResolveError, match="no doi"):
            sr.classify("sciforge://literature/foo")

    def test_sf_lit_not_on_path(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def _raise(*a, **kw):
            raise FileNotFoundError(2, "not found")
        monkeypatch.setattr(subprocess, "run", _raise)
        with pytest.raises(sr.SeedResolveError, match="sf-lit not on PATH"):
            sr.classify("sciforge://literature/foo")

    def test_sf_lit_returns_nonzero(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            subprocess, "run",
            _fake_run(stderr="error: no paper matches 'foo'", returncode=3),
        )
        with pytest.raises(sr.SeedResolveError, match="no paper matches"):
            sr.classify("sciforge://literature/foo")

    def test_sf_lit_returns_garbage(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(subprocess, "run", _fake_run(stdout="not json"))
        with pytest.raises(sr.SeedResolveError, match="not emit valid JSON"):
            sr.classify("sciforge://literature/foo")

    def test_sf_lit_returns_non_object(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(subprocess, "run", _fake_run(stdout="[]"))
        with pytest.raises(sr.SeedResolveError, match="not an object"):
            sr.classify("sciforge://literature/foo")

    def test_sf_lit_timeout(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def _raise(*a, **kw):
            raise subprocess.TimeoutExpired(cmd="sf-lit", timeout=30)
        monkeypatch.setattr(subprocess, "run", _raise)
        with pytest.raises(sr.SeedResolveError, match="timed out"):
            sr.classify("sciforge://literature/foo")
