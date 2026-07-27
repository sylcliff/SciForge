"""`sf-search doctor` — environment & per-source reachability check."""

from __future__ import annotations

import shutil
import subprocess
import sys
import time
from typing import Any

from config import HTTPError, SearchConfig, build_url, http_get

# Cheap "does the API answer at all" pings — each source's search
# endpoint with a trivial query.
_PROBES: dict[str, tuple[str, dict[str, Any]]] = {
    "pubmed": (
        "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/einfo.fcgi",
        {"db": "pubmed", "retmode": "json"},
    ),
    "crossref": (
        "https://api.crossref.org/works",
        {"rows": 1, "query": "test"},
    ),
    "arxiv": (
        "http://export.arxiv.org/api/query",
        {"search_query": "all:test", "max_results": 1},
    ),
    "openalex": (
        "https://api.openalex.org/works",
        {"search": "test", "per-page": 1},
    ),
    "s2": (
        "https://api.semanticscholar.org/graph/v1/paper/search",
        {"query": "test", "limit": 1, "fields": "title"},
    ),
}


def _probe(source: str, cfg: SearchConfig) -> tuple[bool, int, str]:
    base, params = _PROBES[source]
    if source in ("crossref", "openalex") and cfg.polite_email:
        params = {**params, "mailto": cfg.polite_email}
    url = build_url(base, params)
    t0 = time.monotonic()
    try:
        http_get(url, source=source, cfg=cfg)
        return True, int((time.monotonic() - t0) * 1000), ""
    except HTTPError as e:
        return False, int((time.monotonic() - t0) * 1000), e.reason


def cmd_doctor(cfg: SearchConfig) -> int:
    print("sf-search doctor")
    print("================")

    if cfg.polite_email:
        print(f"polite email:         {cfg.polite_email}           (from SCIFORGE_POLITE_EMAIL)")
    else:
        print("polite email:         (not set)                      (set SCIFORGE_POLITE_EMAIL)")

    if cfg.semanticscholar_api_key:
        n = len(cfg.semanticscholar_api_key)
        print(f"S2 API key:           set ({n} chars)                (from SCIFORGE_S2_API_KEY)")
    else:
        print("S2 API key:           (not set)                      (set SCIFORGE_S2_API_KEY)")

    print(f"python:               {sys.version.split()[0]}")
    print(f"config file:          {cfg._source_path}")
    print(f"HTTP timeout:         {cfg.http_timeout_seconds}s")
    print("per-source reachability:")

    any_reachable = False
    for src in ("pubmed", "crossref", "arxiv", "openalex", "s2"):
        ok, ms, reason = _probe(src, cfg)
        if ok:
            any_reachable = True
            print(f"  {src:<16}    ok  ({ms} ms)")
        else:
            print(f"  {src:<16}    fail ({ms} ms) — {reason}")

    print()

    # --- Citation graph support matrix ------------------------------------
    # Static: driven by which endpoints each adapter exposes today.
    print("citation graph support:")
    matrix = [
        # (source, refs, cited-by, note)
        ("openalex", "yes",          "yes",         ""),
        ("s2",       "yes",          "yes",         ""),
        ("crossref", "yes",          "n/a",         "Event Data API not supported"),
        ("pubmed",   "PMID + PMC only", "PMID + PMC only",
                                                    "requires the seed to be a PMID and the paper to be in PubMed Central"),
        ("arxiv",    "n/a",          "n/a",         "Atom API returns no citation graph"),
    ]
    for src, refs, cited, note in matrix:
        line = f"  {src:<10}  refs: {refs:<18}  cited-by: {cited:<18}"
        if note:
            line += f"  ({note})"
        print(line)

    print()

    # --- sf-lit resolver check --------------------------------------------
    # Only needed when users pass `sciforge://literature/<key>` seeds.
    print("sf-lit resolver (needed for sciforge://literature/<key> seeds):")
    sf_lit = shutil.which("sf-lit")
    if not sf_lit:
        print("  sf-lit on PATH:       (not found)  — non-URI seeds still work")
    else:
        print(f"  sf-lit on PATH:       {sf_lit}")
        # Probe: call `sf-lit show __nonexistent-key__ --json`.
        # Success = exit code 3 with a not-found message. Anything else
        # signals a schema/library problem worth flagging.
        try:
            proc = subprocess.run(  # noqa: S603 — sf_lit resolved via which
                [sf_lit, "show", "__sf_search_doctor_probe__", "--json"],
                capture_output=True, text=True, timeout=10, check=False,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired) as e:
            print(f"  sf-lit probe:         fail — {e}")
        else:
            if proc.returncode == 3:
                print("  sf-lit probe:         ok (exit 3 for unknown citekey — expected)")
            elif proc.returncode == 0:
                print("  sf-lit probe:         unexpected success (no library init?)")
            else:
                # Exit 3 also fires when library isn't init'd. Both non-fatal.
                stderr = (proc.stderr or "").strip()[:80]
                print(f"  sf-lit probe:         exit {proc.returncode} — {stderr}")

    print()
    if not any_reachable:
        print("WARNING: no sources reachable. Check network / firewall.", file=sys.stderr)
        return 0  # Doctor never errors on env issues, only reports them
    return 0


__all__ = ["cmd_doctor"]
