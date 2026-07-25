#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///

"""`sf-lit search` — paper-level FTS over Markdown + structured WHERE.

Scope (Q5, Q12, Q15/2C):
  - Content search runs against ``papers_md_fts.markdown`` at
    **paper granularity** — one row = one paper.
  - Ranking is BM25 only (Q12/A). Score is reversed so "bigger is better"
    matches every other search UX in the world.
  - Structured filters (``--year``, ``--tag``, ``--author``, ``--has-md``,
    ``--has-pdf``, ``--collection``) are pure WHERE clauses; they do not
    contribute to the rank.
  - No query → no FTS; results come from the catalog ordered by
    ``year DESC, citekey ASC``.
  - Papers with ``md_status='absent'`` are unreachable via text query
    (their ``paper.md`` doesn't exist yet). ``--has-md`` filters to
    ``ready`` papers; the default includes ``ready`` + ``stale`` (stale
    hits are annotated in the output so the caller knows to reconvert).
"""

import json
import re
import shlex
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import config as config_mod  # noqa: E402
import db as dbmod  # noqa: E402


# ---- FTS query sanitizer ----------------------------------------------


def _sanitize_fts_query(query: str) -> str:
    """Escape a raw user query into a safe FTS5 MATCH expression.

    - Bare words → tokens (AND-joined).
    - "double quoted phrases" → FTS5 phrase queries preserved.
    - Punctuation is stripped inside each unquoted chunk so ``foo,bar``
      still becomes ``foo AND bar`` rather than a syntax error.
    """
    query = (query or "").strip()
    if not query:
        return ""
    try:
        parts = shlex.split(query, posix=True)
    except ValueError:
        parts = re.findall(r"[A-Za-z0-9]+", query)
    clauses = []
    for part in parts:
        tokens = re.findall(r"[A-Za-z0-9]+", part)
        if not tokens:
            continue
        if len(tokens) == 1:
            clauses.append(tokens[0])
        else:
            clauses.append('"' + " ".join(tokens) + '"')
    return " AND ".join(clauses)


def _year_filter(year_expr: str) -> tuple[str, list]:
    """Parse '2020', '2020-2024', '2020-' (open upper), '-2024' (open lower)."""
    if "-" in year_expr:
        lo, hi = year_expr.split("-", 1)
        lo = lo.strip()
        hi = hi.strip()
        if lo and hi:
            return "papers.year BETWEEN ? AND ?", [int(lo), int(hi)]
        if lo:
            return "papers.year >= ?", [int(lo)]
        if hi:
            return "papers.year <= ?", [int(hi)]
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


# ---- runner ------------------------------------------------------------


def run(args) -> int:
    cfg = config_mod.load_config()
    lib = Path(cfg["_library_path"])
    if not (lib / "index.db").exists():
        print("error: no library yet — run `sf-lit init`", file=sys.stderr)
        return 3
    dbmod.connect(lib / "index.db")
    try:
        return _run(args)
    finally:
        dbmod.close()


def _run(args) -> int:
    fts_q = _sanitize_fts_query(args.query) if args.query else ""

    where: list[str] = []
    params: list = []

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
    if args.has_md:
        where.append("papers.md_status = 'ready'")

    if fts_q:
        # FTS path — join papers_md_fts to papers for structured filters.
        # bm25() returns lower==better; we reverse it in output.
        sql = """
            SELECT
              papers.citekey, papers.title, papers.year, papers.venue,
              papers.arxiv_id, papers.doi, papers.pdf_path, papers.md_status,
              bm25(papers_md_fts) AS bm25_raw,
              snippet(papers_md_fts, 0, '<mark>', '</mark>', '…', 24) AS snip
            FROM papers_md_fts
            JOIN papers_md ON papers_md.rowid = papers_md_fts.rowid
            JOIN papers    ON papers.citekey = papers_md.citekey
            WHERE papers_md_fts MATCH ?
        """
        params_full: list = [fts_q]
        if where:
            sql += " AND " + " AND ".join(where)
            params_full.extend(params)
        sql += " ORDER BY bm25_raw ASC LIMIT ?"
        params_full.append(args.limit)
        rows = dbmod.fetchall(sql, tuple(params_full))
    else:
        # No query → catalog listing.
        sql = ("SELECT citekey, title, year, venue, arxiv_id, doi, pdf_path, "
               "md_status FROM papers")
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY year DESC, citekey ASC LIMIT ?"
        params.append(args.limit)
        rows = dbmod.fetchall(sql, tuple(params))

    if args.json:
        out = []
        for r in rows:
            d = {
                "citekey": r["citekey"],
                "title": r["title"],
                "year": r["year"],
                "venue": r["venue"],
                "arxiv_id": r["arxiv_id"],
                "doi": r["doi"],
                "first_author": _first_author(r["citekey"]),
                "has_pdf": bool(r["pdf_path"]),
                "has_md": r["md_status"] == "ready",
                "md_status": r["md_status"],
            }
            if fts_q:
                # Reverse BM25 so bigger = more relevant. FTS5 bm25()
                # returns negative-ish values with the default weights;
                # negating puts the "best" hits highest.
                d["score"] = -float(r["bm25_raw"])
                d["snippet"] = r["snip"]
            out.append(d)
        print(json.dumps(out, indent=2, ensure_ascii=False))
        return 0

    if not rows:
        print("(no matches)")
        return 0

    key_w = max(len(r["citekey"]) for r in rows)
    for r in rows:
        fa = _first_author(r["citekey"])
        fa_last = fa.split()[-1] if fa else ""
        year = str(r["year"] or "----")
        title = (r["title"] or "").strip()
        if len(title) > 72:
            title = title[:69] + "..."
        badge = ""
        if r["md_status"] == "stale":
            badge = " [stale]"
        elif r["md_status"] == "absent" and not fts_q:
            badge = " [no-md]"
        line = f"{r['citekey']:<{key_w}}  {year}  {fa_last:<16}  {title}{badge}"
        print(line)
        if fts_q:
            snip = (r["snip"] or "").strip()
            if snip:
                # De-mark for terminal legibility; JSON callers get raw <mark>.
                snip = snip.replace("<mark>", "").replace("</mark>", "")
                snip = re.sub(r"\s+", " ", snip)
                if len(snip) > 180:
                    snip = snip[:177] + "..."
                print(f"{' ' * (key_w + 2)}  {snip}")

    return 0


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser("search")
    ap.add_argument("query", nargs="?", default="")
    ap.add_argument("--tag", action="append", default=[])
    ap.add_argument("--collection", action="append", default=[])
    ap.add_argument("--year")
    ap.add_argument("--author")
    ap.add_argument("--has-pdf", dest="has_pdf", action="store_true")
    ap.add_argument("--has-md", dest="has_md", action="store_true")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--limit", type=int, default=20)
    sys.exit(run(ap.parse_args()))
