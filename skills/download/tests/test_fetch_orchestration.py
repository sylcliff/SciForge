"""Fallback + status-code orchestration tests.

Mocks all 5 sources with respx so we can prove the fallback chain,
metadata union, and status decisions without touching the network.
"""

from __future__ import annotations

import sys
from pathlib import Path

import httpx
import pytest
import respx

_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(_SCRIPTS))

from config import DownloadConfig  # noqa: E402
from fetch import fetch_one  # noqa: E402
from output import Status  # noqa: E402


# --------------------------------------------------------------------------- #
# Response builders — small, readable, one per shape.
# --------------------------------------------------------------------------- #


ARXIV_ATOM_HIT = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"
      xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/"
      xmlns:arxiv="http://arxiv.org/schemas/atom">
  <opensearch:totalResults>1</opensearch:totalResults>
  <entry>
    <id>http://arxiv.org/abs/1706.03762v7</id>
    <title>Attention Is All You Need</title>
    <summary>The dominant sequence transduction models are based on complex recurrent or convolutional neural networks.</summary>
    <author><name>Ashish Vaswani</name></author>
    <author><name>Noam Shazeer</name></author>
    <published>2017-06-12T00:00:00Z</published>
    <link rel="alternate" type="text/html" href="http://arxiv.org/abs/1706.03762"/>
  </entry>
</feed>
"""

ARXIV_ATOM_MISS = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"
      xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/">
  <opensearch:totalResults>0</opensearch:totalResults>
</feed>
"""

CROSSREF_HIT = {
    "status": "ok",
    "message": {
        "title": ["Array programming with NumPy"],
        "author": [
            {"given": "Charles R.", "family": "Harris"},
            {"given": "K. Jarrod", "family": "Millman"},
        ],
        "abstract": "<jats:p>Array programming provides a powerful, compact and expressive syntax for accessing, manipulating and operating on data in vectors, matrices, and higher-dimensional arrays.</jats:p>",
        "issued": {"date-parts": [[2020]]},
        "container-title": ["Nature"],
        "short-container-title": ["Nature"],
        "DOI": "10.1038/s41586-020-2649-2",
        "URL": "https://www.nature.com/articles/s41586-020-2649-2",
    },
}

UNPAYWALL_OA = {
    "doi": "10.1038/s41586-020-2649-2",
    "is_oa": True,
    "title": "Array programming with NumPy",
    "year": 2020,
    "journal_name": "Nature",
    "doi_url": "https://doi.org/10.1038/s41586-020-2649-2",
    "best_oa_location": {
        "url": "https://www.nature.com/articles/s41586-020-2649-2",
        "url_for_pdf": "https://www.nature.com/articles/s41586-020-2649-2.pdf",
    },
}

UNPAYWALL_CLOSED = {
    "doi": "10.1234/paywalled",
    "is_oa": False,
    "title": "A Very Closed Paper",
    "year": 2022,
}

S2_HIT = {
    "paperId": "abc123" + "0" * 34,
    "title": "Array programming with NumPy",
    "authors": [{"name": "Charles R. Harris"}],
    "year": 2020,
    "venue": "Nature",
    "abstract": "Array programming provides a powerful, compact and expressive syntax for arrays.",
    "externalIds": {"DOI": "10.1038/s41586-020-2649-2"},
    "openAccessPdf": None,
    "url": "https://www.semanticscholar.org/paper/abc123",
}

OPENALEX_HIT = {
    "id": "https://openalex.org/W3103432018",
    "doi": "https://doi.org/10.1038/s41586-020-2649-2",
    "title": "Array programming with NumPy",
    "publication_year": 2020,
    "authorships": [
        {"author": {"display_name": "Charles R. Harris"}},
    ],
    "primary_location": {
        "landing_page_url": "https://www.nature.com/articles/s41586-020-2649-2",
        "pdf_url": None,
        "source": {"display_name": "Nature"},
    },
}


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture
def cfg() -> DownloadConfig:
    c = DownloadConfig(
        polite_email="test@example.com",
        semanticscholar_api_key="",
        http_timeout_seconds=5,
        max_concurrency=4,
    )
    return c


@pytest.fixture
def out_dir(tmp_path: Path) -> Path:
    d = tmp_path / "inbox"
    d.mkdir()
    return d


@pytest.fixture
def pdf_bytes() -> bytes:
    """Minimum viable PDF: just needs to start with %PDF."""
    return b"%PDF-1.4\n" + b"x" * 100 + b"\n%%EOF\n"


# --------------------------------------------------------------------------- #
# arXiv path — fastest, no auth needed
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_arxiv_id_downloaded(cfg: DownloadConfig, out_dir: Path, pdf_bytes: bytes) -> None:
    with respx.mock(assert_all_called=False) as r:
        r.get("http://export.arxiv.org/api/query").respond(200, text=ARXIV_ATOM_HIT)
        r.get("https://arxiv.org/pdf/1706.03762.pdf").respond(
            200, content=pdf_bytes, headers={"content-type": "application/pdf"}
        )
        # S2 enrichment on ArXiv:… — return "not found" so it stays out of the way
        r.get("https://api.semanticscholar.org/graph/v1/paper/ArXiv:1706.03762").respond(404)

        async with httpx.AsyncClient(timeout=5) as client:
            result = await fetch_one("1706.03762", 0, cfg, out_dir, client)

    assert result.status == Status.DOWNLOADED
    assert result.source_used == "arxiv"
    assert result.meta is not None
    assert result.meta.title == "Attention Is All You Need"
    assert result.pdf_path is not None
    assert Path(result.pdf_path).is_file()
    assert Path(result.pdf_path).stat().st_size > 0


# --------------------------------------------------------------------------- #
# DOI path with Unpaywall giving OA PDF
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_doi_unpaywall_wins_pdf(cfg: DownloadConfig, out_dir: Path, pdf_bytes: bytes) -> None:
    doi = "10.1038/s41586-020-2649-2"
    with respx.mock(assert_all_called=False) as r:
        r.get(f"https://api.crossref.org/works/{doi}").respond(200, json=CROSSREF_HIT)
        r.get(f"https://api.unpaywall.org/v2/{doi}").respond(200, json=UNPAYWALL_OA)
        r.get(f"https://api.semanticscholar.org/graph/v1/paper/DOI:{doi}").respond(200, json=S2_HIT)
        r.get(f"https://api.openalex.org/works/https://doi.org/{doi}").respond(200, json=OPENALEX_HIT)
        r.get(UNPAYWALL_OA["best_oa_location"]["url_for_pdf"]).respond(
            200, content=pdf_bytes, headers={"content-type": "application/pdf"}
        )

        async with httpx.AsyncClient(timeout=5) as client:
            result = await fetch_one(doi, 0, cfg, out_dir, client)

    assert result.status == Status.DOWNLOADED
    assert result.source_used == "unpaywall"
    # Metadata union: Crossref's title should win (highest priority)
    assert result.meta is not None
    assert result.meta.title == "Array programming with NumPy"
    # Crossref's ordered authors should win — Crossref returned 2 authors,
    # S2 only 1, so union preferring Crossref keeps 2.
    assert result.meta.authors is not None and len(result.meta.authors) == 2
    assert "unpaywall" in result.sources_queried


# --------------------------------------------------------------------------- #
# Paywalled DOI (is_oa=false)
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_doi_paywalled(cfg: DownloadConfig, out_dir: Path) -> None:
    doi = "10.1234/paywalled"
    with respx.mock(assert_all_called=False) as r:
        r.get(f"https://api.crossref.org/works/{doi}").respond(
            200,
            json={
                "status": "ok",
                "message": {
                    "title": ["A Very Closed Paper"],
                    "author": [{"given": "X", "family": "Y"}],
                    "issued": {"date-parts": [[2022]]},
                    "DOI": doi,
                },
            },
        )
        r.get(f"https://api.unpaywall.org/v2/{doi}").respond(200, json=UNPAYWALL_CLOSED)
        r.get(f"https://api.semanticscholar.org/graph/v1/paper/DOI:{doi}").respond(404)
        r.get(f"https://api.openalex.org/works/https://doi.org/{doi}").respond(404)

        async with httpx.AsyncClient(timeout=5) as client:
            result = await fetch_one(doi, 0, cfg, out_dir, client)

    assert result.status == Status.PAYWALLED
    assert result.pdf_path is None
    # Metadata still gathered from Crossref
    assert result.meta is not None
    assert result.meta.title == "A Very Closed Paper"


# --------------------------------------------------------------------------- #
# All 404 → identifier_not_found
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_all_404_identifier_not_found(cfg: DownloadConfig, out_dir: Path) -> None:
    doi = "10.9999/definitely-not-a-real-doi"
    with respx.mock(assert_all_called=False) as r:
        r.get(f"https://api.crossref.org/works/{doi}").respond(404)
        r.get(f"https://api.unpaywall.org/v2/{doi}").respond(404)
        r.get(f"https://api.semanticscholar.org/graph/v1/paper/DOI:{doi}").respond(404)
        r.get(f"https://api.openalex.org/works/https://doi.org/{doi}").respond(404)

        async with httpx.AsyncClient(timeout=5) as client:
            result = await fetch_one(doi, 0, cfg, out_dir, client)

    assert result.status == Status.IDENTIFIER_NOT_FOUND
    assert result.meta is None
    assert result.pdf_path is None


# --------------------------------------------------------------------------- #
# PDF hint exists but bytes fail → pdf_link_broken
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_pdf_link_broken_when_bytes_bad(cfg: DownloadConfig, out_dir: Path) -> None:
    doi = "10.1038/s41586-020-2649-2"
    with respx.mock(assert_all_called=False) as r:
        r.get(f"https://api.crossref.org/works/{doi}").respond(200, json=CROSSREF_HIT)
        r.get(f"https://api.unpaywall.org/v2/{doi}").respond(200, json=UNPAYWALL_OA)
        r.get(f"https://api.semanticscholar.org/graph/v1/paper/DOI:{doi}").respond(404)
        r.get(f"https://api.openalex.org/works/https://doi.org/{doi}").respond(404)
        # PDF URL returns HTML instead of PDF bytes
        r.get(UNPAYWALL_OA["best_oa_location"]["url_for_pdf"]).respond(
            200,
            content=b"<html><body>Sign in to download</body></html>",
            headers={"content-type": "text/html"},
        )

        async with httpx.AsyncClient(timeout=5) as client:
            result = await fetch_one(doi, 0, cfg, out_dir, client)

    assert result.status == Status.PDF_LINK_BROKEN
    assert result.pdf_attempts is not None and len(result.pdf_attempts) >= 1
    assert result.pdf_attempts[0].reason == "not_pdf_content_type"
    assert result.meta is not None  # metadata still came through


# --------------------------------------------------------------------------- #
# Unpaywall skipped when no email — no request made
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_unpaywall_skipped_without_email(out_dir: Path, pdf_bytes: bytes) -> None:
    cfg_noemail = DownloadConfig(polite_email="", http_timeout_seconds=5, max_concurrency=4)
    doi = "10.1038/s41586-020-2649-2"
    with respx.mock(assert_all_called=False) as r:
        r.get(f"https://api.crossref.org/works/{doi}").respond(200, json=CROSSREF_HIT)
        # DO NOT register Unpaywall — if the code tries to call it, respx will
        # raise for the unmatched route, which is exactly the safety we want.
        r.get(f"https://api.semanticscholar.org/graph/v1/paper/DOI:{doi}").respond(200, json=S2_HIT)
        r.get(f"https://api.openalex.org/works/https://doi.org/{doi}").respond(200, json=OPENALEX_HIT)

        async with httpx.AsyncClient(timeout=5) as client:
            result = await fetch_one(doi, 0, cfg_noemail, out_dir, client)

    # No PDF hint from anyone → metadata_only
    assert result.status == Status.METADATA_ONLY
    assert "unpaywall" not in result.sources_queried


# --------------------------------------------------------------------------- #
# Invalid input
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_invalid_input_short_gibberish(cfg: DownloadConfig, out_dir: Path) -> None:
    # Short + no known shape → invalid_input (not routed to title search).
    async with httpx.AsyncClient(timeout=5) as client:
        result = await fetch_one("???", 0, cfg, out_dir, client)
    assert result.status == Status.INVALID_INPUT


# --------------------------------------------------------------------------- #
# Title fallback — strict match wins → recurse
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_title_strict_match_resolves(cfg: DownloadConfig, out_dir: Path, pdf_bytes: bytes) -> None:
    title = "Attention Is All You Need"
    openalex_search_body = {
        "meta": {"count": 1},
        "results": [
            {
                "id": "https://openalex.org/W2963403868",
                "doi": None,
                "title": "Attention Is All You Need",
                "publication_year": 2017,
                "authorships": [
                    {"author": {"display_name": "Ashish Vaswani"}},
                ],
                "primary_location": {
                    "landing_page_url": "https://arxiv.org/abs/1706.03762",
                    "pdf_url": None,
                    "source": {"display_name": "arXiv"},
                },
                "ids": {"doi": "10.48550/arxiv.1706.03762"},
            }
        ],
    }
    with respx.mock(assert_all_called=False) as r:
        r.get("https://api.openalex.org/works", params={"search": title}).respond(200, json=openalex_search_body)
        # Recursion path: resolved_arxiv_id → arxiv API call, plus its PDF.
        r.get("http://export.arxiv.org/api/query").respond(200, text=ARXIV_ATOM_HIT)
        r.get("https://arxiv.org/pdf/1706.03762.pdf").respond(
            200, content=pdf_bytes, headers={"content-type": "application/pdf"}
        )
        # And S2 lookup by ArXiv id for enrichment.
        r.get("https://api.semanticscholar.org/graph/v1/paper/ArXiv:1706.03762").respond(
            200,
            json={
                "paperId": "x" * 40,
                "title": title,
                "authors": [{"name": "Ashish Vaswani"}],
                "year": 2017,
                "externalIds": {"ArXiv": "1706.03762"},
                "openAccessPdf": {"url": "https://arxiv.org/pdf/1706.03762.pdf"},
            },
        )

        async with httpx.AsyncClient(timeout=5) as client:
            result = await fetch_one(title, 0, cfg, out_dir, client, treat_as_title=True)

    assert result.status == Status.DOWNLOADED
    assert result.identifier == title  # original user string preserved
    # arxiv wins the PDF race (highest priority in the fallback chain).
    assert result.source_used == "arxiv"


# --------------------------------------------------------------------------- #
# Title fallback — no confident match → title_ambiguous with candidates
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_title_ambiguous(cfg: DownloadConfig, out_dir: Path) -> None:
    title = "transformer attention"
    body = {
        "results": [
            {
                "title": "A Comparative Study of Transformer Attention Mechanisms",
                "doi": "https://doi.org/10.1/a",
                "publication_year": 2020,
                "authorships": [{"author": {"display_name": "X Y"}}],
            },
            {
                "title": "Attention Is All You Need",
                "doi": "https://doi.org/10.1/b",
                "publication_year": 2017,
                "authorships": [{"author": {"display_name": "A B"}}],
            },
        ]
    }
    with respx.mock(assert_all_called=False) as r:
        r.get("https://api.openalex.org/works", params={"search": title}).respond(200, json=body)

        async with httpx.AsyncClient(timeout=5) as client:
            result = await fetch_one(title, 0, cfg, out_dir, client, treat_as_title=True)

    assert result.status == Status.TITLE_AMBIGUOUS
    assert result.candidates is not None and len(result.candidates) >= 1
    assert result.candidates[0].title.startswith("A Comparative Study")


# --------------------------------------------------------------------------- #
# Cache hit — second call reuses the file
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_second_call_uses_cache(cfg: DownloadConfig, out_dir: Path, pdf_bytes: bytes) -> None:
    with respx.mock(assert_all_called=False) as r:
        r.get("http://export.arxiv.org/api/query").respond(200, text=ARXIV_ATOM_HIT)
        r.get("https://arxiv.org/pdf/1706.03762.pdf").respond(
            200, content=pdf_bytes, headers={"content-type": "application/pdf"}
        )
        r.get("https://api.semanticscholar.org/graph/v1/paper/ArXiv:1706.03762").respond(404)

        async with httpx.AsyncClient(timeout=5) as client:
            first = await fetch_one("1706.03762", 0, cfg, out_dir, client)
            second = await fetch_one("1706.03762", 1, cfg, out_dir, client)

    assert first.status == Status.DOWNLOADED
    assert second.status == Status.DOWNLOADED
    assert second.source_used == "cache"


if __name__ == "__main__":  # pragma: no cover
    sys.exit(pytest.main([__file__, "-v"]))
