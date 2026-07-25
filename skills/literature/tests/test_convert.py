"""Integration tests for `sf-lit convert`.

Covers the full idempotency table from Q8 plus Docling as a switch,
--converted-dir, and the failure path.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from conftest import parse_kv, make_pdf


def _add_paper(run, libenv, arxiv_id="1706.03762", author="Ashish Vaswani",
               title="Attention Is All You Need", year=2017) -> tuple[str, Path]:
    """Add a paper with a fake PDF. Return (citekey, pdf_path_in_lib)."""
    lib, _ = libenv
    src = make_pdf(lib.parent / f"{arxiv_id}.pdf",
                   content=f"%PDF-1.4 {arxiv_id}".encode())
    r = run(
        "add", "--title", title, "--author", author, "--year", str(year),
        "--arxiv-id", arxiv_id, "--pdf-path", str(src),
    )
    assert r.returncode == 0, r.stderr
    ck = parse_kv(r.stdout)["citekey"]
    return ck, lib / "papers" / ck / "paper.pdf"


# ---- happy path -------------------------------------------------------


def test_convert_mineru_default(run, libenv):
    ck, _ = _add_paper(run, libenv)
    r = run("convert", ck)
    assert r.returncode == 0, r.stderr
    kv = parse_kv(r.stdout)
    assert kv["md_status"] == "ready"
    assert kv["converter"] == "mineru"
    # paper.md exists at library root
    lib, _ = libenv
    assert (lib / "papers" / ck / "paper.md").is_file()
    # converter.json exists with sha256
    sidecar = json.loads((lib / "papers" / ck / "converter.json").read_text())
    assert sidecar["converter"] == "mineru"
    assert len(sidecar["pdf_sha256"]) == 64


def test_convert_docling(run, libenv):
    ck, _ = _add_paper(run, libenv)
    r = run("convert", ck, "--converter", "docling")
    assert r.returncode == 0, r.stderr
    kv = parse_kv(r.stdout)
    assert kv["converter"] == "docling"
    lib, _ = libenv
    # Docling output tree is separate from mineru's
    assert (lib / "papers" / ck / "converter_output" / "docling").is_dir()


# ---- idempotency (Q8 table) -------------------------------------------


def test_convert_repeat_without_flag_errors(run, libenv):
    """Re-running `convert` without --reconvert on an already-ready paper fails."""
    ck, _ = _add_paper(run, libenv)
    assert run("convert", ck).returncode == 0
    r = run("convert", ck)
    assert r.returncode != 0
    assert "already has md_status=ready" in r.stderr


def test_reconvert_noop_when_inputs_unchanged(run, libenv):
    """The sha256 fuse: same PDF + same converter + same version → action=noop."""
    ck, _ = _add_paper(run, libenv)
    assert run("convert", ck).returncode == 0
    r = run("convert", ck, "--reconvert")
    assert r.returncode == 0
    kv = parse_kv(r.stdout)
    assert kv.get("action") == "noop"


def test_reconvert_force_bypasses_fuse(run, libenv):
    ck, _ = _add_paper(run, libenv)
    assert run("convert", ck).returncode == 0
    r = run("convert", ck, "--reconvert", "--force")
    assert r.returncode == 0
    kv = parse_kv(r.stdout)
    # Not a noop this time.
    assert kv.get("action") != "noop"
    assert kv["md_status"] == "ready"


def test_switch_converter_via_reconvert(run, libenv):
    ck, _ = _add_paper(run, libenv)
    assert run("convert", ck).returncode == 0
    r = run("convert", ck, "--reconvert", "--converter", "docling")
    assert r.returncode == 0
    kv = parse_kv(r.stdout)
    assert kv["converter"] == "docling"
    # Both converters' outputs coexist on disk (Q4/A: keep everything).
    lib, _ = libenv
    assert (lib / "papers" / ck / "converter_output" / "mineru").is_dir()
    assert (lib / "papers" / ck / "converter_output" / "docling").is_dir()


def test_pdf_change_forces_reconvert(run, libenv):
    """When the PDF changes, the fuse must not fire — real content differs."""
    ck, pdf_in_lib = _add_paper(run, libenv)
    assert run("convert", ck).returncode == 0
    # Overwrite the PDF on disk with different bytes.
    pdf_in_lib.write_bytes(b"%PDF-1.4 different content")
    r = run("convert", ck, "--reconvert")
    assert r.returncode == 0
    kv = parse_kv(r.stdout)
    assert kv.get("action") != "noop"


# ---- --converted-dir escape hatch --------------------------------------


def test_converted_dir_shortcut(run, libenv, tmp_path):
    """Ingest an existing converter output tree without spawning the CLI."""
    ck, _ = _add_paper(run, libenv)
    # Fake an out-of-band conversion produced elsewhere.
    prebuilt = tmp_path / "prebuilt"
    dest = prebuilt / "paper" / "auto"
    dest.mkdir(parents=True)
    (dest / "paper.md").write_text(
        "# Manual Import\n\n## Section A\n\nhello.\n", encoding="utf-8"
    )
    (dest / "paper_content_list.json").write_text(
        json.dumps([
            {"type": "text", "text": "Manual Import", "text_level": 1, "page_idx": 0},
            {"type": "text", "text": "Section A", "text_level": 2, "page_idx": 0},
            {"type": "text", "text": "hello.", "page_idx": 0},
        ]),
        encoding="utf-8",
    )
    r = run("convert", ck, "--converted-dir", str(prebuilt))
    assert r.returncode == 0, r.stderr
    kv = parse_kv(r.stdout)
    assert kv["md_status"] == "ready"


# ---- failure path -----------------------------------------------------


def test_failed_convert_sets_status(run, libenv, monkeypatch):
    """A missing converter binary → md_status=failed, md_last_error populated."""
    ck, _ = _add_paper(run, libenv)
    # Point the mineru env override at a bogus path so it can't be found.
    _, env = libenv
    env = {**env, "LITLIB_MINERU_BIN": "/nonexistent/mineru"}
    import subprocess, sys
    from pathlib import Path as _P
    LITLIB = _P(__file__).parent.parent / "scripts" / "sf-lit"
    r = subprocess.run(
        [sys.executable, str(LITLIB), "convert", ck],
        env=env, capture_output=True, text=True,
    )
    assert r.returncode != 0
    # Status now reports failed.
    r2 = subprocess.run(
        [sys.executable, str(LITLIB), "status", ck, "--json"],
        env=env, capture_output=True, text=True,
    )
    data = json.loads(r2.stdout)
    assert data["md_status"] == "failed"
    assert data.get("last_error")


def test_reconvert_after_failure_succeeds(run, libenv):
    """Once the failure is resolved, a plain reconvert should succeed."""
    ck, _ = _add_paper(run, libenv)
    # Force a failure once via bogus env.
    _, env = libenv
    import subprocess, sys
    from pathlib import Path as _P
    LITLIB = _P(__file__).parent.parent / "scripts" / "sf-lit"
    bogus = {**env, "LITLIB_MINERU_BIN": "/nonexistent/mineru"}
    subprocess.run([sys.executable, str(LITLIB), "convert", ck],
                   env=bogus, capture_output=True, text=True)
    # Now retry with a working converter (i.e. the fixture env).
    r = run("convert", ck, "--reconvert")
    assert r.returncode == 0, r.stderr
    assert parse_kv(r.stdout)["md_status"] == "ready"
