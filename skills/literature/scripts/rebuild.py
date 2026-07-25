#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///

"""`sf-lit rebuild-db` — regenerate index.db from disk state.

The `library/papers/<citekey>/metadata.json` files are the canonical
source for the metadata catalog. If a paper also has `paper.md` +
`converter.json` on disk, its `papers_md` row and FTS index are
rebuilt too — the catalog and MD store come back in one pass.

Citekey stability: if the sidecar carries a `citekey` field, we honor
it verbatim — never regenerate. Missing citekey → derived from
directory name.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import config as config_mod  # noqa: E402
import db as dbmod  # noqa: E402
import init_db as init_mod  # noqa: E402


def _write_authors(citekey: str, authors: list[str]):
    dbmod.execute("DELETE FROM paper_authors WHERE citekey = ?", (citekey,))
    for i, name in enumerate(authors or [], start=1):
        name = str(name).strip()
        if not name:
            continue
        last = (name.split(",", 1)[0].strip() if "," in name
                else (name.split()[-1] if name.split() else name))
        dbmod.execute(
            "INSERT INTO authors (full_name, last_name) VALUES (?, ?) "
            "ON CONFLICT(full_name, orcid) DO NOTHING",
            (name, last),
        )
        row = dbmod.fetchone(
            "SELECT id FROM authors WHERE full_name = ? AND orcid IS NULL", (name,),
        )
        if row:
            dbmod.execute(
                "INSERT OR IGNORE INTO paper_authors (citekey, author_id, position) VALUES (?, ?, ?)",
                (citekey, row["id"], i),
            )


def _apply_relations(citekey: str, meta: dict):
    for t in meta.get("tags") or []:
        dbmod.execute("INSERT OR IGNORE INTO tags (name, kind) VALUES (?, 'tag')", (t,))
        row = dbmod.fetchone("SELECT id FROM tags WHERE name = ? AND kind = 'tag'", (t,))
        if row:
            dbmod.execute("INSERT OR IGNORE INTO paper_tags (citekey, tag_id) VALUES (?, ?)", (citekey, row["id"]))
    for c in meta.get("collections") or []:
        dbmod.execute("INSERT OR IGNORE INTO tags (name, kind) VALUES (?, 'collection')", (c,))
        row = dbmod.fetchone("SELECT id FROM tags WHERE name = ? AND kind = 'collection'", (c,))
        if row:
            dbmod.execute("INSERT OR IGNORE INTO paper_tags (citekey, tag_id) VALUES (?, ?)", (citekey, row["id"]))
    for gh in meta.get("github") or []:
        if not isinstance(gh, dict):
            continue
        owner, repo = gh.get("owner"), gh.get("repo")
        if not owner or not repo:
            continue
        dbmod.execute(
            "INSERT OR IGNORE INTO github_projects (citekey, owner, repo, url, stars, latest_release, readme_summary, last_checked_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (citekey, owner, repo,
             gh.get("url") or f"https://github.com/{owner}/{repo}",
             gh.get("stars"), gh.get("latest_release"),
             gh.get("readme_summary"), gh.get("last_checked_at")),
        )
    for n in meta.get("news") or []:
        if not isinstance(n, dict) or not n.get("url"):
            continue
        dbmod.execute(
            "INSERT OR IGNORE INTO news_links (citekey, url, title, source_name, published_at, kind) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (citekey, n["url"], n.get("title"), n.get("source_name"),
             n.get("published_at"), n.get("kind") or "news"),
        )
    for s in meta.get("si") or []:
        if not isinstance(s, dict):
            continue
        if not s.get("path") and not s.get("url"):
            continue
        dbmod.execute(
            "INSERT INTO si_files (citekey, path, label, source_url, checksum_sha256) VALUES (?, ?, ?, ?, ?)",
            (citekey, s.get("path") or "", s.get("label"), s.get("url"), s.get("checksum_sha256")),
        )


def _normalize_md(raw: bytes) -> str:
    """UTF-8 + strip BOM + CRLF → LF (mirrors convert.py's ingest)."""
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    text = raw.decode("utf-8", errors="replace")
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _ingest_md_if_present(citekey: str, paper_dir: Path) -> str:
    """If paper.md + converter.json exist, restore papers_md + md_status.

    Returns the effective md_status for the paper.
    """
    top = paper_dir / "paper.md"
    sidecar_p = paper_dir / "converter.json"
    if not top.is_file() or top.stat().st_size == 0:
        return "absent"
    sidecar: dict = {}
    if sidecar_p.is_file():
        try:
            sidecar = json.loads(sidecar_p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            sidecar = {}
    converter = sidecar.get("converter") or "unknown"
    text = _normalize_md(top.read_bytes())
    dbmod.execute(
        """
        INSERT INTO papers_md (citekey, markdown, converter, converter_version,
                                converted_at, pdf_sha256, char_count)
        VALUES (?, ?, ?, ?, coalesce(?, datetime('now')), ?, ?)
        """,
        (
            citekey, text, converter,
            sidecar.get("converter_version"),
            sidecar.get("converted_at"),
            sidecar.get("pdf_sha256"),
            len(text),
        ),
    )
    return "ready"


def _insert_paper(meta: dict, paper_dir: Path):
    citekey = meta["citekey"]
    dbmod.execute(
        """
        INSERT INTO papers (citekey, title, abstract, year, venue, venue_full,
                            doi, arxiv_id, s2_paper_id, url, pdf_path, notes_path,
                            source, md_status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'sidecar', 'absent')
        """,
        (
            citekey,
            meta.get("title") or "",
            meta.get("abstract"),
            meta.get("year"),
            meta.get("venue"),
            meta.get("venue_full"),
            meta.get("doi") or None,
            meta.get("arxiv_id") or None,
            meta.get("s2_paper_id") or None,
            meta.get("url"),
            meta.get("pdf_path"),
            meta.get("notes_path"),
        ),
    )
    _write_authors(citekey, meta.get("authors") or [])
    _apply_relations(citekey, meta)

    # If paper.md exists on disk, restore the MD side too.
    effective = _ingest_md_if_present(citekey, paper_dir)
    if effective == "ready":
        dbmod.execute(
            "UPDATE papers SET md_status = 'ready' WHERE citekey = ?",
            (citekey,),
        )


def run(args) -> int:
    cfg = config_mod.load_config()
    lib = Path(cfg["_library_path"])
    papers_dir = lib / "papers"
    if not papers_dir.exists():
        print(f"error: no papers dir at {papers_dir}", file=sys.stderr)
        return 3

    sidecars = sorted(papers_dir.glob("*/metadata.json"))
    if not sidecars:
        print("(no sidecar metadata.json files to rebuild from)", file=sys.stderr)
        return 3

    if args.dry_run:
        print(f"would rebuild {len(sidecars)} entries:")
        for sc in sidecars:
            print(f"  - {sc.relative_to(lib)}")
        return 0

    # Init a fresh DB (overwrites index.db)
    db_path = lib / "index.db"
    if db_path.exists():
        db_path.unlink()
    init_mod.init_library(lib)
    dbmod.connect(db_path)
    try:
        errors: list[tuple[Path, str]] = []
        loaded = 0
        md_loaded = 0
        for sc in sidecars:
            try:
                data = json.loads(sc.read_text())
            except (OSError, json.JSONDecodeError) as e:
                errors.append((sc, str(e)))
                continue
            citekey = data.get("citekey") or sc.parent.name
            data["citekey"] = citekey
            paper_pdf = sc.parent / "paper.pdf"
            if paper_pdf.exists() and not data.get("pdf_path"):
                data["pdf_path"] = str(paper_pdf.relative_to(lib))
            notes_md = sc.parent / "notes.md"
            if notes_md.exists() and not data.get("notes_path"):
                data["notes_path"] = str(notes_md.relative_to(lib))
            try:
                with dbmod.Atomic():
                    _insert_paper(data, sc.parent)
                loaded += 1
                if (sc.parent / "paper.md").is_file():
                    md_loaded += 1
            except Exception as e:  # noqa: BLE001
                errors.append((sc, str(e)))
        print(f"rebuilt: {loaded} papers ({md_loaded} with paper.md), {len(errors)} errors")
        for sc, err in errors:
            print(f"  ! {sc}: {err}", file=sys.stderr)
        return 0 if not errors else 1
    finally:
        dbmod.close()


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser("rebuild-db")
    ap.add_argument("--dry-run", action="store_true")
    sys.exit(run(ap.parse_args()))