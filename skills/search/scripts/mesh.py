"""MeSH workflow: lookup / build / check subcommands.

lookup: hits PubMed einfo + efetch (db=mesh)
build:  pure local — assemble strategy.json
check:  hits PubMed espell + esearch (rettype=count)
"""

from __future__ import annotations

import json
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from config import VERSION, HTTPError, SearchConfig, build_url, http_get, http_get_json

_BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"


# --------------------------------------------------------------------------- #
# lookup
# --------------------------------------------------------------------------- #


def _lookup_one_concept(concept: str, cfg: SearchConfig, top: int = 5) -> list[dict[str, Any]]:
    """Return up to `top` MeSH candidates for one concept string."""
    esearch = build_url(f"{_BASE}/esearch.fcgi", {
        "db": "mesh", "term": concept, "retmax": top, "retmode": "json",
    })
    data = http_get_json(esearch, source="pubmed", cfg=cfg)
    uids = data.get("esearchresult", {}).get("idlist", []) or []
    if not uids:
        return []

    # NCBI serves MeSH efetch as plain-text MeSH report format.
    efetch = build_url(f"{_BASE}/efetch.fcgi", {
        "db": "mesh", "id": ",".join(uids), "retmode": "text", "rettype": "full",
    })
    body = http_get(efetch, source="pubmed", cfg=cfg)
    return _parse_mesh_report(body.decode("utf-8", errors="replace"), uids, concept)


def _parse_mesh_report(text: str, uids: list[str], concept: str) -> list[dict[str, Any]]:
    """Parse NCBI's plain-text MeSH report format.

    Records are numbered '1: Term Name\\n<scope note>\\n\\nSubheadings:\\n  ...\\n
    Tree Number(s): C1.1, C2.2\\nEntry Terms:\\n    Synonym1\\n    Synonym2\\n'.

    Multiple records are separated by a blank line before the next 'N: ' marker.
    """
    import re

    # Split on lines that look like '  N: Term' (record header)
    # Records are separated by "\n\n<num>: ..." patterns.
    blocks = re.split(r"\n(?=\d+:\s)", text.strip())

    out: list[dict[str, Any]] = []
    for idx, block in enumerate(blocks):
        rec = _parse_one_mesh_block(block)
        if not rec:
            continue
        rec["concept_input"] = concept
        rec["rank"] = idx + 1
        # Map by index → uid (order aligned with esearch idlist)
        if idx < len(uids):
            rec["mesh_ui"] = uids[idx]
        out.append(rec)
    return out


def _parse_one_mesh_block(block: str) -> dict[str, Any] | None:
    """Parse one text block into a MeSH record dict."""
    import re

    lines = block.split("\n")
    if not lines:
        return None

    # Header line: '1: Diabetes Mellitus'  (or '1: Donohue Syndrome')
    m = re.match(r"^\d+:\s+(.+)$", lines[0].strip())
    if not m:
        return None
    mesh_term = m.group(1).strip()

    # Scope note: continuous non-blank lines after the header until first blank
    scope_lines: list[str] = []
    i = 1
    while i < len(lines) and lines[i].strip() and not lines[i].strip().endswith(":"):
        # Stop when we hit a labeled section like "Subheadings:" or "Tree Number(s):"
        if lines[i].strip() in ("Subheadings:", "Entry Terms:", "See Also:", "Previous Indexing:"):
            break
        if lines[i].strip().startswith(("Tree Number(s):", "Year introduced:")):
            break
        scope_lines.append(lines[i].strip())
        i += 1
    scope_note = " ".join(scope_lines).strip() or None

    # Tree numbers
    tree_numbers: list[str] = []
    tree_match = re.search(r"Tree Number\(s\):\s*(.+)", block)
    if tree_match:
        tree_numbers = [t.strip() for t in tree_match.group(1).split(",") if t.strip()]

    # Entry terms (synonyms)
    entry_terms: list[str] = []
    entry_match = re.search(r"Entry Terms:\s*\n((?:\s{4,}.+\n?)+)", block)
    if entry_match:
        for line in entry_match.group(1).split("\n"):
            term = line.strip()
            if term:
                entry_terms.append(term)

    return {
        "mesh_ui": None,   # filled by caller from esearch idlist
        "mesh_term": mesh_term,
        "scope_note": scope_note,
        "tree_numbers": tree_numbers,
        "parents": [],
        "children": [],
        "entry_terms": entry_terms,
    }


def cmd_lookup(args: Any, cfg: SearchConfig) -> int:
    """`sf-search mesh lookup --concept X --concept Y`"""
    for concept in args.concept:
        try:
            candidates = _lookup_one_concept(concept, cfg, top=args.top)
        except HTTPError as e:
            print(json.dumps({"concept_input": concept, "status": "error", "error": str(e)}),
                  file=sys.stdout)
            continue
        if not candidates:
            print(json.dumps({"concept_input": concept, "status": "no_candidates"}),
                  file=sys.stdout)
            continue
        for c in candidates:
            print(json.dumps(c, ensure_ascii=False))
    return 0


# --------------------------------------------------------------------------- #
# build
# --------------------------------------------------------------------------- #


def _compile_pubmed(concepts: list[dict[str, Any]], op: str) -> str:
    parts = []
    for c in concepts:
        mesh_term = c["mesh"]
        terms = [f'"{mesh_term}"[MeSH]', f'"{mesh_term}"[tiab]']
        for syn in c.get("synonyms") or []:
            terms.append(f'{syn}[tiab]')
        parts.append("(" + " OR ".join(terms) + ")")
    return f" {op} ".join(parts)


def _compile_free_text(concepts: list[dict[str, Any]], op: str) -> str:
    parts = []
    for c in concepts:
        terms = [f'"{c["mesh"]}"'] + [f'"{s}"' if " " in s else s for s in (c.get("synonyms") or [])]
        parts.append("(" + " OR ".join(terms) + ")")
    return f" {op} ".join(parts)


def _compile_arxiv(concepts: list[dict[str, Any]], op: str) -> str:
    parts = []
    for c in concepts:
        terms = [f'all:"{c["mesh"]}"'] + [
            f'all:"{s}"' if " " in s else f"all:{s}" for s in (c.get("synonyms") or [])
        ]
        parts.append("(" + " OR ".join(terms) + ")")
    return f" {op} ".join(parts)


def compile_strategy(concepts: list[dict[str, Any]], op: str = "AND") -> dict[str, str]:
    ft = _compile_free_text(concepts, op)
    return {
        "pubmed": _compile_pubmed(concepts, op),
        "crossref": ft,
        "openalex": ft,
        "s2": ft,
        "arxiv": _compile_arxiv(concepts, op),
    }


def cmd_build(args: Any, cfg: SearchConfig) -> int:
    """`sf-search mesh build --mesh X --synonym Y --op AND -o strategy.json`

    Repeated `--mesh` starts a new concept. Repeated `--synonym` attaches
    to the most recent `--mesh`.
    """
    concepts: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None

    # argparse gives us: args.terms = list of (kind, value) pairs
    for kind, value in args.terms:
        if kind == "mesh":
            current = {"mesh": value, "synonyms": []}
            concepts.append(current)
        elif kind == "synonym":
            if current is None:
                print(f"error: --synonym {value!r} given before any --mesh", file=sys.stderr)
                return 2
            current["synonyms"].append(value)

    if not concepts:
        print("error: at least one --mesh is required", file=sys.stderr)
        return 2

    op = (args.op or "AND").upper()
    if op not in ("AND", "OR"):
        print(f"error: --op must be AND or OR, got {args.op!r}", file=sys.stderr)
        return 2

    strategy = {
        "version": 1,
        "concepts": concepts,
        "op": op,
        "compiled": compile_strategy(concepts, op),
        "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "tool": "sf-search",
        "tool_version": VERSION,
    }
    output_json = json.dumps(strategy, ensure_ascii=False, indent=2)
    if args.out and args.out != "-":
        Path(args.out).write_text(output_json + "\n", encoding="utf-8")
        print(f"wrote strategy → {args.out}", file=sys.stderr)
    else:
        sys.stdout.write(output_json + "\n")
    return 0


# --------------------------------------------------------------------------- #
# check
# --------------------------------------------------------------------------- #


def cmd_check(args: Any, cfg: SearchConfig) -> int:
    """`sf-search mesh check strategy.json`"""
    strategy_path = Path(args.strategy_path).expanduser()
    if not strategy_path.is_file():
        print(f"error: not found: {strategy_path}", file=sys.stderr)
        return 3
    try:
        strategy = json.loads(strategy_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        print(f"error: strategy JSON invalid: {e}", file=sys.stderr)
        return 2

    compiled = strategy.get("compiled") or {}
    pubmed_q = compiled.get("pubmed", "")

    report: dict[str, Any] = {"compiled": compiled}

    if pubmed_q:
        # Count
        count_url = build_url(f"{_BASE}/esearch.fcgi", {
            "db": "pubmed", "term": pubmed_q, "retmode": "json", "rettype": "count",
        })
        try:
            data = http_get_json(count_url, source="pubmed", cfg=cfg)
            report["pubmed_count"] = int(data.get("esearchresult", {}).get("count") or 0)
        except HTTPError as e:
            report["pubmed_count"] = None
            report["pubmed_count_error"] = str(e)

        # Spell check
        spell_url = build_url(f"{_BASE}/espell.fcgi", {
            "db": "pubmed", "term": pubmed_q, "retmode": "xml",
        })
        try:
            body = http_get(spell_url, source="pubmed", cfg=cfg)
            root = ET.fromstring(body)
            correction = root.findtext("CorrectedQuery") or ""
            report["suggested_corrections"] = (
                [correction.strip()] if correction.strip() and correction.strip() != pubmed_q
                else []
            )
        except (HTTPError, ET.ParseError) as e:
            report["suggested_corrections"] = []
            report["spellcheck_error"] = str(e)

    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


__all__ = ["cmd_lookup", "cmd_build", "cmd_check", "compile_strategy"]
