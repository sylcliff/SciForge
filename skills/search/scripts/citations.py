"""Orchestrator for `sf-search refs` and `sf-search cited-by`.

Given a `SeedId` (from `seed_resolver.classify`), fans out to the
citation endpoints of OpenAlex / S2 / Crossref (refs also) and PubMed
(only when the seed maps to a PMID). Each source returns raw
source-format records. The orchestrator then:

  1. β-dedups the union via `dedup.group_by_identifier`
  2. Optionally batch-fills missing metadata (openalex works batch,
     pubmed efetch batch) — enabled by default, `--no-fetch-meta` skips
  3. Merges each group with `merge.merge_group`
  4. Splits into resolved (has ≥1 identifier) and unresolved (only
     `raw_citation` from Crossref)
  5. Sorts:
       - refs     → source-natural order (input order preserved)
       - cited-by → citations:desc (default) or year:desc
  6. Truncates by --top (or leaves whole when --all set)
  7. Stamps each record with `direction` and `seed`, drops
     search-specific fields (score / rank_by_source / dedup_group)
  8. Emits via `output.emit` and writes summary + unresolved streams.

Exit codes: 0 (incl. empty result), 3 (seed not found in ANY source),
5 (all sources HTTP-failed). See references/citations.md.
"""

from __future__ import annotations

import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable

import dedup
import merge
import output
from config import SearchConfig
from seed_resolver import SeedError, SeedId, SeedResolveError, classify
from sources import crossref, openalex, pubmed, s2


# --------------------------------------------------------------------------- #
# Source-fan-out matrix per problem-3 decision
# --------------------------------------------------------------------------- #

# Each fetcher returns list[record_dict]. Signature: (seed: SeedId, cfg,
# limit_or_None) -> list[dict]. Raises are caught by the caller and turned
# into "source failed".


def _fetch_refs_openalex(seed: SeedId, cfg: SearchConfig,
                         limit: int | None) -> list[dict[str, Any]]:
    # OpenAlex needs the seed as W-id or DOI; falls back to DOI when both.
    ident = seed.get("openalex_id") or seed.get("doi")
    if not ident:
        return []
    return openalex.get_referenced_works(ident, cfg=cfg)


def _fetch_refs_s2(seed: SeedId, cfg: SearchConfig,
                   limit: int | None) -> list[dict[str, Any]]:
    if seed.kind == "openalex":
        # S2 does not accept OpenAlex ids; if we have a DOI we already
        # populated ids, use it; otherwise skip.
        if seed.get("doi"):
            return s2.get_references(seed.get("doi") or "", "doi",
                                     cfg=cfg, limit=limit)
        return []
    primary_field = {
        "doi": "doi", "arxiv": "arxiv_id",
        "pmid": "pmid", "s2": "s2_id",
    }.get(seed.kind)
    if not primary_field:
        return []
    val = seed.get(primary_field) or seed.primary
    return s2.get_references(val, seed.kind, cfg=cfg, limit=limit)


def _fetch_refs_crossref(seed: SeedId, cfg: SearchConfig,
                         limit: int | None) -> list[dict[str, Any]]:
    doi = seed.get("doi")
    if not doi:
        return []
    return crossref.get_references(doi, cfg=cfg)


def _fetch_refs_pubmed(seed: SeedId, cfg: SearchConfig,
                       limit: int | None) -> list[dict[str, Any]]:
    pmid = seed.get("pmid")
    if not pmid:
        return []
    linked = pubmed.get_refs_elink(pmid, cfg=cfg)
    if not linked:
        return []
    recs_by_pmid = pubmed.fetch_by_pmids(linked, cfg=cfg)
    return [recs_by_pmid[p] for p in linked if p in recs_by_pmid]


def _fetch_cited_openalex(seed: SeedId, cfg: SearchConfig,
                          limit: int | None) -> list[dict[str, Any]]:
    ident = seed.get("openalex_id") or seed.get("doi")
    if not ident:
        return []
    return openalex.get_cited_by(ident, cfg=cfg, limit=limit)


def _fetch_cited_s2(seed: SeedId, cfg: SearchConfig,
                    limit: int | None) -> list[dict[str, Any]]:
    if seed.kind == "openalex":
        if seed.get("doi"):
            return s2.get_citations(seed.get("doi") or "", "doi",
                                    cfg=cfg, limit=limit)
        return []
    primary_field = {
        "doi": "doi", "arxiv": "arxiv_id",
        "pmid": "pmid", "s2": "s2_id",
    }.get(seed.kind)
    if not primary_field:
        return []
    val = seed.get(primary_field) or seed.primary
    return s2.get_citations(val, seed.kind, cfg=cfg, limit=limit)


def _fetch_cited_pubmed(seed: SeedId, cfg: SearchConfig,
                        limit: int | None) -> list[dict[str, Any]]:
    pmid = seed.get("pmid")
    if not pmid:
        return []
    linked = pubmed.get_citedin_elink(pmid, cfg=cfg)
    if not linked:
        return []
    if limit is not None:
        linked = linked[:limit]
    recs_by_pmid = pubmed.fetch_by_pmids(linked, cfg=cfg)
    return [recs_by_pmid[p] for p in linked if p in recs_by_pmid]


_REFS_FETCHERS: dict[str, Callable[..., list[dict[str, Any]]]] = {
    "openalex": _fetch_refs_openalex,
    "s2":       _fetch_refs_s2,
    "crossref": _fetch_refs_crossref,
    "pubmed":   _fetch_refs_pubmed,
}

_CITED_FETCHERS: dict[str, Callable[..., list[dict[str, Any]]]] = {
    "openalex": _fetch_cited_openalex,
    "s2":       _fetch_cited_s2,
    "pubmed":   _fetch_cited_pubmed,
}


# --------------------------------------------------------------------------- #
# Fan-out
# --------------------------------------------------------------------------- #


def _fanout(fetchers: dict[str, Callable[..., list[dict[str, Any]]]],
            seed: SeedId, cfg: SearchConfig,
            limit: int | None,
            selected: list[str] | None) -> tuple[list[dict[str, Any]],
                                                  dict[str, int],
                                                  dict[str, str]]:
    """Run each source fetcher concurrently. Returns (records, per_source_count,
    per_source_failure)."""
    active = {name: fn for name, fn in fetchers.items()
              if selected is None or name in selected}
    records: list[dict[str, Any]] = []
    counts: dict[str, int] = {}
    failures: dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=len(active)) as pool:
        futs = {pool.submit(fn, seed, cfg, limit): name
                for name, fn in active.items()}
        for fut in as_completed(futs):
            name = futs[fut]
            try:
                recs = fut.result() or []
            except Exception as e:  # noqa: BLE001
                failures[name] = str(e)[:200]
                counts[name] = 0
                continue
            counts[name] = len(recs)
            records.extend(recs)
    return records, counts, failures


# --------------------------------------------------------------------------- #
# Meta-fill for identifier-only records (OpenAlex refs / PubMed elink)
# --------------------------------------------------------------------------- #


def _needs_meta(rec: dict[str, Any]) -> bool:
    """A record needs meta-fill when it has an identifier but no title."""
    if rec.get("title"):
        return False
    return bool(rec.get("openalex_id") or rec.get("pmid"))


def _fill_meta(records: list[dict[str, Any]], cfg: SearchConfig) -> list[dict[str, Any]]:
    """For every openalex-id-only or pmid-only record, batch-fetch full
    metadata and merge back. Appends the fetched records into the list
    (they'll β-dedup with the sparse ones in the next stage)."""
    open_wids: list[str] = []
    open_pmids: list[str] = []
    for r in records:
        if not _needs_meta(r):
            continue
        if r.get("openalex_id"):
            open_wids.append(r["openalex_id"])
        elif r.get("pmid"):
            open_pmids.append(r["pmid"])

    filled: list[dict[str, Any]] = list(records)  # copy
    if open_wids:
        try:
            batch = openalex.get_works_batch(open_wids, cfg=cfg)
            filled.extend(batch)
        except Exception:  # noqa: BLE001
            pass
    if open_pmids:
        try:
            batch = pubmed.fetch_by_pmids(open_pmids, cfg=cfg)
            filled.extend(batch.values())
        except Exception:  # noqa: BLE001
            pass
    return filled


# --------------------------------------------------------------------------- #
# Main entry points — cmd_refs / cmd_cited_by
# --------------------------------------------------------------------------- #


def cmd_refs(args: Any, cfg: SearchConfig) -> int:
    return _run(args, cfg, direction="refs")


def cmd_cited_by(args: Any, cfg: SearchConfig) -> int:
    return _run(args, cfg, direction="cited-by")


def _run(args: Any, cfg: SearchConfig, *, direction: str) -> int:
    # 1. Classify seed
    try:
        seed = classify(args.seed)
    except SeedError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    except SeedResolveError as e:
        print(f"error: {e}", file=sys.stderr)
        return 3

    # 2. Pick source set + limit + per-record cap
    if direction == "refs":
        fetchers = _REFS_FETCHERS
        # top defaults to None (=all) for refs
        top: int | None = args.top if args.top is not None else None
        per_source_limit = top  # s2 uses this as its stop-early hint
    else:
        fetchers = _CITED_FETCHERS
        # cited-by: --top and --all are mutually exclusive
        if args.all and args.top is not None:
            print("error: --top and --all are mutually exclusive", file=sys.stderr)
            return 2
        if args.all:
            top = None
        else:
            top = args.top if args.top is not None else 100
        per_source_limit = top

    # 3. Select sources per --sources
    selected = _parse_sources(args.sources)
    if selected is not None:
        selected = [s for s in selected if s in fetchers]

    # 4. Fan out
    raw_records, counts, failures = _fanout(fetchers, seed, cfg,
                                            per_source_limit, selected)

    all_source_names = list(selected) if selected is not None else list(fetchers.keys())
    n_ok_sources = sum(1 for s in all_source_names if s not in failures)

    # 5. Empty across all sources: distinguish "all HTTP failed" vs "seed just has no refs"
    if not raw_records and n_ok_sources == 0:
        print(f"error: all citation sources failed for seed {seed.primary!r}",
              file=sys.stderr)
        for src, reason in failures.items():
            print(f"  {src}: {reason}", file=sys.stderr)
        return 5

    # 6. Batch-fill metadata (unless --no-fetch-meta)
    if not getattr(args, "no_fetch_meta", False):
        raw_records = _fill_meta(raw_records, cfg)

    # 7. β-dedup + merge (per group, so we can preserve raw_citation)
    groups = dedup.group_by_identifier(raw_records)

    resolved: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []
    for g in groups:
        merged = merge.merge_group(g)
        # Strip search-specific fields we don't want in citation output
        merged.pop("rank_by_source", None)
        merged.pop("dedup_group", None)
        meta = merged.get("meta") or {}
        has_id = any(meta.get(k) for k in ("doi", "pmid", "arxiv_id", "openalex_id", "s2_id"))
        if has_id:
            resolved.append(merged)
        else:
            # Pluck the first non-empty raw_citation from the group
            raw = None
            for r in g:
                if r.get("raw_citation"):
                    raw = r["raw_citation"]
                    break
            merged["raw_citation"] = raw
            unresolved.append(merged)

    # 9. Sort
    if direction == "refs":
        # source-natural order — merged records come out of dict order which
        # ~preserves input order for the first-seen groups. Leave as-is.
        pass
    else:
        sort_key = getattr(args, "sort", "citations:desc") or "citations:desc"
        _sort_cited_by(resolved, sort_key)

    # 10. Truncate
    if top is not None:
        resolved = resolved[:top]

    # 11. Stamp direction + seed + drop score
    for i, rec in enumerate(resolved):
        rec.pop("rank_by_source", None)
        rec.pop("dedup_group", None)
        rec.pop("score", None)
        rec["index"] = i
        rec["direction"] = direction
        rec["seed"] = seed.primary
    for j, rec in enumerate(unresolved):
        rec.pop("rank_by_source", None)
        rec.pop("dedup_group", None)
        rec.pop("score", None)
        rec["index"] = j
        rec["direction"] = direction
        rec["seed"] = seed.primary

    # 12. Summary
    summary = {
        "seed": seed.primary,
        "seed_kind": seed.kind,
        "direction": direction,
        "resolved": len(resolved),
        "unresolved": len(unresolved),
        "sources_ok": {s: counts.get(s, 0) for s in all_source_names if s not in failures},
        "sources_failed": failures,
    }

    # 13. Emit main stream
    output.emit(resolved,
                fmt=args.format, out=args.out,
                summary=summary if args.format == "ndjson" else None)

    # 14. Emit unresolved stream (always ndjson to a separate file)
    if unresolved and getattr(args, "out_unresolved", None):
        output.write_ndjson(unresolved, out=args.out_unresolved, summary=None)

    # 15. Exit code: 3 when seed literally does not exist in any source
    #     (every source succeeded but returned nothing AND we got no results
    #     at all after dedup). Otherwise 0 — an empty resolved list is
    #     legitimate ("this paper has no cited-by").
    seed_missing = (
        len(raw_records) == 0
        and n_ok_sources == len(all_source_names)  # no failures
        and not failures
    )
    if seed_missing:
        # But only claim "not found" when the seed itself is not resolvable
        # by any source — if the paper existed but had zero refs/citations,
        # that's still success. We approximate: 0 records + 0 failures + at
        # least one source that could accept the seed → paper likely not
        # in that source's index.
        # However this heuristic can't tell "paper exists with 0 refs" from
        # "paper doesn't exist". We favor exit 3 here because a totally
        # empty response across all sources overwhelmingly indicates a bad
        # seed (a real paper has at least ONE ref-graph hit somewhere).
        print(f"note: no results across sources for seed {seed.primary!r}",
              file=sys.stderr)
        return 3
    return 0


def _sort_cited_by(records: list[dict[str, Any]], sort_key: str) -> None:
    key = (sort_key or "").lower()
    if key == "year:desc":
        records.sort(key=lambda r: (r.get("meta", {}).get("year") or 0),
                     reverse=True)
    else:  # citations:desc default
        records.sort(key=lambda r: (r.get("meta", {}).get("citation_count") or 0),
                     reverse=True)


def _parse_sources(arg: str | None) -> list[str] | None:
    if not arg:
        return None
    return [s.strip().lower() for s in arg.split(",") if s.strip()]


__all__ = ["cmd_refs", "cmd_cited_by"]
