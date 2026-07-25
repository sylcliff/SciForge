"""Integration tests for `sf-lit add` (catalog step, two-phase).

Uses the fake-converter fixtures via ``libenv`` / ``run`` from conftest.
Focuses on:
  - The four ingest entry points.
  - Duplicate detection and --upsert.
  - PDF copy vs move.
  - --and-convert sugar (which chains into `convert`).
  - md_status=absent semantics.
"""

from __future__ import annotations

import json
from pathlib import Path

from conftest import parse_kv, make_pdf


# ---- entry points -----------------------------------------------------


def test_cli_flags(run, libenv):
    lib, _ = libenv
    pdf = make_pdf(lib.parent / "src.pdf")
    r = run(
        "add", "--title", "Attention Is All You Need",
        "--author", "Ashish Vaswani", "--author", "Noam Shazeer",
        "--year", "2017", "--arxiv-id", "1706.03762",
        "--pdf-path", str(pdf),
    )
    assert r.returncode == 0, r.stderr
    kv = parse_kv(r.stdout)
    assert kv["citekey"] == "vaswani2017attention"
    assert kv["md_status"] == "absent"
    assert "convert" in kv["hint"]


def test_meta_json_stdin(run):
    blob = json.dumps({
        "title": "Array programming with NumPy",
        "authors": ["Charles Harris"],
        "year": 2020,
        "doi": "10.1038/s41586-020-2649-2",
    })
    r = run("add", "--meta-json", "-", stdin=blob)
    assert r.returncode == 0, r.stderr
    assert parse_kv(r.stdout)["citekey"] == "harris2020array"


def test_meta_json_file(run, tmp_path):
    meta_path = tmp_path / "m.json"
    meta_path.write_text(json.dumps({
        "title": "Test", "authors": ["Alice"], "year": 2024,
    }))
    r = run("add", "--meta-json", str(meta_path))
    assert r.returncode == 0, r.stderr


def test_meta_json_invalid(run):
    # ADR-0006: malformed --meta-json is invalid user input (exit 2).
    r = run("add", "--meta-json", "-", stdin="not json{")
    assert r.returncode == 2


def test_empty_title_rejected(run):
    # ADR-0006: empty title fails the metadata schema → invalid input (exit 2).
    r = run("add", "--title", "   ")
    assert r.returncode == 2


# ---- PDF handling ------------------------------------------------------


def test_pdf_copy_semantics(run, libenv):
    lib, _ = libenv
    src = make_pdf(lib.parent / "src.pdf")
    r = run(
        "add", "--title", "PDF Test", "--author", "Alice", "--year", "2024",
        "--pdf-path", str(src),
    )
    assert r.returncode == 0, r.stderr
    kv = parse_kv(r.stdout)
    assert Path(kv["pdf"]).is_file()
    assert src.exists(), "source PDF should still exist under copy semantics"


def test_pdf_move_semantics(run, libenv):
    lib, _ = libenv
    src = make_pdf(lib.parent / "moved.pdf")
    r = run(
        "add", "--title", "Move Test", "--author", "Alice", "--year", "2024",
        "--pdf-path", str(src), "--move-pdf",
    )
    assert r.returncode == 0, r.stderr
    assert not src.exists()


def test_missing_pdf_rejected(run):
    # ADR-0006: --pdf-path pointing at a nonexistent file → resource
    # not found (exit 3). The user named a file; it isn't there.
    r = run("add", "--title", "X", "--pdf-path", "/nonexistent/xyz.pdf")
    assert r.returncode == 3


def test_zero_byte_pdf_rejected(run, tmp_path):
    # ADR-0006: file exists but is empty → invalid user input (exit 2).
    # Distinct from "file missing" (3) because the file is there but bad.
    p = tmp_path / "empty.pdf"
    p.write_bytes(b"")
    r = run("add", "--title", "X", "--pdf-path", str(p))
    assert r.returncode == 2


# ---- duplicate & upsert -----------------------------------------------


def test_duplicate_exits_2(run):
    for _ in range(2):
        r = run(
            "add", "--title", "Foo", "--author", "A B",
            "--year", "2024", "--arxiv-id", "2401.99999",
        )
    assert r.returncode == 2
    assert "already in library" in r.stderr


def test_upsert_merges_fields_and_tags(run):
    run(
        "add", "--title", "Foo", "--author", "A B",
        "--year", "2024", "--arxiv-id", "2401.99999",
    )
    blob = json.dumps({
        "arxiv_id": "2401.99999",
        "abstract": "UPDATED",
        "tags": ["new-tag"],
    })
    r = run("add", "--meta-json", "-", "--upsert", stdin=blob)
    assert r.returncode == 0, r.stderr
    assert "upsert=1" in r.stdout

    r = run("show", "2401.99999", "--json")
    data = json.loads(r.stdout)
    assert data["abstract"] == "UPDATED"
    assert "new-tag" in data["tags"]


# ---- md_status=absent default ----------------------------------------


def test_add_never_runs_converter(run, libenv):
    lib, _ = libenv
    pdf = make_pdf(lib.parent / "src.pdf")
    r = run(
        "add", "--title", "T", "--author", "A B", "--year", "2024",
        "--pdf-path", str(pdf),
    )
    kv = parse_kv(r.stdout)
    ck = kv["citekey"]
    # md_status is absent; no paper.md on disk
    r2 = run("status", ck)
    kv2 = parse_kv(r2.stdout)
    assert kv2["md_status"] == "absent"
    assert not (lib / "papers" / ck / "paper.md").exists()


# ---- --and-convert sugar ----------------------------------------------


def test_and_convert_runs_conversion(run, libenv):
    lib, _ = libenv
    pdf = make_pdf(lib.parent / "src.pdf")
    r = run(
        "add", "--title", "Attention Is All You Need",
        "--author", "Ashish Vaswani", "--year", "2017",
        "--arxiv-id", "1706.03762",
        "--pdf-path", str(pdf), "--and-convert",
    )
    assert r.returncode == 0, r.stderr
    # After --and-convert, md_status should be ready.
    r2 = run("status", "vaswani2017attention")
    kv = parse_kv(r2.stdout)
    assert kv["md_status"] == "ready"
    assert (lib / "papers" / "vaswani2017attention" / "paper.md").is_file()
