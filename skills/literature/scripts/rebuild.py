#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///

"""`litlib rebuild-db` — regenerate index.db from sidecar metadata.json.

The `library/papers/<citekey>/metadata.json` files are the canonical
source of truth. This verb walks them, drops the tables, and re-inserts
everything.

Citekey stability: if the sidecar carries a `citekey` field, we honor
it verbatim — never regenerate. Missing citekey → derived from
directory name.
"""

import json
import shutil
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


def _insert_paper(meta: dict):
    citekey = meta["citekey"]
    dbmod.execute(
        """
        INSERT INTO papers (citekey, title, abstract, year, venue, venue_full,
                            doi, arxiv_id, s2_paper_id, url, pdf_path, notes_path, source)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            "sidecar",
        ),
    )
    _write_authors(citekey, meta.get("authors") or [])
    authors_flat = " ".join(str(a) for a in (meta.get("authors") or []))
    dbmod.execute(
        "UPDATE papers_fts SET authors_flat = ? WHERE citekey = ?",
        (authors_flat, citekey),
    )
    _apply_relations(citekey, meta)
    # Refresh tags_flat for FTS
    current = [
        r["name"] for r in dbmod.fetchall(
            "SELECT t.name FROM paper_tags pt JOIN tags t ON t.id = pt.tag_id "
            "WHERE pt.citekey = ? AND t.kind = 'tag' ORDER BY t.name", (citekey,))
    ]
    if current:
        dbmod.execute("UPDATE papers_fts SET tags_flat = ? WHERE citekey = ?",
                      (" ".join(current), citekey))


def run(args) -> int:
    cfg = config_mod.load_config()
    lib = Path(cfg["_library_path"])
    papers_dir = lib / "papers"
    if not papers_dir.exists():
        print(f"error: no papers dir at {papers_dir}", file=sys.stderr)
        return 1

    sidecars = sorted(papers_dir.glob("*/metadata.json"))
    if not sidecars:
        print("(no sidecar metadata.json files to rebuild from)", file=sys.stderr)
        return 1

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
        for sc in sidecars:
            try:
                data = json.loads(sc.read_text())
            except (OSError, json.JSONDecodeError) as e:
                errors.append((sc, str(e)))
                continue
            citekey = data.get("citekey") or sc.parent.name
            data["citekey"] = citekey
            # Fill in file paths if the on-disk file exists but sidecar omits them.
            paper_pdf = sc.parent / "paper.pdf"
            if paper_pdf.exists() and not data.get("pdf_path"):
                data["pdf_path"] = str(paper_pdf.relative_to(lib))
            notes_md = sc.parent / "notes.md"
            if notes_md.exists() and not data.get("notes_path"):
                data["notes_path"] = str(notes_md.relative_to(lib))
            try:
                with dbmod.Atomic():
                    _insert_paper(data)
                loaded += 1
            except Exception as e:  # noqa: BLE001
                errors.append((sc, str(e)))
        print(f"rebuilt: {loaded} papers, {len(errors)} errors")
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