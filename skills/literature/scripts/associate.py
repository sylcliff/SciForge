#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///

"""`litlib tag / collection / note / add-si / add-github / add-news`

All are pure write operations — no external fetching. Companion skills
call `add-github --owner O --repo R --stars N` etc. after they have
done the actual fetching.
"""

import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import config as config_mod  # noqa: E402
import db as dbmod  # noqa: E402


def _resolve_key(key: str) -> str | None:
    """Return citekey if a paper matches key (citekey / arxiv_id / doi)."""
    for field in ("citekey", "arxiv_id", "doi"):
        row = dbmod.fetchone(f"SELECT citekey FROM papers WHERE {field} = ?", (key,))
        if row:
            return row["citekey"]
    return None


def _require_paper(key: str) -> str:
    ck = _resolve_key(key)
    if ck is None:
        print(f"error: no paper matches {key!r}", file=sys.stderr)
        sys.exit(1)
    return ck


def cmd_tag(key: str, tag: str, remove: bool):
    ck = _require_paper(key)
    with dbmod.Atomic():
        if remove:
            row = dbmod.fetchone(
                "SELECT id FROM tags WHERE name = ? AND kind = 'tag'", (tag,)
            )
            if row:
                dbmod.execute("DELETE FROM paper_tags WHERE citekey = ? AND tag_id = ?", (ck, row["id"]))
        else:
            dbmod.execute("INSERT OR IGNORE INTO tags (name, kind) VALUES (?, 'tag')", (tag,))
            row = dbmod.fetchone("SELECT id FROM tags WHERE name = ? AND kind = 'tag'", (tag,))
            if row:
                dbmod.execute("INSERT OR IGNORE INTO paper_tags (citekey, tag_id) VALUES (?, ?)", (ck, row["id"]))
    if remove:
        print(f"removed tag {tag!r} from {ck}")
    else:
        print(f"tagged {ck} with {tag!r}")


def cmd_collection(slug: str, action: str, key: str):
    ck = _require_paper(key)
    with dbmod.Atomic():
        if action == "add":
            dbmod.execute("INSERT OR IGNORE INTO tags (name, kind) VALUES (?, 'collection')", (slug,))
            row = dbmod.fetchone("SELECT id FROM tags WHERE name = ? AND kind = 'collection'", (slug,))
            if row:
                dbmod.execute("INSERT OR IGNORE INTO paper_tags (citekey, tag_id) VALUES (?, ?)", (ck, row["id"]))
            print(f"added {ck} to collection {slug!r}")
        else:  # remove
            row = dbmod.fetchone("SELECT id FROM tags WHERE name = ? AND kind = 'collection'", (slug,))
            if row:
                dbmod.execute("DELETE FROM paper_tags WHERE citekey = ? AND tag_id = ?", (ck, row["id"]))
            print(f"removed {ck} from collection {slug!r}")


def cmd_note(key: str, open_: bool, append: str | None, set_from: str | None):
    ck = _require_paper(key)
    row = dbmod.fetchone("SELECT notes_path FROM papers WHERE citekey = ?", (ck,))
    if not row or not row["notes_path"]:
        # If no notes_path, create one
        cfg = config_mod.load_config()
        lib = Path(cfg["_library_path"])
        notes = lib / "papers" / ck / "notes.md"
        if not notes.exists():
            p_row = dbmod.fetchone("SELECT title FROM papers WHERE citekey = ?", (ck,))
            title = p_row["title"] if p_row else ck
            notes.parent.mkdir(parents=True, exist_ok=True)
            notes.write_text(f"# {title}\n\ncitekey: `{ck}`\n\n## Summary\n\n_TODO_\n\n## Notes\n\n")
        with dbmod.Atomic():
            dbmod.execute("UPDATE papers SET notes_path = ? WHERE citekey = ?",
                          (str(notes.relative_to(lib)), ck))
        row = dbmod.fetchone("SELECT notes_path FROM papers WHERE citekey = ?", (ck,))

    cfg = config_mod.load_config()
    lib = Path(cfg["_library_path"])
    notes_path = lib / row["notes_path"]

    if open_ or (not append and not set_from):
        # Default: print the path so the caller can open it
        print(notes_path)
    if append:
        from datetime import datetime
        ts = datetime.now().strftime("%Y-%m-%d %H:%M")
        with notes_path.open("a") as f:
            f.write(f"\n[{ts}] {append}\n")
        print(f"appended note to {ck}")
    if set_from:
        src = Path(set_from).expanduser()
        if not src.is_file():
            print(f"error: {set_from} not found", file=sys.stderr)
            sys.exit(1)
        shutil.copyfile(src, notes_path)
        print(f"set notes from {set_from}")


def cmd_add_si(key: str, path: str | None, url: str | None, label: str | None):
    ck = _require_paper(key)
    if not path and not url:
        print("error: --path or --url required for add-si", file=sys.stderr)
        return 1
    # If path is given, copy the file into the SI subdir
    config = config_mod.load_config()
    lib = Path(config["_library_path"])
    paper_dir = lib / "papers" / ck
    si_dir = paper_dir / "si"
    si_dir.mkdir(parents=True, exist_ok=True)

    stored_path = ""
    if path:
        src = Path(path).expanduser()
        if not src.is_file():
            print(f"error: SI file not found: {path}", file=sys.stderr)
            return 1
        dest = si_dir / src.name
        shutil.copyfile(src, dest)
        stored_path = str(dest.relative_to(lib))

    with dbmod.Atomic():
        dbmod.execute(
            "INSERT INTO si_files (citekey, path, label, source_url) VALUES (?, ?, ?, ?)",
            (ck, stored_path, label, url),
        )
    print(f"added SI record to {ck}")


def cmd_add_github(key: str, owner: str, repo: str, url: str | None,
                   stars: int | None, latest_release: str | None,
                   readme_summary: str | None):
    ck = _require_paper(key)
    gh_url = url or f"https://github.com/{owner}/{repo}"
    with dbmod.Atomic():
        dbmod.execute(
            """
            INSERT INTO github_projects (citekey, owner, repo, url, stars, latest_release, readme_summary, last_checked_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))
            ON CONFLICT(citekey, owner, repo) DO UPDATE SET
              stars = coalesce(excluded.stars, github_projects.stars),
              latest_release = coalesce(excluded.latest_release, github_projects.latest_release),
              readme_summary = coalesce(excluded.readme_summary, github_projects.readme_summary),
              last_checked_at = excluded.last_checked_at
            """,
            (ck, owner, repo, gh_url, stars, latest_release, readme_summary),
        )
    print(f"added/updated GitHub {owner}/{repo} for {ck}")


def cmd_add_news(key: str, url: str, title: str | None, source_name: str | None,
                 kind: str | None, published_at: str | None):
    ck = _require_paper(key)
    with dbmod.Atomic():
        dbmod.execute(
            """
            INSERT OR IGNORE INTO news_links (citekey, url, title, source_name, published_at, kind)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (ck, url, title, source_name, published_at, kind or "news"),
        )
    print(f"added news link to {ck}")


def run(args) -> int:
    cfg = config_mod.load_config()
    lib = Path(cfg["_library_path"])
    if not (lib / "index.db").exists():
        print("error: no library yet — run `litlib init`", file=sys.stderr)
        return 1
    dbmod.connect(lib / "index.db")
    try:
        verb = args._assoc_verb  # injected by the dispatcher
        if verb == "tag":
            cmd_tag(args.key, args.tag, args.remove)
        elif verb == "collection":
            cmd_collection(args.slug, args.action, args.key)
        elif verb == "note":
            cmd_note(args.key, args.open_, args.append, args.set_from)
        elif verb == "add-si":
            return cmd_add_si(args.key, args.path, args.url, args.label)
        elif verb == "add-github":
            cmd_add_github(args.key, args.owner, args.repo, args.url,
                           args.stars, args.latest_release, args.readme_summary)
        elif verb == "add-news":
            cmd_add_news(args.key, args.url, args.title, args.source_name,
                         args.kind, args.published_at)
        else:
            print(f"error: unknown associate verb {verb!r}", file=sys.stderr)
            return 1
        return 0
    finally:
        dbmod.close()


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser("associate")
    ap.add_argument("_assoc_verb", help="Internal: injected by dispatcher")
    ap.add_argument("key")
    # minimal — real usage goes through litlib
    ap.add_argument("--url")
    ap.add_argument("--owner")
    ap.add_argument("--repo")
    ap.add_argument("--path")
    ap.add_argument("--label")
    ap.add_argument("--title")
    ap.add_argument("--source", dest="source_name")
    ap.add_argument("--kind")
    ap.add_argument("--published-at", dest="published_at")
    ap.add_argument("--stars", type=int)
    ap.add_argument("--release", dest="latest_release")
    ap.add_argument("--readme-summary", dest="readme_summary")
    ap.add_argument("--remove", action="store_true")
    ap.add_argument("--open", action="store_true", dest="open_")
    ap.add_argument("--append")
    ap.add_argument("--set-from", dest="set_from")
    run(ap.parse_args())