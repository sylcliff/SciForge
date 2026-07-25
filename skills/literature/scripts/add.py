#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///

"""`litlib add` — ingest a paper into the local library.

Scope reminder: this skill does not fetch external data. Companion
skills (arxiv-fetch, doi-fetch, github-fetch, pdf-extract) are expected
to hand this skill fully-assembled metadata via one of four entrypoints:

  1. `--meta-json PATH` (or `-` for stdin)     — JSON blob
  2. `--title ... [--author ... --arxiv-id ...]` — CLI flags
  3. `--title ... --manual`                    — placeholder-only entry
  4. Sidecar direct write + `litlib rebuild-db` — external skill writes
     `library/papers/<citekey>/metadata.json` itself.

All four routes normalize to a single dict, then flow through
`_write_paper_files` + `_insert_paper`.

`add` is strictly the **catalog** step: it never runs PDF→Markdown
conversion. A freshly-added paper's ``md_status`` is ``absent`` — call
``litlib convert <citekey>`` to render its ``paper.md`` and make it
searchable. Pass ``--and-convert`` on ``add`` for the two-step
convenience shortcut.

Exit codes:
  0  success
  2  duplicate (arxiv_id/doi/citekey already in library); pass --upsert to merge
  3  reserved for ambiguous matches (unused now; kept for future)
  1  generic error
"""

import json
import shutil
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent))
import config as config_mod  # noqa: E402
import db as dbmod  # noqa: E402
import ids as ids_mod  # noqa: E402
import init_db as init_mod  # noqa: E402

# ---- Metadata schema ---------------------------------------------------

# The set of fields understood by `add --meta-json`. Anything else in
# the JSON is ignored (with a warning) so future extensions don't break.
KNOWN_FIELDS = {
    "title", "authors", "abstract", "year",
    "venue", "venue_full",
    "doi", "arxiv_id", "s2_paper_id",
    "url", "notes",
    "tags", "collections",
    "github", "news", "si",
    "citekey",  # optional override; usually generated
    "pdf_path",  # source path on disk; the file will be copied into the library
}

LIST_FIELDS = {"authors", "tags", "collections", "github", "news", "si"}


def _ensure_library(lib: Path):
    if not (lib / "index.db").exists():
        init_mod.init_library(lib)


def _find_existing_by_identifier(meta: dict) -> str | None:
    """Return citekey if any of (arxiv_id, doi, s2_paper_id) already exists."""
    for field, val in (
        ("arxiv_id", meta.get("arxiv_id")),
        ("doi", meta.get("doi")),
        ("s2_paper_id", meta.get("s2_paper_id")),
    ):
        if not val:
            continue
        row = dbmod.fetchone(f"SELECT citekey FROM papers WHERE {field} = ?", (val,))
        if row:
            return row["citekey"]
    return None


def _all_citekeys() -> set[str]:
    return {r["citekey"] for r in dbmod.fetchall("SELECT citekey FROM papers")}


# ---- Normalization -----------------------------------------------------


def _normalize_authors(raw: Any) -> list[str]:
    """Accept ['A B', 'C D'] or 'A B, C D' or 'A B; C D' etc."""
    if not raw:
        return []
    if isinstance(raw, str):
        parts = []
        for chunk in raw.split(";"):
            parts.extend(p.strip() for p in chunk.split(",") if p.strip())
        return parts
    if isinstance(raw, list):
        return [str(x).strip() for x in raw if str(x).strip()]
    return [str(raw).strip()]


def _normalize_meta(raw: dict) -> dict:
    """Coerce keys/types into the canonical shape. Unknown fields warned."""
    meta: dict[str, Any] = {}

    for k, v in raw.items():
        if k not in KNOWN_FIELDS:
            print(f"warn: ignoring unknown metadata field {k!r}", file=sys.stderr)
            continue
        meta[k] = v

    # Types & defaults
    meta["title"] = (meta.get("title") or "").strip()
    meta["authors"] = _normalize_authors(meta.get("authors"))
    meta["abstract"] = (meta.get("abstract") or "") or None
    if meta.get("year") is not None:
        try:
            meta["year"] = int(meta["year"])
        except (TypeError, ValueError):
            print(f"warn: dropping non-integer year {meta['year']!r}", file=sys.stderr)
            meta["year"] = None
    for text_field in ("venue", "venue_full", "doi", "arxiv_id", "s2_paper_id", "url"):
        val = meta.get(text_field)
        meta[text_field] = str(val).strip() if val else None
    if meta.get("doi"):
        meta["doi"] = meta["doi"].lower()
    if meta.get("arxiv_id"):
        # strip any accidental "v1"/"v2" suffix from external skills
        import re
        meta["arxiv_id"] = re.sub(r"v\d+$", "", meta["arxiv_id"])

    for list_field in LIST_FIELDS:
        val = meta.get(list_field)
        if val is None:
            meta[list_field] = []
        elif isinstance(val, list):
            meta[list_field] = val
        else:
            meta[list_field] = [val]

    # `tags` / `collections` are just strings; strip empties
    meta["tags"] = [str(t).strip() for t in meta["tags"] if str(t).strip()]
    meta["collections"] = [str(t).strip() for t in meta["collections"] if str(t).strip()]

    return meta


def _meta_from_args(args) -> dict:
    """Build a meta dict from CLI --flag arguments."""
    meta = {
        "title": args.title,
        "authors": list(args.author or []),
        "year": args.year,
        "venue": args.venue,
        "venue_full": args.venue_full,
        "abstract": args.abstract,
        "doi": args.doi,
        "arxiv_id": args.arxiv_id,
        "s2_paper_id": args.s2_id,
        "url": args.url,
        "tags": list(args.tag or []),
        "collections": list(args.collection or []),
        "notes": args.notes,
    }
    return _normalize_meta({k: v for k, v in meta.items() if v is not None})


def _load_meta_json(path_arg: str) -> dict:
    """Load a metadata JSON blob from a file or stdin."""
    if path_arg == "-":
        raw = sys.stdin.read()
    else:
        raw = Path(path_arg).expanduser().read_text()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(f"invalid --meta-json input: {e}") from e
    if not isinstance(data, dict):
        raise ValueError("--meta-json must be a JSON object at the top level")
    return _normalize_meta(data)


# ---- Persistence ------------------------------------------------------


def _copy_pdf_into_library(src: Path, dest_dir: Path, move: bool = False) -> Path:
    """Copy (or move) the PDF into paper_dir/paper.pdf. Return relative path."""
    src = src.expanduser().resolve()
    if not src.is_file():
        raise FileNotFoundError(f"PDF source not found: {src}")
    if src.stat().st_size == 0:
        raise ValueError(f"PDF source is empty: {src}")

    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / "paper.pdf"
    shutil.copyfile(src, dest)
    if move:
        src.unlink(missing_ok=True)
    return dest


def _write_paper_files(lib: Path, citekey: str, meta: dict, pdf_src: Path | None,
                       move_pdf: bool = False, seed_notes: str | None = None) -> dict:
    """Write metadata.json, notes.md, and (optionally) copy in the PDF.

    Returns a copy of `meta` with `pdf_path` / `notes_path` filled in.
    """
    paper_dir = lib / "papers" / citekey
    paper_dir.mkdir(parents=True, exist_ok=True)

    # notes.md — created only if missing (never overwritten)
    notes = paper_dir / "notes.md"
    if not notes.exists():
        title = meta.get("title") or "(untitled)"
        body = f"# {title}\n\ncitekey: `{citekey}`\n\n## Summary\n\n_TODO_\n\n## Notes\n\n"
        notes.write_text(body)
    meta = dict(meta)
    meta["notes_path"] = str(notes.relative_to(lib))

    if seed_notes:
        with notes.open("a") as f:
            f.write(f"\n{seed_notes}\n")

    if pdf_src is not None:
        pdf_dest = _copy_pdf_into_library(pdf_src, paper_dir, move=move_pdf)
        meta["pdf_path"] = str(pdf_dest.relative_to(lib))

    # sidecar
    sidecar = {k: v for k, v in meta.items() if not k.startswith("_")}
    sidecar["citekey"] = citekey
    (paper_dir / "metadata.json").write_text(
        json.dumps(sidecar, indent=2, sort_keys=True, ensure_ascii=False)
    )

    return meta


def _insert_paper(citekey: str, meta: dict, source: str):
    """Insert into papers + authors + paper_authors within one transaction.

    New rows land with ``md_status='absent'`` — a call to
    ``litlib convert`` is required to render `paper.md` and enable
    full-text search on the body.
    """
    authors = meta.get("authors") or []
    with dbmod.Atomic():
        dbmod.execute(
            """
            INSERT INTO papers (citekey, title, abstract, year, venue, venue_full,
                                doi, arxiv_id, s2_paper_id, url, pdf_path,
                                notes_path, source, md_status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'absent')
            """,
            (
                citekey,
                meta.get("title", ""),
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
                source,
            ),
        )
        _write_authors(citekey, authors)


def _write_authors(citekey: str, authors: list[str]):
    """Insert / link authors for this paper. Assumes a live transaction."""
    dbmod.execute("DELETE FROM paper_authors WHERE citekey = ?", (citekey,))
    for i, name in enumerate(authors, start=1):
        name = name.strip()
        if not name:
            continue
        last = (
            name.split(",", 1)[0].strip()
            if "," in name
            else (name.split()[-1] if name.split() else name)
        )
        dbmod.execute(
            "INSERT INTO authors (full_name, last_name) VALUES (?, ?) "
            "ON CONFLICT(full_name, orcid) DO NOTHING",
            (name, last),
        )
        row = dbmod.fetchone(
            "SELECT id FROM authors WHERE full_name = ? AND orcid IS NULL",
            (name,),
        )
        if row:
            dbmod.execute(
                "INSERT OR IGNORE INTO paper_authors (citekey, author_id, position) "
                "VALUES (?, ?, ?)",
                (citekey, row["id"], i),
            )


def _apply_tags(citekey: str, tags: list[str], kind: str = "tag"):
    """Idempotent tag/collection linkage."""
    if not tags:
        return
    with dbmod.Atomic():
        for t in tags:
            dbmod.execute(
                "INSERT OR IGNORE INTO tags (name, kind) VALUES (?, ?)",
                (t, kind),
            )
            row = dbmod.fetchone(
                "SELECT id FROM tags WHERE name = ? AND kind = ?",
                (t, kind),
            )
            if row:
                dbmod.execute(
                    "INSERT OR IGNORE INTO paper_tags (citekey, tag_id) VALUES (?, ?)",
                    (citekey, row["id"]),
                )


def _write_associations(citekey: str, meta: dict):
    """Persist github / news / si entries embedded in the meta blob."""
    with dbmod.Atomic():
        for gh in meta.get("github") or []:
            if not isinstance(gh, dict):
                continue
            owner = gh.get("owner")
            repo = gh.get("repo")
            if not owner or not repo:
                continue
            dbmod.execute(
                """
                INSERT INTO github_projects
                  (citekey, owner, repo, url, stars, latest_release, readme_summary, last_checked_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(citekey, owner, repo) DO UPDATE SET
                  stars = coalesce(excluded.stars, github_projects.stars),
                  latest_release = coalesce(excluded.latest_release, github_projects.latest_release),
                  readme_summary = coalesce(excluded.readme_summary, github_projects.readme_summary),
                  last_checked_at = coalesce(excluded.last_checked_at, github_projects.last_checked_at)
                """,
                (
                    citekey, owner, repo,
                    gh.get("url") or f"https://github.com/{owner}/{repo}",
                    gh.get("stars"),
                    gh.get("latest_release"),
                    gh.get("readme_summary"),
                    gh.get("last_checked_at"),
                ),
            )

        for n in meta.get("news") or []:
            if not isinstance(n, dict) or not n.get("url"):
                continue
            dbmod.execute(
                """
                INSERT OR IGNORE INTO news_links
                  (citekey, url, title, source_name, published_at, kind)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    citekey, n["url"],
                    n.get("title"), n.get("source_name"),
                    n.get("published_at"),
                    n.get("kind") or "news",
                ),
            )

        for s in meta.get("si") or []:
            if not isinstance(s, dict):
                continue
            path = s.get("path")
            if not path:
                # Allow URL-only SI records (external skill hasn't downloaded yet).
                if not s.get("url"):
                    continue
            dbmod.execute(
                """
                INSERT INTO si_files (citekey, path, label, source_url, checksum_sha256)
                VALUES (?, ?, ?, ?, ?)
                """,
                (citekey, path or "", s.get("label"), s.get("url"), s.get("checksum_sha256")),
            )


# ---- Upsert helpers ---------------------------------------------------


def _merge_meta_upsert(existing: dict, incoming: dict) -> dict:
    """Non-empty overwrites; list fields union.

    `existing` comes from the DB row (dict). `incoming` is the fresh
    normalized meta from the caller. Return a merged dict suitable for
    UPDATE papers.
    """
    merged = dict(existing)
    for k, v in incoming.items():
        if v is None or v == "" or v == []:
            continue
        if k in LIST_FIELDS and isinstance(v, list):
            prev = merged.get(k) or []
            if not isinstance(prev, list):
                prev = []
            # Deduplicate while preserving order.
            seen = set()
            merged[k] = [
                x for x in list(prev) + list(v)
                if not (str(x) in seen or seen.add(str(x)))  # type: ignore[func-returns-value]
            ]
        else:
            merged[k] = v
    return merged


def _upsert_paper(citekey: str, existing_row: dict, meta: dict, source: str):
    """Update fields of an existing paper (identifier match)."""
    merged = _merge_meta_upsert(existing_row, meta)
    with dbmod.Atomic():
        dbmod.execute(
            """
            UPDATE papers SET
              title = ?, abstract = coalesce(?, abstract), year = coalesce(?, year),
              venue = coalesce(?, venue), venue_full = coalesce(?, venue_full),
              doi = coalesce(?, doi), arxiv_id = coalesce(?, arxiv_id),
              s2_paper_id = coalesce(?, s2_paper_id), url = coalesce(?, url),
              pdf_path = coalesce(?, pdf_path),
              source = coalesce(?, source),
              updated_at = datetime('now')
            WHERE citekey = ?
            """,
            (
                merged.get("title") or existing_row.get("title") or "",
                merged.get("abstract"),
                merged.get("year"),
                merged.get("venue"),
                merged.get("venue_full"),
                merged.get("doi"),
                merged.get("arxiv_id"),
                merged.get("s2_paper_id"),
                merged.get("url"),
                merged.get("pdf_path"),
                source,
                citekey,
            ),
        )
        if merged.get("authors"):
            _write_authors(citekey, merged["authors"])


# ---- Entry points -----------------------------------------------------


def _do_add(meta: dict, args, lib: Path, source: str) -> int:
    """Common flow after meta has been normalized."""
    if not meta.get("title"):
        if args.upsert:
            # Upsert may be used to add fields to an existing entry;
            # the title is already in the DB.
            meta["title"] = ""
        else:
            print("error: metadata is missing a non-empty `title`", file=sys.stderr)
            return 1

    # Check duplicate by identifier (arxiv_id / doi / s2)
    existing_key = _find_existing_by_identifier(meta)

    # Also consider explicit citekey override
    if not existing_key and meta.get("citekey"):
        row = dbmod.fetchone(
            "SELECT citekey FROM papers WHERE citekey = ?", (meta["citekey"],)
        )
        if row:
            existing_key = row["citekey"]

    if existing_key:
        if not args.upsert:
            print(f"citekey={existing_key}")
            print(
                f"error: paper already in library ({existing_key}); "
                f"pass --upsert to merge",
                file=sys.stderr,
            )
            return 2

        # Upsert path
        existing_row = dict(
            dbmod.fetchone("SELECT * FROM papers WHERE citekey = ?", (existing_key,))
        )
        pdf_src = Path(args.pdf_path).expanduser() if args.pdf_path else None
        paper_dir = lib / "papers" / existing_key

        if pdf_src is not None:
            new_pdf = _copy_pdf_into_library(pdf_src, paper_dir, move=args.move_pdf)
            meta["pdf_path"] = str(new_pdf.relative_to(lib))

        # Rewrite sidecar with merged data
        merged = _merge_meta_upsert(existing_row, meta)
        merged["citekey"] = existing_key
        (paper_dir / "metadata.json").write_text(
            json.dumps(
                {k: v for k, v in merged.items() if not k.startswith("_")},
                indent=2, sort_keys=True, ensure_ascii=False,
            )
        )

        _upsert_paper(existing_key, existing_row, meta, source=source)
        if meta.get("tags"):
            _apply_tags(existing_key, meta["tags"], kind="tag")
        if meta.get("collections"):
            _apply_tags(existing_key, meta["collections"], kind="collection")
        _write_associations(existing_key, meta)

        print(f"citekey={existing_key}")
        print(
            f"pdf={lib / meta['pdf_path']}"
            if meta.get("pdf_path")
            else f"pdf={lib / (existing_row.get('pdf_path') or '')}" if existing_row.get("pdf_path")
            else "pdf=(not present)"
        )
        print(f"notes={lib / (existing_row.get('notes_path') or '')}")
        print("upsert=1")
        return 0

    # Fresh insert
    citekey_override = meta.get("citekey")
    if citekey_override:
        citekey = citekey_override
    else:
        authors = meta.get("authors") or ["Anon"]
        base = ids_mod.make_citekey(authors[0], meta.get("year"), meta.get("title", ""))
        citekey = ids_mod.suffix_for_collision(base, _all_citekeys())

    pdf_src = Path(args.pdf_path).expanduser() if args.pdf_path else None
    meta_written = _write_paper_files(
        lib, citekey, meta,
        pdf_src=pdf_src,
        move_pdf=args.move_pdf,
        seed_notes=meta.get("notes"),
    )
    _insert_paper(citekey, meta_written, source=source)

    if meta.get("tags"):
        _apply_tags(citekey, meta["tags"], kind="tag")
    if meta.get("collections"):
        _apply_tags(citekey, meta["collections"], kind="collection")
    _write_associations(citekey, meta)

    print(f"citekey={citekey}")
    if meta_written.get("pdf_path"):
        print(f"pdf={lib / meta_written['pdf_path']}")
    else:
        print("pdf=(not provided)")
    print(f"notes={lib / meta_written['notes_path']}")
    print("md_status=absent")
    if meta_written.get("pdf_path"):
        print(f"hint=run `litlib convert {citekey}` to enable full-text search")
    return 0


def run(args) -> int:
    cfg = config_mod.load_config()
    lib = Path(cfg["_library_path"])
    _ensure_library(lib)
    dbmod.connect(lib / "index.db")
    added_citekey: str | None = None
    try:
        # Which entrypoint?
        if args.meta_json:
            try:
                meta = _load_meta_json(args.meta_json)
            except (OSError, ValueError) as e:
                print(f"error: {e}", file=sys.stderr)
                return 1
            source = "meta-json"
        elif args.title:
            meta = _meta_from_args(args)
            source = "manual" if args.manual else "cli"
        else:
            print(
                "error: one of --meta-json / --title is required",
                file=sys.stderr,
            )
            return 1

        # Capture the resolved citekey so `--and-convert` can hand it off.
        # We defer conversion until after the DB connection is closed so
        # `convert` can open the DB itself without contention.
        rc = _do_add(meta, args, lib, source=source)
        if rc == 0 and getattr(args, "and_convert", False):
            # `_do_add` printed citekey=<key> as its first line.
            # Re-derive from meta the same way to avoid re-parsing stdout.
            existing = _find_existing_by_identifier(meta) if not args.upsert else None
            if meta.get("citekey"):
                added_citekey = meta["citekey"]
            elif existing:
                added_citekey = existing
            else:
                # Regenerate; matches `_do_add`'s logic.
                authors = meta.get("authors") or ["Anon"]
                base = ids_mod.make_citekey(authors[0], meta.get("year"), meta.get("title", ""))
                added_citekey = ids_mod.suffix_for_collision(base, _all_citekeys() - {base})
                # If suffix_for_collision moved us but the row landed under
                # the original base, fall back to a fresh DB lookup.
                if not dbmod.fetchone(
                    "SELECT 1 FROM papers WHERE citekey = ?", (added_citekey,)
                ):
                    row = dbmod.fetchone(
                        "SELECT citekey FROM papers ORDER BY added_at DESC LIMIT 1"
                    )
                    if row:
                        added_citekey = row["citekey"]
        return rc
    finally:
        dbmod.close()
        # Only spawn `convert` after the DB is closed and we know an add
        # actually succeeded. Any errors here are surfaced to the user
        # but don't undo the catalog insert.
        if added_citekey:
            _spawn_convert(added_citekey, args)


def _spawn_convert(citekey: str, args) -> None:
    """Run ``litlib convert <citekey>`` as a subprocess (for --and-convert)."""
    import subprocess
    here = Path(__file__).parent
    argv = [sys.executable, str(here / "litlib"), "convert", citekey]
    if getattr(args, "converter", None):
        argv.extend(["--converter", args.converter])
    print(f"and-convert: running `litlib convert {citekey}`", file=sys.stderr)
    proc = subprocess.run(argv)
    if proc.returncode != 0:
        print(
            f"and-convert: convert exited {proc.returncode} — "
            f"catalog entry is fine, retry with `litlib convert {citekey} --reconvert`",
            file=sys.stderr,
        )


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser("add")
    grp = ap.add_mutually_exclusive_group(required=True)
    grp.add_argument("--meta-json", help="JSON metadata blob (path or '-')")
    grp.add_argument("--title", help="Paper title (CLI/manual mode)")
    ap.add_argument("--author", action="append", default=[])
    ap.add_argument("--year", type=int)
    ap.add_argument("--venue")
    ap.add_argument("--venue-full", dest="venue_full")
    ap.add_argument("--abstract")
    ap.add_argument("--doi")
    ap.add_argument("--arxiv-id", dest="arxiv_id")
    ap.add_argument("--s2-id", dest="s2_id")
    ap.add_argument("--url")
    ap.add_argument("--tag", action="append", default=[])
    ap.add_argument("--collection", action="append", default=[])
    ap.add_argument("--notes")
    ap.add_argument("--pdf-path", dest="pdf_path", help="Local PDF to ingest")
    ap.add_argument("--move-pdf", dest="move_pdf", action="store_true")
    ap.add_argument("--manual", action="store_true", help="Explicit manual/placeholder entry")
    ap.add_argument("--upsert", action="store_true")
    ap.add_argument(
        "--and-convert", dest="and_convert", action="store_true",
        help="Run `litlib convert` immediately after ingest (two-phase sugar)",
    )
    ap.add_argument(
        "--converter", choices=["mineru", "docling"], default=None,
        help="With --and-convert: pick the converter (defaults to config)",
    )
    sys.exit(run(ap.parse_args()))