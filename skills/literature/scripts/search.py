#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///

"""`litlib search` — FTS5 + structured filters over the library."""

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import config as config_mod  # noqa: E402
import db as dbmod  # noqa: E402


def _sanitize_fts_query(query: str) -> str:
    """Escape a user query so FTS5 doesn't choke.

    Splits into alphanumeric tokens and AND-joins them (this matches
    what users expect from bare-word search — every word must match
    somewhere in title/abstract/authors/venue/tags).

    Multi-word phrases can be forced with double quotes; those are
    forwarded to FTS5 verbatim.
    """
    query = query.strip()
    if not query:
        return ""

    # Preserve "quoted phrases" as FTS5 phrase queries.
    import shlex
    try:
        parts = shlex.split(query, posix=True)
    except ValueError:
        parts = re.findall(r"[A-Za-z0-9]+", query)

    clauses = []
    for part in parts:
        # Keep alnum inside each part; drop tokens that become empty.
        tokens = re.findall(r"[A-Za-z0-9]+", part)
        if not tokens:
            continue
        if len(tokens) == 1:
            clauses.append(tokens[0])
        else:
            clauses.append('"' + " ".join(tokens) + '"')

    return " AND ".join(clauses)


def _year_filter(year_expr: str) -> tuple[str, list]:
    """Parse '2020' or '2020-2024' → SQL clause + params."""
    if "-" in year_expr:
        lo, hi = year_expr.split("-", 1)
        return "papers.year BETWEEN ? AND ?", [int(lo), int(hi)]
    return "papers.year = ?", [int(year_expr)]


def _first_author(citekey: str) -> str:
    row = dbmod.fetchone(
        """
        SELECT a.full_name FROM paper_authors pa
        JOIN authors a ON a.id = pa.author_id
        WHERE pa.citekey = ? ORDER BY position LIMIT 1
        """,
        (citekey,),
    )
    return row["full_name"] if row else ""


def run(args) -> int:
    cfg = config_mod.load_config()
    lib = Path(cfg["_library_path"])
    if not (lib / "index.db").exists():
        print("error: no library yet — run `litlib init`", file=sys.stderr)
        return 1
    dbmod.connect(lib / "index.db")
    try:
        return _run(args)
    finally:
        dbmod.close()


def _run(args) -> int:
    where = []
    params: list = []

    if args.query:
        fts_q = _sanitize_fts_query(args.query)
        if fts_q:
            where.append("papers.citekey IN (SELECT citekey FROM papers_fts WHERE papers_fts MATCH ?)")
            params.append(fts_q)

    if args.year:
        clause, p = _year_filter(args.year)
        where.append(clause)
        params.extend(p)

    if args.author:
        where.append(
            "papers.citekey IN (SELECT pa.citekey FROM paper_authors pa "
            "JOIN authors a ON a.id = pa.author_id WHERE a.full_name LIKE ?)"
        )
        params.append(f"%{args.author}%")

    for t in args.tag:
        where.append(
            "papers.citekey IN (SELECT pt.citekey FROM paper_tags pt "
            "JOIN tags tg ON tg.id = pt.tag_id WHERE tg.name = ? AND tg.kind = 'tag')"
        )
        params.append(t)

    for c in args.collection:
        where.append(
            "papers.citekey IN (SELECT pt.citekey FROM paper_tags pt "
            "JOIN tags tg ON tg.id = pt.tag_id WHERE tg.name = ? AND tg.kind = 'collection')"
        )
        params.append(c)

    if args.has_pdf:
        where.append("papers.pdf_path IS NOT NULL")

    sql = "SELECT citekey, title, year, venue, arxiv_id, doi FROM papers"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY year DESC, citekey ASC LIMIT ?"
    params.append(args.limit)

    rows = dbmod.fetchall(sql, tuple(params))

    if args.json:
        out = []
        for r in rows:
            d = dict(r)
            d["first_author"] = _first_author(r["citekey"])
            out.append(d)
        print(json.dumps(out, indent=2))
        return 0

    if not rows:
        print("(no matches)")
        return 0

    # Compact table
    key_w = max(len(r["citekey"]) for r in rows)
    for r in rows:
        fa = _first_author(r["citekey"])
        fa_last = fa.split()[-1] if fa else ""
        year = str(r["year"] or "----")
        title = (r["title"] or "").strip()
        if len(title) > 80:
            title = title[:77] + "..."
        print(f"{r['citekey']:<{key_w}}  {year}  {fa_last:<16}  {title}")

    return 0


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser("search")
    ap.add_argument("query", nargs="?", default="")
    ap.add_argument("--tag", action="append", default=[])
    ap.add_argument("--collection", action="append", default=[])
    ap.add_argument("--year")
    ap.add_argument("--author")
    ap.add_argument("--has-pdf", action="store_true")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--limit", type=int, default=50)
    sys.exit(run(ap.parse_args()))