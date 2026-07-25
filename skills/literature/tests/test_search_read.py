"""Integration tests for `litlib search` and `litlib read`.

Exercises BM25 hits, structured filters, snippet output, MinerU vs
Docling asymmetry (--pages/--kind vs --section fallback), and each of
the four `read` modes.
"""

from __future__ import annotations

import json
from pathlib import Path

from conftest import parse_kv, make_pdf


def _ingest_and_convert(run, libenv, converter="mineru",
                        arxiv_id="1706.03762", title="Attention Is All You Need",
                        author="Ashish Vaswani", year=2017) -> str:
    lib, _ = libenv
    src = make_pdf(lib.parent / f"{arxiv_id}.pdf",
                   content=f"%PDF-1.4 {arxiv_id}".encode())
    r = run(
        "add", "--title", title, "--author", author, "--year", str(year),
        "--arxiv-id", arxiv_id, "--pdf-path", str(src),
    )
    ck = parse_kv(r.stdout)["citekey"]
    assert run("convert", ck, "--converter", converter).returncode == 0
    return ck


# ---- search: content hits ---------------------------------------------


def test_search_bm25_hits_body_words(run, libenv):
    ck = _ingest_and_convert(run, libenv)
    r = run("search", "attention")
    assert ck in r.stdout


def test_search_json_shape(run, libenv):
    ck = _ingest_and_convert(run, libenv)
    r = run("search", "attention", "--json")
    data = json.loads(r.stdout)
    assert data and data[0]["citekey"] == ck
    hit = data[0]
    # score present, snippet present, structured flags right
    assert isinstance(hit["score"], float)
    assert "<mark>" in hit["snippet"]
    assert hit["has_pdf"] is True
    assert hit["has_md"] is True
    assert hit["md_status"] == "ready"


def test_search_stemming_matches_singular_and_plural(run, libenv):
    """Q13: porter stemmer merges 'network'/'networks'/'networking'."""
    _ingest_and_convert(run, libenv)
    r1 = run("search", "network", "--json")
    r2 = run("search", "networks", "--json")
    # Body has "networks" — both queries should land on the same paper.
    assert json.loads(r1.stdout)
    assert json.loads(r2.stdout)


def test_search_no_match(run, libenv):
    _ingest_and_convert(run, libenv)
    r = run("search", "quantumfoo1234")
    assert "(no matches)" in r.stdout


# ---- search: structured filters + no query ----------------------------


def test_search_year_range(run, libenv):
    _ingest_and_convert(run, libenv, arxiv_id="1706.03762", year=2017)
    _ingest_and_convert(run, libenv, arxiv_id="2001.11111",
                        title="Recent Work", author="Alice", year=2020)
    r = run("search", "--year", "2015-2018")
    assert "vaswani2017attention" in r.stdout
    assert "alice2020recent" not in r.stdout


def test_search_has_md_filters(run, libenv):
    # Add a paper WITHOUT converting.
    lib, _ = libenv
    p = make_pdf(lib.parent / "b.pdf")
    run("add", "--title", "Absent Paper", "--author", "Zoe D",
        "--year", "2024", "--arxiv-id", "2401.00001", "--pdf-path", str(p))
    # Convert a second paper.
    ck = _ingest_and_convert(run, libenv, arxiv_id="1706.03762")
    r = run("search", "--has-md")
    assert ck in r.stdout
    assert "doe2024absent" not in r.stdout


def test_search_absent_paper_unreachable_by_content(run, libenv):
    """Q-followup: papers with md_status=absent must be invisible to FTS."""
    lib, _ = libenv
    p = make_pdf(lib.parent / "b.pdf")
    run("add", "--title", "Unique Word Xylophone", "--author", "Zoe D",
        "--year", "2024", "--arxiv-id", "2401.00001", "--pdf-path", str(p))
    r = run("search", "xylophone")
    assert "(no matches)" in r.stdout


def test_search_no_query_lists_catalog(run, libenv):
    _ingest_and_convert(run, libenv, arxiv_id="1706.03762")
    _ingest_and_convert(run, libenv, arxiv_id="2001.11111",
                        title="Second", author="Bob C", year=2020)
    r = run("search", "--limit", "10")
    # Both papers listed. Second author is "Bob C" so last name is C →
    # citekey is c2020second.
    assert "vaswani2017attention" in r.stdout
    assert "c2020second" in r.stdout


# ---- read: whole paper ------------------------------------------------


def test_read_whole_paper(run, libenv):
    ck = _ingest_and_convert(run, libenv)
    r = run("read", ck)
    assert "Attention Is All You Need" in r.stdout
    assert "3.2 Baselines" in r.stdout


def test_read_absent_paper_errors(run, libenv):
    lib, _ = libenv
    p = make_pdf(lib.parent / "b.pdf")
    r = run("add", "--title", "T", "--author", "A", "--year", "2024",
            "--pdf-path", str(p))
    ck = parse_kv(r.stdout)["citekey"]
    r = run("read", ck)
    assert r.returncode == 1
    assert "md_status=absent" in r.stderr


# ---- read --section (Q19/B fuzzy matcher) -----------------------------


def test_read_section_by_number(run, libenv):
    ck = _ingest_and_convert(run, libenv)
    r = run("read", ck, "--section", "3.2", "--json")
    data = json.loads(r.stdout)
    # "3.2" matches "3.2 Baselines" (one hit) — does NOT match plain "3".
    assert data["count"] == 1
    assert "Baselines" in data["sections"][0]["heading"]


def test_read_section_by_word(run, libenv):
    ck = _ingest_and_convert(run, libenv)
    r = run("read", ck, "--section", "Baselines", "--json")
    data = json.loads(r.stdout)
    assert data["count"] == 1
    assert "Baselines" in data["sections"][0]["heading"]


def test_read_section_mineru_has_pages(run, libenv):
    ck = _ingest_and_convert(run, libenv)
    r = run("read", ck, "--section", "Baselines", "--json")
    data = json.loads(r.stdout)
    s = data["sections"][0]
    assert s["page_from"] is not None


def test_read_section_no_match_returns_empty(run, libenv):
    ck = _ingest_and_convert(run, libenv)
    r = run("read", ck, "--section", "NoSuchThing", "--json")
    data = json.loads(r.stdout)
    assert data["count"] == 0
    assert data["sections"] == []


def test_read_section_returns_array_even_on_single(run, libenv):
    """Q19 contract: `sections` is always a list."""
    ck = _ingest_and_convert(run, libenv)
    r = run("read", ck, "--section", "Baselines", "--json")
    data = json.loads(r.stdout)
    assert isinstance(data["sections"], list)


# ---- read --pages / --kind (MinerU-only) ------------------------------


def test_read_pages_mineru(run, libenv):
    ck = _ingest_and_convert(run, libenv)
    r = run("read", ck, "--pages", "3", "--json")
    data = json.loads(r.stdout)
    # Page 3 (1-indexed) contains the "Model Architecture" section.
    assert data["pages"]


def test_read_kind_table(run, libenv):
    ck = _ingest_and_convert(run, libenv)
    r = run("read", ck, "--kind", "table", "--json")
    data = json.loads(r.stdout)
    assert data["blocks"]
    assert "BLEU" in data["blocks"][0]["text"]


def test_pages_on_docling_errors(run, libenv):
    ck = _ingest_and_convert(run, libenv, converter="docling",
                             arxiv_id="1234.00001",
                             title="Docling Only", author="Zoe D", year=2025)
    r = run("read", ck, "--pages", "1-2")
    assert r.returncode != 0
    assert "MinerU" in r.stderr


def test_kind_on_docling_errors(run, libenv):
    ck = _ingest_and_convert(run, libenv, converter="docling",
                             arxiv_id="1234.00002",
                             title="Docling Only 2", author="Zoe D", year=2025)
    r = run("read", ck, "--kind", "table")
    assert r.returncode != 0
    assert "MinerU" in r.stderr


def test_section_still_works_on_docling(run, libenv):
    ck = _ingest_and_convert(run, libenv, converter="docling",
                             arxiv_id="1234.00003",
                             title="Docling Only 3", author="Zoe D", year=2025)
    r = run("read", ck, "--section", "Methods", "--json")
    data = json.loads(r.stdout)
    assert data["count"] == 1


# ---- read --grep -------------------------------------------------------


def test_read_grep_returns_lines(run, libenv):
    ck = _ingest_and_convert(run, libenv)
    r = run("read", ck, "--grep", "Transformer", "--json")
    data = json.loads(r.stdout)
    assert data["matches"]
    assert any("Transformer" in m["text"] for m in data["matches"])


def test_read_grep_bad_regex_errors(run, libenv):
    ck = _ingest_and_convert(run, libenv)
    r = run("read", ck, "--grep", "[bad")
    assert r.returncode != 0
    assert "regex" in r.stderr.lower()


# ---- mutual exclusivity ----------------------------------------------


def test_read_multiple_modes_rejected(run, libenv):
    ck = _ingest_and_convert(run, libenv)
    r = run("read", ck, "--section", "x", "--pages", "1")
    assert r.returncode != 0
    assert "at most one" in r.stderr
