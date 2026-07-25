"""Integration tests preserved from v1: association verbs, export, rebuild, open.

These verbs are unchanged in v2 (Q15/1 = keep catalog layer, Q15/3 = keep
`open` handbook). This suite is a regression net: it ensures the rewrite
of add/convert didn't break tag/collection/note/add-github/add-news/add-si,
BibTeX/JSON export, `rebuild-db` (now MD-aware), and `open` (now with
the new `md` target).
"""

from __future__ import annotations

import json
from pathlib import Path

from conftest import parse_kv, make_pdf


def _add(run, libenv, **kw):
    lib, _ = libenv
    default = {
        "title": "T", "author": "A B", "year": 2024,
        "arxiv_id": None, "pdf": None,
    }
    default.update(kw)
    argv = [
        "add", "--title", default["title"],
        "--author", default["author"], "--year", str(default["year"]),
    ]
    if default["arxiv_id"]:
        argv += ["--arxiv-id", default["arxiv_id"]]
    if default["pdf"]:
        argv += ["--pdf-path", default["pdf"]]
    r = run(*argv)
    assert r.returncode == 0, r.stderr
    return parse_kv(r.stdout)["citekey"]


# ---- tag / collection --------------------------------------------------


def test_tag_add_remove(run, libenv):
    ck = _add(run, libenv, arxiv_id="2401.11111")
    assert run("tag", ck, "hello").returncode == 0
    r = run("search", "--tag", "hello")
    assert ck in r.stdout
    assert run("tag", ck, "hello", "--remove").returncode == 0
    r = run("search", "--tag", "hello")
    assert ck not in r.stdout


def test_collection(run, libenv):
    ck = _add(run, libenv, arxiv_id="2401.22222")
    assert run("collection", "reading-2026", "add", ck).returncode == 0
    r = run("search", "--collection", "reading-2026")
    assert ck in r.stdout


# ---- add-github / add-news / add-si ----------------------------------


def test_add_github(run, libenv):
    ck = _add(run, libenv, arxiv_id="2401.33333")
    r = run("add-github", ck, "--owner", "tensorflow", "--repo", "tensor2tensor",
            "--stars", "15000")
    assert r.returncode == 0, r.stderr
    r = run("show", ck, "--json")
    data = json.loads(r.stdout)
    assert data["github"][0]["owner"] == "tensorflow"
    assert data["github"][0]["stars"] == 15000


def test_add_news_dedupes_on_url(run, libenv):
    ck = _add(run, libenv, arxiv_id="2401.44444")
    for _ in range(2):
        assert run("add-news", ck, "--url", "https://ex.com/x", "--kind", "blog").returncode == 0
    r = run("show", ck, "--json")
    data = json.loads(r.stdout)
    assert len(data["news"]) == 1


def test_add_si_copies_file(run, libenv, tmp_path):
    ck = _add(run, libenv, arxiv_id="2401.55555")
    src = tmp_path / "si.pdf"
    src.write_bytes(b"%PDF supplement")
    assert run("add-si", ck, "--path", str(src), "--label", "SI-1").returncode == 0
    lib, _ = libenv
    si_files = list((lib / "papers" / ck / "si").iterdir())
    assert len(si_files) == 1


def test_si_files_do_not_appear_in_paper_md(run, libenv, tmp_path):
    """Q17: SI is never part of paper.md / papers_md."""
    lib, _ = libenv
    pdf = make_pdf(lib.parent / "src.pdf")
    r = run("add", "--title", "Main", "--author", "X Y", "--year", "2024",
            "--arxiv-id", "2401.66666", "--pdf-path", str(pdf), "--and-convert")
    assert r.returncode == 0
    ck = "yxxx2024main"  # actual citekey resolved below
    ck = parse_kv([l for l in r.stdout.splitlines()
                   if l.startswith("citekey=")][0] + "\n")["citekey"] if False else None
    # simpler: get it from `list`
    r = run("list", "--md-status", "ready", "--json")
    ck = json.loads(r.stdout)[0]["citekey"]
    # Attach an SI; assert paper.md doesn't change.
    lib, _ = libenv
    paper_md = lib / "papers" / ck / "paper.md"
    before = paper_md.read_text(encoding="utf-8")
    src = tmp_path / "si.pdf"; src.write_bytes(b"%PDF supp")
    assert run("add-si", ck, "--path", str(src)).returncode == 0
    after = paper_md.read_text(encoding="utf-8")
    assert before == after


# ---- notes -----------------------------------------------------------


def test_note_append_and_print_path(run, libenv):
    ck = _add(run, libenv, arxiv_id="2401.77777")
    assert run("note", ck, "--append", "an insight").returncode == 0
    r = run("note", ck)
    notes = Path(r.stdout.strip())
    assert notes.is_file()
    assert "an insight" in notes.read_text()


# ---- export ----------------------------------------------------------


def test_export_bibtex(run, libenv):
    ck = _add(run, libenv, title="Attention Is All You Need",
              author="Ashish Vaswani", year=2017, arxiv_id="1706.03762")
    r = run("export", ck, "--format", "bibtex")
    assert r.returncode == 0
    # Venue not set → @misc; either way, the citekey and arxiv eprint appear.
    assert ck in r.stdout
    assert "1706.03762" in r.stdout


def test_export_all_json(run, libenv):
    _add(run, libenv, title="P1")
    _add(run, libenv, title="P2", arxiv_id="2401.88888")
    r = run("export", "--all", "--format", "json")
    data = json.loads(r.stdout)
    assert len(data) == 2


def test_export_requires_selector(run, libenv):
    r = run("export")
    assert r.returncode != 0


# ---- rebuild-db ------------------------------------------------------


def test_rebuild_from_sidecars(run, libenv):
    lib, _ = libenv
    ck = _add(run, libenv, title="Roundtrip", arxiv_id="2401.99999")
    # Add a paper via hand-written sidecar.
    new_dir = lib / "papers" / "sidecar_direct"
    new_dir.mkdir()
    (new_dir / "metadata.json").write_text(json.dumps({
        "citekey": "sidecar_direct",
        "title": "Written directly",
        "authors": ["Direct Writer"],
        "year": 2025,
        "tags": ["sidecar"],
    }))
    (lib / "index.db").unlink()
    r = run("rebuild-db")
    assert r.returncode == 0
    assert "rebuilt: 2 papers" in r.stdout
    r = run("show", "sidecar_direct", "--json")
    data = json.loads(r.stdout)
    assert data["title"] == "Written directly"


def test_rebuild_restores_papers_md(run, libenv):
    """After rebuild, converted papers still have md_status=ready.

    The dispatched `add` call in this fixture uses author="A B", so the
    citekey generator picks last-name "B" and the resulting key is
    ``b2017attention``. The important thing is that after wiping and
    rebuilding, that key is still searchable — which requires
    ``rebuild.py`` to have restored ``papers_md`` from the on-disk
    ``paper.md``.
    """
    lib, _ = libenv
    pdf = make_pdf(lib.parent / "src.pdf")
    r = run("add", "--title", "Attention Is All You Need",
            "--author", "A B", "--year", "2017", "--arxiv-id", "1706.03762",
            "--pdf-path", str(pdf), "--and-convert")
    assert r.returncode == 0
    ck = json.loads(run("list", "--md-status", "ready", "--json").stdout)[0]["citekey"]
    (lib / "index.db").unlink()
    r = run("rebuild-db")
    assert r.returncode == 0
    assert "with paper.md" in r.stdout
    # Search still works after rebuild.
    r = run("search", "attention")
    assert ck in r.stdout


# ---- open ------------------------------------------------------------


def test_open_pdf(run, libenv):
    lib, _ = libenv
    pdf = make_pdf(lib.parent / "src.pdf")
    ck = _add(run, libenv, arxiv_id="2401.abc111", pdf=str(pdf))
    # `open pdf` should succeed; can't rely on any window actually opening
    # so we just check exit code.
    r = run("open", ck, "pdf")
    assert r.returncode == 0


def test_open_md_absent_errors(run, libenv):
    lib, _ = libenv
    pdf = make_pdf(lib.parent / "src.pdf")
    ck = _add(run, libenv, arxiv_id="2401.abc222", pdf=str(pdf))
    r = run("open", ck, "md")
    assert r.returncode != 0
    assert "convert" in r.stderr.lower()


def test_open_md_after_convert(run, libenv):
    lib, _ = libenv
    pdf = make_pdf(lib.parent / "src.pdf")
    r = run("add", "--title", "T", "--author", "A", "--year", "2024",
            "--pdf-path", str(pdf), "--and-convert")
    assert r.returncode == 0
    ck = json.loads(run("list", "--md-status", "ready", "--json").stdout)[0]["citekey"]
    r = run("open", ck, "md")
    assert r.returncode == 0
