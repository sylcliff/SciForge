"""sf-search entry point: argparse + top-level dispatch."""

from __future__ import annotations

import argparse
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

import arxiv_upgrade
import dedup
import merge
import output
import rank
from config import HTTPError, VERSION, load_config
from doctor import cmd_doctor
from mesh import cmd_build, cmd_check, cmd_lookup
from citations import cmd_refs, cmd_cited_by
from query import QueryError, build_from_args, build_from_batch_line
from query_obj import QueryObject
from sources import ALL_SOURCES, DEFAULT_ORDER


# --------------------------------------------------------------------------- #
# argparse
# --------------------------------------------------------------------------- #


def _build_search_parser() -> argparse.ArgumentParser:
    """Parser used when no subcommand is present (default search mode)."""
    p = argparse.ArgumentParser(
        prog="sf-search",
        description="Multi-source academic literature discovery (PubMed / Crossref / arXiv / OpenAlex / Semantic Scholar).",
    )
    p.add_argument("--version", action="version", version=f"sf-search {VERSION}")

    p.add_argument("positional", nargs="?", help="Keyword query (positional)")
    p.add_argument("--query", help="Raw query string, passed unchanged to each source (PubMed syntax works fully; others treat as free text)")
    p.add_argument("--title", help="Field mode: title phrase")
    p.add_argument("--author", action="append", help="Field mode: author (may repeat; OR'd)")
    p.add_argument("--journal", help="Field mode: journal / venue name")
    p.add_argument("--year", help="Year filter: '2020' or '2020..2024' or '2020..' or '..2024'")
    p.add_argument("--from-strategy", help="Execute a strategy.json produced by 'mesh build'")
    p.add_argument("--from-file", help="Batch mode: run one query per line from PATH")

    p.add_argument("--sources", help="Comma-separated subset of pubmed,crossref,arxiv,openalex,s2 (default: all)")
    p.add_argument("--top", type=int, default=30, help="Number of results to return (default 30)")
    p.add_argument("--per-source-limit", type=int, default=None,
                   help="Per-source retrieval limit before dedup (default: top*2, capped at 100)")
    p.add_argument("--sort", default="relevance",
                   choices=("relevance", "year:desc", "citations:desc"),
                   help="Ranking (default relevance = RRF)")
    p.add_argument("--format", default="ndjson",
                   choices=("ndjson", "ids", "table", "bib", "ris"),
                   help="Output format (default ndjson)")
    p.add_argument("--out", default="-", help="Output path (default stdout)")
    p.add_argument("--no-arxiv-upgrade", action="store_true",
                   help="Disable the post-dedup arXiv→journal DOI lookup "
                        "(faster but same paper may appear twice when its "
                        "arxiv preprint and journal version both hit different sources)")
    p.add_argument("--arxiv-upgrade-fallback", default="none",
                   choices=("none", "title-search"),
                   help="Third-tier fallback when OpenAlex + S2 arxiv-id lookups "
                        "both fail. `title-search` queries Crossref by the arxiv "
                        "paper's title and accepts a match iff title Jaccard ≥ 0.85 "
                        "and first-author surname agrees. Off by default because "
                        "it costs one extra HTTP per still-unresolved arxiv-only record.")
    return p


def _build_subcommand_parser() -> argparse.ArgumentParser:
    """Parser used when the first non-flag token is 'doctor' or 'mesh'."""
    p = argparse.ArgumentParser(prog="sf-search")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("doctor", help="Environment + per-source reachability check")

    mesh_p = sub.add_parser("mesh", help="MeSH search strategy workflow")
    mesh_sub = mesh_p.add_subparsers(dest="mesh_cmd", required=True)

    lookup_p = mesh_sub.add_parser("lookup", help="Discover MeSH terms per concept")
    lookup_p.add_argument("--concept", action="append", required=True,
                          help="One concept per --concept flag; may repeat")
    lookup_p.add_argument("--top", type=int, default=5, help="Candidates per concept")

    build_p = mesh_sub.add_parser("build", help="Compile strategy.json (no network)")

    class TermAction(argparse.Action):
        def __call__(self, parser, ns, values, option_string=None):
            kind = "mesh" if "mesh" in (option_string or "") else "synonym"
            getattr(ns, "terms").append((kind, values))

    build_p.set_defaults(terms=[])
    build_p.add_argument("--mesh", action=TermAction, help="MeSH descriptor; may repeat")
    build_p.add_argument("--synonym", action=TermAction, help="Synonym for the last --mesh; may repeat")
    build_p.add_argument("--op", default="AND", help="Top-level operator: AND | OR (default AND)")
    build_p.add_argument("-o", "--out", default="-", help="Write strategy.json to PATH (default: stdout)")

    check_p = mesh_sub.add_parser("check", help="espell + esearch count on a strategy")
    check_p.add_argument("strategy_path", help="Path to strategy.json")

    # --- citation-graph subcommands: refs / cited-by ---
    for verb, help_text, is_cited in [
        ("refs", "Outgoing references cited by a paper", False),
        ("cited-by", "Papers that cite the seed paper", True),
    ]:
        cp = sub.add_parser(verb, help=help_text)
        cp.add_argument("seed",
            help="Seed paper: DOI, PMID, arxiv id, OpenAlex W-id, "
                 "S2 paper id, or sciforge://literature/<citekey>")
        cp.add_argument("--top", type=int, default=None,
            help=("Truncate to top N results (refs default: all; "
                  "cited-by default: 100)"))
        if is_cited:
            cp.add_argument("--all", action="store_true",
                help="cited-by only: fetch every citer (may be tens of thousands)")
            cp.add_argument("--sort", default="citations:desc",
                choices=("citations:desc", "year:desc"),
                help="cited-by ranking (default citations:desc)")
        else:
            cp.set_defaults(all=False, sort="citations:desc")
        cp.add_argument("--sources",
            help="Comma-separated subset of openalex,s2,crossref,pubmed "
                 "(default: all applicable)")
        cp.add_argument("--no-fetch-meta", action="store_true",
            help="Skip batch meta-fill for id-only records (faster; "
                 "records may be missing title/authors)")
        cp.add_argument("--format", default="ndjson",
            choices=("ndjson", "ids", "table", "bib", "ris"),
            help="Output format (default ndjson)")
        cp.add_argument("--out", default="-", help="Main output path (default stdout)")
        cp.add_argument("--out-unresolved", default=None,
            help="Write unresolved-reference records (raw citation strings "
                 "with no DOI) as NDJSON to PATH. Never mixes into main output.")

    return p


def _first_non_flag(argv: list[str]) -> str | None:
    for tok in argv:
        if not tok.startswith("-"):
            return tok
    return None


# --------------------------------------------------------------------------- #
# Search orchestration
# --------------------------------------------------------------------------- #


def _select_sources(sources_arg: str | None) -> list[str]:
    if not sources_arg:
        return list(DEFAULT_ORDER)
    names = [s.strip().lower() for s in sources_arg.split(",") if s.strip()]
    unknown = [n for n in names if n not in ALL_SOURCES]
    if unknown:
        raise ValueError(f"unknown source(s): {', '.join(unknown)}. "
                         f"Valid: {', '.join(DEFAULT_ORDER)}")
    return names


def _run_one_query(
    q: QueryObject,
    *,
    sources: list[str],
    per_source_limit: int,
    cfg: Any,
    respect_rate_limit: bool,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Fan out to all sources concurrently; return (raw_records, failed_sources)."""
    raw: list[dict[str, Any]] = []
    failed: list[str] = []

    def _call(src_name: str) -> tuple[str, list[dict[str, Any]] | str]:
        adapter = ALL_SOURCES[src_name]
        try:
            recs = adapter.search(
                q,
                limit=per_source_limit,
                cfg=cfg,
                respect_rate_limit=respect_rate_limit,
            )
            return src_name, recs
        except HTTPError as e:
            return src_name, str(e)
        except Exception as e:  # noqa: BLE001
            return src_name, f"{type(e).__name__}: {e}"

    with ThreadPoolExecutor(max_workers=len(sources)) as ex:
        futs = [ex.submit(_call, s) for s in sources]
        for fut in as_completed(futs):
            src_name, result = fut.result()
            if isinstance(result, str):
                failed.append(f"{src_name}: {result}")
            else:
                raw.extend(result)

    return raw, failed


def cmd_search(args: argparse.Namespace, cfg: Any) -> int:
    try:
        sources = _select_sources(args.sources)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    top = max(1, int(args.top))
    per_source_limit = args.per_source_limit if args.per_source_limit else top * 2
    per_source_limit = min(per_source_limit, 100)

    # ---- Batch mode ----
    if args.from_file:
        return _run_batch(args, cfg, sources, top, per_source_limit)

    # ---- Single query mode ----
    try:
        q = build_from_args(args)
    except QueryError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    raw, failed = _run_one_query(
        q, sources=sources, per_source_limit=per_source_limit,
        cfg=cfg, respect_rate_limit=False,
    )

    if not raw:
        if len(failed) == len(sources):
            print(f"error: all sources failed: {'; '.join(failed)}", file=sys.stderr)
            return 5
        print("no results", file=sys.stderr)
        if failed:
            print(f"warning: sources failed: {'; '.join(failed)}", file=sys.stderr)
        return 3

    groups = dedup.group_by_identifier(raw)
    merged = merge.merge_all(groups)

    # arxiv-upgrade phase: for arxiv-only groups, look up the published-
    # version DOI concurrently on OpenAlex + S2, inject the DOI, and
    # re-run dedup so preprint and journal records merge.
    upgraded_arxiv_ids: set[str] = set()
    title_search_arxiv_ids: set[str] = set()
    if not args.no_arxiv_upgrade:
        raw, upgraded_arxiv_ids, title_search_arxiv_ids = arxiv_upgrade.upgrade(
            raw, cfg=cfg, respect_rate_limit=False,
            title_search_fallback=(args.arxiv_upgrade_fallback == "title-search"),
        )
        if upgraded_arxiv_ids:
            groups = dedup.group_by_identifier(raw)
            merged = merge.merge_all(groups)
    arxiv_upgrade.annotate_merged(merged, upgraded_arxiv_ids, title_search_arxiv_ids)

    ranked = rank.rank(merged, args.sort)[:top]

    if failed:
        print(f"warning: sources failed: {'; '.join(failed)}", file=sys.stderr)

    output.emit(ranked, fmt=args.format, out=args.out, summary=None)
    return 0


def _run_batch(
    args: argparse.Namespace, cfg: Any,
    sources: list[str], top: int, per_source_limit: int,
) -> int:
    lines: list[str] = []
    with open(args.from_file, "r", encoding="utf-8") as f:
        for line in f:
            s = line.strip()
            if s and not s.startswith("#"):
                lines.append(s)

    if not lines:
        print(f"error: no queries in {args.from_file}", file=sys.stderr)
        return 2

    all_ranked: list[dict[str, Any]] = []
    all_failed_sources: set[str] = set()
    t0 = time.monotonic()
    queries_failed = 0

    import json  # local import to avoid top-of-file shuffle
    fp = sys.stdout if args.out in (None, "-") else open(args.out, "w", encoding="utf-8", newline="\n")

    try:
        for i, line in enumerate(lines):
            try:
                q = build_from_batch_line(line)
            except QueryError as e:
                fp.write(json.dumps({"query_index": i, "query": line,
                                     "status": "error", "error": str(e)}) + "\n")
                queries_failed += 1
                continue

            raw, failed = _run_one_query(
                q, sources=sources, per_source_limit=per_source_limit,
                cfg=cfg, respect_rate_limit=True,   # batch mode: rate-limit
            )
            for f in failed:
                all_failed_sources.add(f.split(":", 1)[0])

            if not raw:
                fp.write(json.dumps({"query_index": i, "query": line,
                                     "status": "no_results"}) + "\n")
                continue

            groups = dedup.group_by_identifier(raw)
            merged = merge.merge_all(groups)

            # arxiv-upgrade phase (batch): same logic as single-query.
            upgraded_arxiv_ids: set[str] = set()
            title_search_arxiv_ids: set[str] = set()
            if not args.no_arxiv_upgrade:
                raw, upgraded_arxiv_ids, title_search_arxiv_ids = arxiv_upgrade.upgrade(
                    raw, cfg=cfg, respect_rate_limit=True,
                    title_search_fallback=(args.arxiv_upgrade_fallback == "title-search"),
                )
                if upgraded_arxiv_ids:
                    groups = dedup.group_by_identifier(raw)
                    merged = merge.merge_all(groups)
            arxiv_upgrade.annotate_merged(merged, upgraded_arxiv_ids, title_search_arxiv_ids)

            ranked = rank.rank(merged, args.sort)[:top]

            for rec in ranked:
                rec["query_index"] = i
                fp.write(json.dumps(rec, ensure_ascii=False) + "\n")
                all_ranked.append(rec)

        summary = {
            "queries_ran": len(lines),
            "queries_failed": queries_failed,
            "papers_returned": len(all_ranked),
            "sources_queried": sources,
            "sources_failed": sorted(all_failed_sources),
            "duration_sec": round(time.monotonic() - t0, 2),
        }
        fp.write(json.dumps({"summary": summary}, ensure_ascii=False) + "\n")
    finally:
        if fp is not sys.stdout:
            fp.close()

    return 0 if all_ranked else 3


# --------------------------------------------------------------------------- #
# Entry
# --------------------------------------------------------------------------- #


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]

    cfg = load_config()

    first = _first_non_flag(argv)
    if first in ("doctor", "mesh", "refs", "cited-by"):
        parser = _build_subcommand_parser()
        args = parser.parse_args(argv)
        if args.cmd == "doctor":
            return cmd_doctor(cfg)
        if args.cmd == "mesh":
            if args.mesh_cmd == "lookup":
                return cmd_lookup(args, cfg)
            if args.mesh_cmd == "build":
                return cmd_build(args, cfg)
            if args.mesh_cmd == "check":
                return cmd_check(args, cfg)
            parser.error(f"unhandled mesh subcommand: {args.mesh_cmd}")
            return 2
        if args.cmd == "refs":
            return cmd_refs(args, cfg)
        if args.cmd == "cited-by":
            return cmd_cited_by(args, cfg)
        parser.error(f"unhandled subcommand: {args.cmd}")
        return 2

    # Default: search mode
    parser = _build_search_parser()
    args = parser.parse_args(argv)
    return cmd_search(args, cfg)


if __name__ == "__main__":
    sys.exit(main())
