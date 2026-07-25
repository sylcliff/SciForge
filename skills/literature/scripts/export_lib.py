#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///

"""`litlib export <selector> --format bibtex|json`

Selector forms:
  litlib export <citekey>             # single paper
  litlib export --tag T [--tag T2]    # union of tag(s)
  litlib export --collection C        # collection
  litlib export --all
  litlib export --query "search terms" [--tag T ...]

Formats: bibtex (default), json.
"""

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import config as config_mod  # noqa: E402
import db as dbmod  # noqa: E402

CONF_KEYWORDS = ("NeurIPS", "ICLR", "ICML", "CVPR", "ECCV", "ICCV",
                 "ACL", "EMNLP", "NAACL", "AAAI", "IJCAI", "SIGGRAPH",
                 "KDD", "WWW", "PLDI", "POPL", "OSDI", "SOSP")


def _bibtex_kind(paper: dict) -> str:
    """Best-effort entry-type heuristic."""
    venue = (paper.get("venue") or "").strip()
    venue_full = (paper.get("venue_full") or "").strip()
    text = f"{venue} {venue_full}"
    if paper.get("arxiv_id") and not venue:
        return "misc"
    if "arxiv" in text.lower():
        return "misc"
    for kw in CONF_KEYWORDS:
        if kw.lower() in text.lower():
            return "inproceedings"
    return "article"


def _bibtex_escape(text: str) -> str:
    """Escape braces/backslashes for a BibTeX field body."""
    if text is None:
        return ""
    text = text.replace("\\", "\\\\")
    text = text.replace("{", r"\{").replace("}", r"\}")
    return text


def _authors_bibtex(citekey: str) -> str:
    rows = dbmod.fetchall(
        "SELECT a.full_name FROM paper_authors pa JOIN authors a ON a.id = pa.author_id "
        "WHERE pa.citekey = ? ORDER BY position",
        (citekey,),
    )
    names = []
    for r in rows:
        n = r["full_name"].strip()
        if "," in n:
            names.append(n)
        else:
            parts = n.split()
            if len(parts) >= 2:
                names.append(f"{parts[-1]}, {' '.join(parts[:-1])}")
            else:
                names.append(n)
    return " and ".join(names)


def _paper_to_bibtex(paper: dict, lib: Path, include_file: bool) -> str:
    citekey = paper["citekey"]
    kind = _bibtex_kind(paper)

    fields: list[tuple[str, str]] = []
    if paper.get("title"):
        fields.append(("title", "{" + _bibtex_escape(paper["title"]) + "}"))
    authors = _authors_bibtex(citekey)
    if authors:
        fields.append(("author", "{" + _bibtex_escape(authors) + "}"))
    if paper.get("year"):
        fields.append(("year", str(paper["year"])))
    venue = paper.get("venue_full") or paper.get("venue")
    if venue:
        vf = "journal" if kind == "article" else "booktitle"
        fields.append((vf, "{" + _bibtex_escape(venue) + "}"))
    if paper.get("doi"):
        fields.append(("doi", "{" + paper["doi"] + "}"))
    if paper.get("arxiv_id"):
        fields.append(("eprint", "{" + paper["arxiv_id"] + "}"))
        fields.append(("archivePrefix", "{arXiv}"))
    if paper.get("url"):
        fields.append(("url", "{" + paper["url"] + "}"))
    if include_file and paper.get("pdf_path"):
        abs_pdf = lib / paper["pdf_path"]
        fields.append(("file", "{:" + str(abs_pdf) + ":pdf}"))

    body = ",\n  ".join(f"{k} = {v}" for k, v in fields)
    return f"@{kind}{{{citekey},\n  {body}\n}}\n"


def _paper_to_json(paper: dict, lib: Path) -> dict:
    ck = paper["citekey"]
    out = dict(paper)
    out["authors"] = [
        r["full_name"] for r in dbmod.fetchall(
            "SELECT a.full_name FROM paper_authors pa JOIN authors a ON a.id = pa.author_id "
            "WHERE pa.citekey = ? ORDER BY position", (ck,))
    ]
    out["tags"] = [r["name"] for r in dbmod.fetchall(
        "SELECT t.name FROM paper_tags pt JOIN tags t ON t.id = pt.tag_id "
        "WHERE pt.citekey = ? AND t.kind = 'tag'", (ck,))]
    out["collections"] = [r["name"] for r in dbmod.fetchall(
        "SELECT t.name FROM paper_tags pt JOIN tags t ON t.id = pt.tag_id "
        "WHERE pt.citekey = ? AND t.kind = 'collection'", (ck,))]
    if out.get("pdf_path"):
        out["pdf_abs_path"] = str(lib / out["pdf_path"])
    return out


def _select_papers(args) -> list[dict]:
    """Resolve the selector into a list of paper rows."""
    where: list[str] = []
    params: list = []

    if args.selector:
        where.append("citekey = ?")
        params.append(args.selector)

    if args.all_:
        pass  # no additional where

    for t in args.tag:
        where.append(
            "citekey IN (SELECT pt.citekey FROM paper_tags pt "
            "JOIN tags tg ON tg.id = pt.tag_id "
            "WHERE tg.name = ? AND tg.kind = 'tag')"
        )
        params.append(t)

    for c in args.collection:
        where.append(
            "citekey IN (SELECT pt.citekey FROM paper_tags pt "
            "JOIN tags tg ON tg.id = pt.tag_id "
            "WHERE tg.name = ? AND tg.kind = 'collection')"
        )
        params.append(c)

    if args.query:
        # Route --query through the same paper-level FTS as `search`.
        # Any word tokenizable to alnum joins with AND; phrases stay
        # implicit (`export` is not the right place for advanced query
        # syntax — `search` is).
        tokens = re.findall(r"[A-Za-z0-9]+", args.query)
        if tokens:
            fts = " AND ".join(tokens)
            where.append(
                "citekey IN (SELECT papers_md.citekey FROM papers_md_fts "
                "JOIN papers_md ON papers_md.rowid = papers_md_fts.rowid "
                "WHERE papers_md_fts MATCH ?)"
            )
            params.append(fts)

    if not where and not args.all_:
        print("error: export needs a selector — citekey, --tag, --collection, --all, or --query",
              file=sys.stderr)
        sys.exit(1)

    sql = "SELECT * FROM papers"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY year DESC, citekey ASC"

    return [dict(r) for r in dbmod.fetchall(sql, tuple(params))]


def run(args) -> int:
    cfg = config_mod.load_config()
    lib = Path(cfg["_library_path"])
    if not (lib / "index.db").exists():
        print("error: no library yet — run `litlib init`", file=sys.stderr)
        return 1
    dbmod.connect(lib / "index.db")
    try:
        papers = _select_papers(args)
        if not papers:
            print("(no papers match selector)", file=sys.stderr)
            return 1

        if args.format == "bibtex":
            out_lines = [_paper_to_bibtex(p, lib, args.include_file) for p in papers]
            content = "\n".join(out_lines)
        else:  # json
            content = json.dumps(
                [_paper_to_json(p, lib) for p in papers],
                indent=2, ensure_ascii=False, default=str,
            )

        if args.out:
            Path(args.out).expanduser().write_text(content)
            print(f"wrote {len(papers)} entries to {args.out}")
        else:
            sys.stdout.write(content)
            if not content.endswith("\n"):
                sys.stdout.write("\n")
        return 0
    finally:
        dbmod.close()


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser("export")
    ap.add_argument("selector", nargs="?")
    ap.add_argument("--format", default="bibtex", choices=["bibtex", "json"])
    ap.add_argument("--tag", action="append", default=[])
    ap.add_argument("--collection", action="append", default=[])
    ap.add_argument("--all", action="store_true", dest="all_")
    ap.add_argument("--query")
    ap.add_argument("--out")
    ap.add_argument("--include-file", action="store_true", dest="include_file")
    sys.exit(run(ap.parse_args()))