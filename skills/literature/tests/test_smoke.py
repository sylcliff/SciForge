"""End-to-end smoke tests for the literature skill.

Runs against a fresh temporary library. No network access; no
`pdftotext` dependency. Exercises the CLI as an agent would.

Run with:
    pytest skills/literature/tests/ -x
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

SKILL_ROOT = Path(__file__).parent.parent
LITLIB = SKILL_ROOT / "scripts" / "litlib"


@pytest.fixture
def libdir(tmp_path: Path):
    """Fresh library + config pointing at it."""
    lib = tmp_path / "lib"
    cfg = tmp_path / "config.toml"
    cfg.write_text(f'[library]\npath = "{lib}"\n')
    env = os.environ.copy()
    env["SCIFORGE_CONFIG"] = str(cfg)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    # bootstrap
    _run(["init"], env=env, check=True)
    yield lib, env


def _run(argv, env=None, check=False, stdin=None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(LITLIB), *argv],
        env=env,
        capture_output=True,
        text=True,
        check=check,
        input=stdin,
    )


def _parse_kv(out: str) -> dict[str, str]:
    return dict(
        line.split("=", 1)
        for line in out.strip().splitlines()
        if "=" in line
    )


# ---- init / doctor -----------------------------------------------------


def test_init_creates_expected_tree(libdir):
    lib, env = libdir
    assert (lib / "index.db").exists()
    assert (lib / "papers").is_dir()
    assert (lib / "cache").is_dir()
    assert (lib / "collections").is_dir()


def test_doctor_passes(libdir):
    _, env = libdir
    r = _run(["doctor"], env=env)
    assert r.returncode == 0
    assert "index.db present" in r.stdout


# ---- add (CLI flags) --------------------------------------------------


def test_add_cli_flags(libdir):
    _, env = libdir
    r = _run(
        [
            "add", "--title", "Attention Is All You Need",
            "--author", "Ashish Vaswani", "--author", "Noam Shazeer",
            "--year", "2017", "--venue", "NeurIPS",
            "--arxiv-id", "1706.03762",
            "--tag", "ml", "--tag", "transformer",
        ],
        env=env,
    )
    assert r.returncode == 0, r.stderr
    kv = _parse_kv(r.stdout)
    assert kv["citekey"] == "vaswani2017attention"


def test_add_cli_flags_empty_title_rejected(libdir):
    _, env = libdir
    r = _run(["add", "--title", "   "], env=env)
    assert r.returncode == 1


# ---- add (meta-json) --------------------------------------------------


def test_add_meta_json_stdin(libdir):
    _, env = libdir
    blob = json.dumps({
        "title": "Array programming with NumPy",
        "authors": ["Charles Harris", "Others"],
        "year": 2020,
        "venue": "Nature",
        "doi": "10.1038/s41586-020-2649-2",
        "tags": ["numpy"],
    })
    r = _run(["add", "--meta-json", "-"], env=env, stdin=blob)
    assert r.returncode == 0, r.stderr
    kv = _parse_kv(r.stdout)
    assert kv["citekey"] == "harris2020array"


def test_add_meta_json_file(libdir, tmp_path):
    _, env = libdir
    meta = tmp_path / "m.json"
    meta.write_text(json.dumps({
        "title": "Test",
        "authors": ["Alice Wonderland"],
        "year": 2024,
    }))
    r = _run(["add", "--meta-json", str(meta)], env=env)
    assert r.returncode == 0, r.stderr


def test_add_meta_json_invalid_json(libdir):
    _, env = libdir
    r = _run(["add", "--meta-json", "-"], env=env, stdin="not json{")
    assert r.returncode == 1


# ---- add + pdf --------------------------------------------------------


def test_add_with_pdf_copy(libdir, tmp_path):
    lib, env = libdir
    src = tmp_path / "src.pdf"
    src.write_bytes(b"%PDF-1.4 fake")
    r = _run(
        ["add", "--title", "PDF Test", "--author", "Alice", "--year", "2024",
         "--pdf-path", str(src)],
        env=env,
    )
    assert r.returncode == 0, r.stderr
    kv = _parse_kv(r.stdout)
    assert Path(kv["pdf"]).is_file()
    assert src.exists(), "source PDF should still exist (copy semantics)"


def test_add_with_pdf_move(libdir, tmp_path):
    lib, env = libdir
    src = tmp_path / "movesrc.pdf"
    src.write_bytes(b"%PDF-1.4 move")
    r = _run(
        ["add", "--title", "Move Test", "--author", "Alice", "--year", "2024",
         "--pdf-path", str(src), "--move-pdf"],
        env=env,
    )
    assert r.returncode == 0, r.stderr
    assert not src.exists(), "source PDF should be moved"


def test_add_rejects_missing_pdf(libdir):
    _, env = libdir
    r = _run(
        ["add", "--title", "T", "--pdf-path", "/nonexistent/xyz.pdf"],
        env=env,
    )
    assert r.returncode == 1


# ---- dupe / upsert ----------------------------------------------------


def test_add_duplicate_returns_exit_2(libdir):
    _, env = libdir
    for _ in range(2):
        r = _run(
            ["add", "--title", "Foo", "--author", "A B",
             "--year", "2024", "--arxiv-id", "2401.99999"],
            env=env,
        )
    assert r.returncode == 2
    assert "already in library" in r.stderr


def test_upsert_merges(libdir):
    _, env = libdir
    _run(
        ["add", "--title", "Foo", "--author", "A B",
         "--year", "2024", "--arxiv-id", "2401.99999"],
        env=env,
        check=True,
    )
    blob = json.dumps({
        "arxiv_id": "2401.99999",
        "abstract": "UPDATED",
        "tags": ["new-tag"],
    })
    r = _run(["add", "--meta-json", "-", "--upsert"], env=env, stdin=blob)
    assert r.returncode == 0
    assert "upsert=1" in r.stdout

    # Verify abstract updated and tag added
    r = _run(["show", "2401.99999", "--json"], env=env)
    data = json.loads(r.stdout)
    assert data["abstract"] == "UPDATED"
    assert "new-tag" in data["tags"]


# ---- search / show ----------------------------------------------------


def test_search_and_show(libdir):
    _, env = libdir
    _run(
        ["add", "--title", "Attention Is All You Need", "--author", "Ashish Vaswani",
         "--year", "2017", "--arxiv-id", "1706.03762", "--tag", "ml"],
        env=env,
        check=True,
    )
    _run(
        ["add", "--title", "Array programming with NumPy", "--author", "Charles Harris",
         "--year", "2020", "--doi", "10.1038/s41586-020-2649-2", "--tag", "numpy"],
        env=env,
        check=True,
    )
    r = _run(["search", "attention"], env=env)
    assert "vaswani2017attention" in r.stdout
    assert "harris2020array" not in r.stdout

    # AND semantics
    r = _run(["search", "attention numpy"], env=env)
    assert "vaswani2017attention" not in r.stdout
    assert "harris2020array" not in r.stdout

    r = _run(["search", "--tag", "ml"], env=env)
    assert "vaswani2017attention" in r.stdout

    r = _run(["show", "1706.03762", "--json"], env=env)  # by arxiv_id
    data = json.loads(r.stdout)
    assert data["title"] == "Attention Is All You Need"
    assert data["authors"] == ["Ashish Vaswani"]


# ---- associate --------------------------------------------------------


def test_tag_add_remove(libdir):
    _, env = libdir
    _run(["add", "--title", "T", "--author", "A B", "--year", "2024"], env=env, check=True)
    ck = _parse_kv(
        _run(["add", "--title", "T2", "--author", "A B", "--year", "2024",
              "--arxiv-id", "2401.11111"], env=env, check=True).stdout
    )["citekey"]

    _run(["tag", ck, "hello"], env=env, check=True)
    r = _run(["search", "--tag", "hello"], env=env)
    assert ck in r.stdout

    _run(["tag", ck, "hello", "--remove"], env=env, check=True)
    r = _run(["search", "--tag", "hello"], env=env)
    assert ck not in r.stdout


def test_collection(libdir):
    _, env = libdir
    ck = _parse_kv(
        _run(["add", "--title", "T", "--author", "A B", "--year", "2024"], env=env, check=True).stdout
    )["citekey"]
    _run(["collection", "reading-2026", "add", ck], env=env, check=True)
    r = _run(["search", "--collection", "reading-2026"], env=env)
    assert ck in r.stdout


def test_add_github(libdir):
    _, env = libdir
    ck = _parse_kv(
        _run(["add", "--title", "T", "--author", "A B", "--year", "2024"], env=env, check=True).stdout
    )["citekey"]
    _run(
        ["add-github", ck, "--owner", "tensorflow", "--repo", "tensor2tensor", "--stars", "15000"],
        env=env,
        check=True,
    )
    r = _run(["show", ck, "--json"], env=env)
    data = json.loads(r.stdout)
    assert data["github"][0]["owner"] == "tensorflow"
    assert data["github"][0]["stars"] == 15000


def test_add_news_dedupes_on_url(libdir):
    _, env = libdir
    ck = _parse_kv(
        _run(["add", "--title", "T", "--author", "A B", "--year", "2024"], env=env, check=True).stdout
    )["citekey"]
    for _ in range(2):
        _run(["add-news", ck, "--url", "https://ex.com/x", "--kind", "blog"], env=env, check=True)
    r = _run(["show", ck, "--json"], env=env)
    data = json.loads(r.stdout)
    assert len(data["news"]) == 1


def test_add_si_copies_file(libdir, tmp_path):
    _, env = libdir
    ck = _parse_kv(
        _run(["add", "--title", "T", "--author", "A B", "--year", "2024"], env=env, check=True).stdout
    )["citekey"]
    src = tmp_path / "si.pdf"
    src.write_bytes(b"%PDF supplement")
    _run(["add-si", ck, "--path", str(src), "--label", "SI-1"], env=env, check=True)
    lib_root = Path(env["SCIFORGE_CONFIG"]).parent / "lib"
    si_files = list((lib_root / "papers" / ck / "si").iterdir())
    assert len(si_files) == 1


def test_note_append_and_default_prints_path(libdir):
    _, env = libdir
    ck = _parse_kv(
        _run(["add", "--title", "T", "--author", "A B", "--year", "2024"], env=env, check=True).stdout
    )["citekey"]
    _run(["note", ck, "--append", "an insight"], env=env, check=True)
    r = _run(["note", ck], env=env)
    notes_path = Path(r.stdout.strip())
    assert notes_path.is_file()
    assert "an insight" in notes_path.read_text()


# ---- export -----------------------------------------------------------


def test_export_bibtex(libdir):
    _, env = libdir
    _run(
        ["add", "--title", "Attention Is All You Need", "--author", "Ashish Vaswani",
         "--year", "2017", "--venue", "NeurIPS", "--arxiv-id", "1706.03762"],
        env=env,
        check=True,
    )
    r = _run(["export", "vaswani2017attention", "--format", "bibtex"], env=env)
    assert r.returncode == 0
    assert "@inproceedings{vaswani2017attention" in r.stdout
    assert "title = {Attention Is All You Need}" in r.stdout
    assert "eprint = {1706.03762}" in r.stdout


def test_export_json_all(libdir):
    _, env = libdir
    _run(["add", "--title", "P1", "--author", "A B", "--year", "2024"], env=env, check=True)
    _run(["add", "--title", "P2", "--author", "C D", "--year", "2023"], env=env, check=True)
    r = _run(["export", "--all", "--format", "json"], env=env)
    data = json.loads(r.stdout)
    assert len(data) == 2


def test_export_requires_selector(libdir):
    _, env = libdir
    r = _run(["export"], env=env)
    assert r.returncode != 0


# ---- rebuild-db -------------------------------------------------------


def test_rebuild_db_from_sidecar(libdir):
    lib, env = libdir
    ck = _parse_kv(
        _run(["add", "--title", "Roundtrip", "--author", "A B", "--year", "2024",
              "--tag", "keep"], env=env, check=True).stdout
    )["citekey"]

    # Add a paper via a hand-written sidecar
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

    r = _run(["rebuild-db"], env=env)
    assert r.returncode == 0
    assert "rebuilt: 2 papers" in r.stdout

    r = _run(["show", "sidecar_direct", "--json"], env=env)
    data = json.loads(r.stdout)
    assert data["title"] == "Written directly"
    assert "sidecar" in data["tags"]


# ---- citekey precompute ----------------------------------------------


def test_citekey_precompute(libdir):
    _, env = libdir
    r = _run(
        ["citekey", "--author", "Vaswani", "--year", "2017",
         "--title", "Attention Is All You Need"],
        env=env,
    )
    assert r.stdout.strip() == "vaswani2017attention"