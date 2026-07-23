#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///

"""Initialize / bootstrap the literature library.

Creates the directory tree, SQLite DB with schema, and writes an
effective-config snapshot.

Usage:
  init_db.py [--path <library dir>] [--force]

  --path   override the config's library path
  --force  drop and recreate the DB (data loss)
"""

import argparse
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import config  # noqa: E402
import db as dbmod  # noqa: E402

SCHEMA_VERSION = 1

SCHEMA_SQL = """
PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS papers (
    citekey       TEXT PRIMARY KEY,
    title         TEXT NOT NULL,
    abstract      TEXT,
    year          INTEGER,
    venue         TEXT,
    venue_full    TEXT,
    doi           TEXT UNIQUE,
    arxiv_id      TEXT UNIQUE,
    s2_paper_id   TEXT UNIQUE,
    url           TEXT,
    pdf_path      TEXT,
    added_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at    TEXT NOT NULL DEFAULT (datetime('now')),
    source        TEXT,
    notes_path    TEXT
);

CREATE INDEX IF NOT EXISTS idx_papers_year  ON papers(year);
CREATE INDEX IF NOT EXISTS idx_papers_venue ON papers(venue);

CREATE TABLE IF NOT EXISTS authors (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    full_name     TEXT NOT NULL,
    last_name     TEXT,
    orcid         TEXT,
    UNIQUE(full_name, orcid)
);

CREATE TABLE IF NOT EXISTS paper_authors (
    citekey       TEXT NOT NULL REFERENCES papers(citekey) ON DELETE CASCADE,
    author_id     INTEGER NOT NULL REFERENCES authors(id) ON DELETE CASCADE,
    position      INTEGER NOT NULL,
    is_corresponding INTEGER DEFAULT 0,
    PRIMARY KEY (citekey, author_id, position)
);

CREATE INDEX IF NOT EXISTS idx_pa_author ON paper_authors(author_id);

CREATE TABLE IF NOT EXISTS tags (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT UNIQUE NOT NULL,
    kind          TEXT NOT NULL DEFAULT 'tag'
);

CREATE TABLE IF NOT EXISTS paper_tags (
    citekey       TEXT NOT NULL REFERENCES papers(citekey) ON DELETE CASCADE,
    tag_id        INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
    PRIMARY KEY (citekey, tag_id)
);

CREATE TABLE IF NOT EXISTS si_files (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    citekey       TEXT NOT NULL REFERENCES papers(citekey) ON DELETE CASCADE,
    path          TEXT NOT NULL,
    label         TEXT,
    source_url    TEXT,
    checksum_sha256 TEXT
);

CREATE TABLE IF NOT EXISTS github_projects (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    citekey       TEXT NOT NULL REFERENCES papers(citekey) ON DELETE CASCADE,
    owner         TEXT NOT NULL,
    repo          TEXT NOT NULL,
    url           TEXT NOT NULL,
    stars         INTEGER,
    latest_release TEXT,
    readme_summary TEXT,
    last_checked_at TEXT,
    UNIQUE(citekey, owner, repo)
);

CREATE TABLE IF NOT EXISTS news_links (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    citekey       TEXT NOT NULL REFERENCES papers(citekey) ON DELETE CASCADE,
    url           TEXT NOT NULL,
    title         TEXT,
    source_name   TEXT,
    published_at  TEXT,
    kind          TEXT,
    discovered_at TEXT DEFAULT (datetime('now')),
    UNIQUE(citekey, url)
);

CREATE TABLE IF NOT EXISTS citations (
    citing_citekey TEXT NOT NULL REFERENCES papers(citekey) ON DELETE CASCADE,
    cited_doi      TEXT,
    cited_arxiv_id TEXT,
    cited_title    TEXT,
    cited_citekey  TEXT
);

CREATE VIRTUAL TABLE IF NOT EXISTS papers_fts USING fts5(
    citekey UNINDEXED,
    title, abstract, authors_flat, venue, year_str, tags_flat,
    tokenize='porter unicode61'
);

-- FTS synchronization triggers.
-- Insert / delete on papers keep an FTS row per citekey.
CREATE TRIGGER IF NOT EXISTS papers_ai AFTER INSERT ON papers BEGIN
    INSERT INTO papers_fts(citekey, title, abstract, authors_flat, venue, year_str, tags_flat)
    VALUES (new.citekey, new.title, coalesce(new.abstract, ''), '', coalesce(new.venue, ''), coalesce(cast(new.year as TEXT), ''), '');
END;

CREATE TRIGGER IF NOT EXISTS papers_ad AFTER DELETE ON papers BEGIN
    DELETE FROM papers_fts WHERE citekey = old.citekey;
END;

CREATE TRIGGER IF NOT EXISTS papers_au AFTER UPDATE ON papers BEGIN
    DELETE FROM papers_fts WHERE citekey = old.citekey;
    INSERT INTO papers_fts(citekey, title, abstract, authors_flat, venue, year_str, tags_flat)
    VALUES (new.citekey, new.title, coalesce(new.abstract, ''), '', coalesce(new.venue, ''), coalesce(cast(new.year as TEXT), ''), '');
END;
"""


def init_library(library_path: Path, force: bool = False) -> None:
    """Create the library tree and (optionally recreate) the DB."""
    library_path = library_path.expanduser().resolve()
    library_path.mkdir(parents=True, exist_ok=True)
    (library_path / "papers").mkdir(exist_ok=True)
    (library_path / "collections").mkdir(exist_ok=True)
    for sub in ("arxiv", "crossref", "s2", "github", "news"):
        (library_path / "cache" / sub).mkdir(parents=True, exist_ok=True)

    db_path = library_path / "index.db"
    if force and db_path.exists():
        db_path.unlink()

    conn = dbmod.connect(db_path)
    try:
        conn.executescript(SCHEMA_SQL)
        conn.execute(
            "INSERT INTO meta(key, value) VALUES('schema_version', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (str(SCHEMA_VERSION),),
        )
        conn.commit()
    finally:
        dbmod.close()

    (library_path / ".litlib-version").write_text(f"{SCHEMA_VERSION}\n")

    # Write effective config snapshot (as a comment header + best-effort dump)
    snap = library_path / "config.effective.toml"
    lines = [
        "# Effective SciForge literature config snapshot.",
        "# Regenerated by `litlib init`.",
        f"# library.path = {library_path}",
        f"# schema_version = {SCHEMA_VERSION}",
        "",
    ]
    snap.write_text("\n".join(lines))


def main():
    ap = argparse.ArgumentParser(prog="init_db", description=__doc__)
    ap.add_argument("--path", help="Override library path")
    ap.add_argument("--force", action="store_true", help="Recreate DB (data loss)")
    args = ap.parse_args()

    cfg = config.load_config()
    if args.path:
        lib = Path(args.path).expanduser().resolve()
    else:
        lib = Path(cfg["_library_path"])

    init_library(lib, force=args.force)
    print(f"library={lib}")
    print(f"db={lib / 'index.db'}")
    print(f"schema_version={SCHEMA_VERSION}")


if __name__ == "__main__":
    main()