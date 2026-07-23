#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///

"""`litlib show` — render one paper as a compact markdown card or JSON."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import config as config_mod  # noqa: E402
import db as dbmod  # noqa: E402


def _resolve_key(key: str) -> dict | None:
    """Look up a paper by citekey, arxiv_id, or doi."""
    for field in ("citekey", "arxiv_id", "doi"):
        row = dbmod.fetchone(f"SELECT * FROM papers WHERE {field} = ?", (key,))
        if row:
            return dict(row)
    return None


def _authors(citekey: str) -> list[str]:
    rows = dbmod.fetchall(
        """
        SELECT a.full_name FROM paper_authors pa
        JOIN authors a ON a.id = pa.author_id
        WHERE pa.citekey = ? ORDER BY position
        """,
        (citekey,),
    )
    return [r["full_name"] for r in rows]


def _tags(citekey: str, kind: str) -> list[str]:
    rows = dbmod.fetchall(
        """
        SELECT tg.name FROM paper_tags pt
        JOIN tags tg ON tg.id = pt.tag_id
        WHERE pt.citekey = ? AND tg.kind = ?
        ORDER BY tg.name
        """,
        (citekey, kind),
    )
    return [r["name"] for r in rows]


def _github_projects(citekey: str) -> list[dict]:
    rows = dbmod.fetchall(
        "SELECT owner, repo, url, stars, latest_release, last_checked_at "
        "FROM github_projects WHERE citekey = ? ORDER BY owner, repo",
        (citekey,),
    )
    return [dict(r) for r in rows]


def _news_links(citekey: str) -> list[dict]:
    rows = dbmod.fetchall(
        "SELECT url, title, source_name, published_at, kind "
        "FROM news_links WHERE citekey = ? ORDER BY discovered_at DESC",
        (citekey,),
    )
    return [dict(r) for r in rows]


def _si_files(citekey: str) -> list[dict]:
    rows = dbmod.fetchall(
        "SELECT path, label, source_url FROM si_files WHERE citekey = ? ORDER BY id",
        (citekey,),
    )
    return [dict(r) for r in rows]


def _refs(citekey: str) -> list[dict]:
    rows = dbmod.fetchall(
        "SELECT cited_doi, cited_arxiv_id, cited_title, cited_citekey "
        "FROM citations WHERE citing_citekey = ? ORDER BY id",
        (citekey,),
    )
    return [dict(r) for r in rows]


def run(args) -> int:
    cfg = config_mod.load_config()
    lib = Path(cfg["_library_path"])
    if not (lib / "index.db").exists():
        print("error: no library yet — run `litlib init`", file=sys.stderr)
        return 1
    dbmod.connect(lib / "index.db")
    try:
        return _run(args, lib)
    finally:
        dbmod.close()


def _run(args, lib: Path) -> int:
    paper = _resolve_key(args.key)
    if paper is None:
        print(f"error: no paper matches {args.key!r}", file=sys.stderr)
        return 1

    citekey = paper["citekey"]
    authors = _authors(citekey)
    tags = _tags(citekey, "tag")
    collections = _tags(citekey, "collection")
    github = _github_projects(citekey) if not args.json else _github_projects(citekey)
    news = _news_links(citekey)
    si = _si_files(citekey)
    refs = _refs(citekey) if args.refs else []

    if args.json:
        out = dict(paper)
        out["authors"] = authors
        out["tags"] = tags
        out["collections"] = collections
        out["github"] = github
        out["news"] = news
        out["si"] = si
        if args.refs:
            out["citations"] = refs
        # Absolute paths for convenience
        if paper.get("pdf_path"):
            out["pdf_abs_path"] = str(lib / paper["pdf_path"])
        if paper.get("notes_path"):
            out["notes_abs_path"] = str(lib / paper["notes_path"])
        print(json.dumps(out, indent=2, default=str))
        return 0

    # Markdown card
    print(f"# {paper['title']}")
    print()
    print(f"**citekey:** `{citekey}`")
    if authors:
        print(f"**authors:** {', '.join(authors)}")
    if paper.get("year"):
        print(f"**year:** {paper['year']}")
    if paper.get("venue"):
        v = paper["venue"]
        if paper.get("venue_full") and paper["venue_full"] != v:
            v = f"{v} ({paper['venue_full']})"
        print(f"**venue:** {v}")
    if paper.get("doi"):
        print(f"**doi:** [{paper['doi']}](https://doi.org/{paper['doi']})")
    if paper.get("arxiv_id"):
        print(f"**arxiv:** [arXiv:{paper['arxiv_id']}](https://arxiv.org/abs/{paper['arxiv_id']})")
    if tags:
        print(f"**tags:** {', '.join('#' + t for t in tags)}")
    if collections:
        print(f"**collections:** {', '.join(collections)}")

    if paper.get("pdf_path"):
        print(f"**pdf:** `{lib / paper['pdf_path']}`")
    if paper.get("notes_path"):
        print(f"**notes:** `{lib / paper['notes_path']}`")

    if paper.get("abstract"):
        print()
        print("## Abstract")
        print()
        print(paper["abstract"])

    if github:
        print()
        print("## GitHub")
        for gh in github:
            stars = f" ★{gh['stars']}" if gh.get("stars") is not None else ""
            print(f"- [{gh['owner']}/{gh['repo']}]({gh['url']}){stars}")

    if si:
        print()
        print("## Supporting Information")
        for s in si:
            label = f" ({s['label']})" if s.get("label") else ""
            print(f"- `{lib / s['path']}`{label}")

    if news:
        print()
        print("## News / discussion")
        for n in news:
            src = f" — *{n['source_name']}*" if n.get("source_name") else ""
            print(f"- [{n['title'] or n['url']}]({n['url']}){src}")

    if args.refs and refs:
        print()
        print("## References")
        for r in refs[:20]:
            marker = " ✓" if r.get("cited_citekey") else ""
            title = r.get("cited_title") or r.get("cited_doi") or r.get("cited_arxiv_id") or "?"
            print(f"- {title}{marker}")
        if len(refs) > 20:
            print(f"- … and {len(refs) - 20} more")

    return 0


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser("show")
    ap.add_argument("key")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--refs", action="store_true")
    ap.add_argument("--news", action="store_true")
    ap.add_argument("--github", action="store_true")
    sys.exit(run(ap.parse_args()))