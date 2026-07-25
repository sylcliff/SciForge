"""Integration tests for `sf-lit status`, `sf-lit list`, `sf-lit show` MD status."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from conftest import parse_kv, make_pdf


def _add_and_convert(run, libenv, arxiv="1706.03762"):
    lib, _ = libenv
    src = make_pdf(lib.parent / f"{arxiv}.pdf",
                   content=f"%PDF-1.4 {arxiv}".encode())
    r = run(
        "add", "--title", "Attention Is All You Need",
        "--author", "Ashish Vaswani", "--year", "2017",
        "--arxiv-id", arxiv, "--pdf-path", str(src),
    )
    ck = parse_kv(r.stdout)["citekey"]
    assert run("convert", ck).returncode == 0
    return ck


# ---- status: ready ----------------------------------------------------


def test_status_after_convert_is_ready(run, libenv):
    ck = _add_and_convert(run, libenv)
    r = run("status", ck)
    kv = parse_kv(r.stdout)
    assert kv["md_status"] == "ready"
    assert kv["converter"] == "mineru"
    assert kv["pdf_sha256"]


def test_status_absent_before_convert(run, libenv):
    lib, _ = libenv
    p = make_pdf(lib.parent / "b.pdf")
    r = run("add", "--title", "T", "--author", "A", "--year", "2024",
            "--pdf-path", str(p))
    ck = parse_kv(r.stdout)["citekey"]
    r = run("status", ck)
    assert parse_kv(r.stdout)["md_status"] == "absent"


def test_status_json_shape(run, libenv):
    ck = _add_and_convert(run, libenv)
    r = run("status", ck, "--json")
    data = json.loads(r.stdout)
    assert data["md_status"] == "ready"
    assert data["converter"] == "mineru"
    assert data["char_count"] > 0


# ---- status: revalidation (stale detection) ---------------------------


def test_status_detects_missing_paper_md(run, libenv):
    """Handing back md_status=stale when paper.md is deleted post-convert."""
    ck = _add_and_convert(run, libenv)
    lib, _ = libenv
    (lib / "papers" / ck / "paper.md").unlink()
    r = run("status", ck)
    assert parse_kv(r.stdout)["md_status"] == "stale"


def test_status_detects_pdf_hash_mismatch(run, libenv):
    """PDF replaced on disk (bypassing `add`) → stale on next status call."""
    ck = _add_and_convert(run, libenv)
    lib, _ = libenv
    (lib / "papers" / ck / "paper.pdf").write_bytes(b"%PDF-1.4 different")
    r = run("status", ck)
    assert parse_kv(r.stdout)["md_status"] == "stale"


# ---- list --md-status --------------------------------------------------


def test_list_md_status_absent(run, libenv):
    lib, _ = libenv
    p = make_pdf(lib.parent / "b.pdf")
    r = run("add", "--title", "T", "--author", "A", "--year", "2024",
            "--pdf-path", str(p))
    ck = parse_kv(r.stdout)["citekey"]
    r = run("list", "--md-status", "absent")
    assert ck in r.stdout


def test_list_md_status_ready(run, libenv):
    ck = _add_and_convert(run, libenv)
    r = run("list", "--md-status", "ready")
    assert ck in r.stdout


def test_list_json_shape(run, libenv):
    ck = _add_and_convert(run, libenv)
    r = run("list", "--md-status", "ready", "--json")
    data = json.loads(r.stdout)
    assert data[0]["citekey"] == ck
    assert data[0]["md_status"] == "ready"


def test_list_all(run, libenv):
    ck1 = _add_and_convert(run, libenv, arxiv="1706.03762")
    # Second, absent
    lib, _ = libenv
    p = make_pdf(lib.parent / "b.pdf")
    r = run("add", "--title", "Second", "--author", "B C", "--year", "2020",
            "--pdf-path", str(p))
    ck2 = parse_kv(r.stdout)["citekey"]
    r = run("list")
    assert ck1 in r.stdout
    assert ck2 in r.stdout


# ---- show: MD line ----------------------------------------------------


def test_show_md_line_ready(run, libenv):
    ck = _add_and_convert(run, libenv)
    r = run("show", ck)
    assert "**md:** ready (mineru" in r.stdout


def test_show_md_line_absent(run, libenv):
    lib, _ = libenv
    p = make_pdf(lib.parent / "b.pdf")
    r = run("add", "--title", "T", "--author", "A", "--year", "2024",
            "--pdf-path", str(p))
    ck = parse_kv(r.stdout)["citekey"]
    r = run("show", ck)
    assert "**md:** absent" in r.stdout


def test_show_json_md_field(run, libenv):
    ck = _add_and_convert(run, libenv)
    r = run("show", ck, "--json")
    data = json.loads(r.stdout)
    assert data["md"]["status"] == "ready"
    assert data["md"]["converter"] == "mineru"
    assert data["md"]["char_count"] > 0
