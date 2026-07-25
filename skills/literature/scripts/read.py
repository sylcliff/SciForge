#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///

"""`litlib read <citekey>` — read canonical Markdown, whole or in slices.

Q11/B: this is the paired *read* command for search. ``search`` returns
paper hits with a short snippet; ``read`` is how the caller pulls
content out of a specific paper by section, page range, kind, or regex.

Modes (mutually exclusive; pick zero or one):

- **no flag** → whole ``paper.md`` to stdout.
- **``--section S``** → fuzzy substring match over headings; every
  matching heading's slice is returned. Works for both MinerU papers
  (headings from ``*_content_list.json``, with page numbers) and
  Docling papers (headings parsed from the top-level ``paper.md``).
  Output is **always an array** — 0, 1, or N sections.
- **``--pages P``** → MinerU-only. ``3``, ``3-5``, or ``3,7,9-11``.
  Returns each requested page's contents by reading page ranges in
  ``*_content_list.json``.
- **``--kind K``** → MinerU-only. ``table | equation | image_caption |
  code | text``. Returns every block whose ``type`` matches.
- **``--grep RE``** → line-oriented regex over the whole ``paper.md``,
  Python regex syntax. Returns matches with 1-line context.

Docling papers fail loudly on ``--pages`` / ``--kind`` — those flags
have no equivalent in Docling's output.
"""

import json
import re
import sys
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import config as config_mod  # noqa: E402
import db as dbmod  # noqa: E402


# ---- resolution --------------------------------------------------------


def _resolve_key(key: str) -> dict | None:
    for field in ("citekey", "arxiv_id", "doi"):
        row = dbmod.fetchone(f"SELECT * FROM papers WHERE {field} = ?", (key,))
        if row:
            return dict(row)
    return None


def _paper_dir(lib: Path, citekey: str) -> Path:
    return lib / "papers" / citekey


def _read_sidecar(paper_dir: Path) -> dict:
    p = paper_dir / "converter.json"
    if not p.is_file():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _load_content_list(paper_dir: Path, converter: str) -> list[dict] | None:
    """MinerU stores blocks in reading order in ``*_content_list.json``.

    Returns the parsed list, or None if the paper wasn't MinerU-converted
    or the file is missing.
    """
    if converter != "mineru":
        return None
    root = paper_dir / "converter_output" / "mineru"
    if not root.is_dir():
        return None
    # Prefer v2 if present (Q4/A keeps everything).
    for name in ("_content_list_v2.json", "_content_list.json"):
        for p in root.rglob(f"*{name}"):
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(data, list):
                return data
    return None


# ---- fuzzy matcher (Q19/B, shared by both providers) -------------------


_PUNCT_RE = re.compile(r"[^\w\s]+", flags=re.UNICODE)


def _normalize_heading(text: str) -> list[str]:
    """Lowercase + strip diacritics + split into alnum tokens."""
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.lower()
    text = _PUNCT_RE.sub(" ", text)
    return [t for t in text.split() if t]


def _tokens_subseq(needle: list[str], hay: list[str]) -> bool:
    """True if ``needle`` appears as a contiguous token slice of ``hay``."""
    if not needle:
        return False
    n = len(needle)
    for i in range(len(hay) - n + 1):
        if hay[i:i + n] == needle:
            return True
    return False


def _heading_matches(query: str, heading: str) -> bool:
    q = _normalize_heading(query)
    h = _normalize_heading(heading)
    return _tokens_subseq(q, h)


# ---- heading providers -------------------------------------------------


def _headings_from_mineru(content_list: list[dict]) -> list[dict]:
    """Extract heading records from MinerU's content_list.

    Each returned dict: ``{index, text, level, page_from, page_to}``
    where ``index`` is the position in the reading order.
    """
    out = []
    for i, block in enumerate(content_list):
        if not isinstance(block, dict):
            continue
        t = block.get("type", "")
        # MinerU uses "text" with a "text_level" attribute for headings.
        level = block.get("text_level")
        if t in ("title",) or (t == "text" and level):
            heading = block.get("text") or ""
            if not heading:
                continue
            page = block.get("page_idx")
            if isinstance(page, int):
                page = page + 1  # convert to 1-indexed
            out.append({
                "index": i,
                "text": str(heading).strip(),
                "level": int(level) if level else 1,
                "page_from": page,
                "page_to": page,
            })
    return out


_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$", flags=re.MULTILINE)


def _headings_from_markdown(md: str) -> list[dict]:
    """Extract ATX-style headings from a raw markdown string.

    Records line offsets so we can slice the body later.
    """
    out = []
    for m in _HEADING_RE.finditer(md):
        level = len(m.group(1))
        text = m.group(2).strip()
        # Compute the 1-indexed line number where the heading sits.
        line = md.count("\n", 0, m.start()) + 1
        out.append({
            "index": m.start(),
            "text": text,
            "level": level,
            "line": line,
            "page_from": None,
            "page_to": None,
        })
    return out


# ---- section extraction -----------------------------------------------


def _extract_section_mineru(content_list: list[dict], start: dict) -> dict:
    """Given a heading record, return the block slice up to the next same-or-higher heading.

    The heading block itself is emitted as the section's title (via the
    ``heading`` field on the return value) — we skip it in the body so
    the caller doesn't see the heading duplicated.
    """
    start_idx = start["index"]
    end_idx = len(content_list)
    for j in range(start_idx + 1, len(content_list)):
        b = content_list[j]
        if not isinstance(b, dict):
            continue
        blevel = b.get("text_level")
        btype = b.get("type", "")
        if (btype == "title" or (btype == "text" and blevel)) and blevel and blevel <= start["level"]:
            end_idx = j
            break
    # Skip the heading block itself (start_idx) — it's already the section title.
    blocks = content_list[start_idx + 1:end_idx]
    lines: list[str] = []
    page_from: int | None = None
    page_to: int | None = None
    # Include the heading block's own page in the range so the section
    # ranges look right in the output.
    hp = content_list[start_idx].get("page_idx")
    if isinstance(hp, int):
        page_from = page_to = hp + 1
    for b in blocks:
        if not isinstance(b, dict):
            continue
        p = b.get("page_idx")
        if isinstance(p, int):
            p1 = p + 1
            page_from = p1 if page_from is None else min(page_from, p1)
            page_to = p1 if page_to is None else max(page_to, p1)
        text = b.get("text") or ""
        btype = b.get("type", "")
        if btype == "table":
            html = b.get("table_body") or b.get("html")
            if html:
                lines.append(str(html))
            elif text:
                lines.append(str(text))
        elif btype == "equation":
            lines.append(str(text))
        elif btype in ("image", "figure"):
            cap = b.get("img_caption") or b.get("caption") or ""
            if isinstance(cap, list):
                cap = " ".join(str(c) for c in cap)
            if cap:
                lines.append(f"![]({b.get('img_path', '')})  \n_{cap}_")
        else:
            if text:
                lines.append(str(text))
    return {
        "heading": start["text"],
        "level": start["level"],
        "page_from": page_from,
        "page_to": page_to,
        "text": "\n\n".join(lines).strip(),
    }


def _extract_section_markdown(md: str, headings: list[dict], start: dict) -> dict:
    """Slice ``md`` from ``start`` to the next same-or-higher heading."""
    start_pos = start["index"]
    end_pos = len(md)
    for h in headings:
        if h["index"] <= start_pos:
            continue
        if h["level"] <= start["level"]:
            end_pos = h["index"]
            break
    body = md[start_pos:end_pos].strip()
    return {
        "heading": start["text"],
        "level": start["level"],
        "page_from": None,
        "page_to": None,
        "text": body,
    }


# ---- pages / kind (MinerU-only) ---------------------------------------


def _parse_pages(expr: str) -> set[int]:
    """Parse ``3``, ``3-5``, ``3,7,9-11`` into a set of 1-indexed page numbers."""
    out: set[int] = set()
    for chunk in expr.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if "-" in chunk:
            lo, hi = chunk.split("-", 1)
            out.update(range(int(lo), int(hi) + 1))
        else:
            out.add(int(chunk))
    return out


def _extract_pages_mineru(content_list: list[dict], pages: set[int]) -> list[dict]:
    """Return blocks whose page_idx (1-indexed) is in ``pages``."""
    grouped: dict[int, list[str]] = {}
    for b in content_list:
        if not isinstance(b, dict):
            continue
        p = b.get("page_idx")
        if not isinstance(p, int):
            continue
        page1 = p + 1
        if page1 not in pages:
            continue
        text = b.get("text") or ""
        if isinstance(text, list):
            text = " ".join(str(x) for x in text)
        grouped.setdefault(page1, []).append(str(text))
    out = []
    for page in sorted(grouped):
        out.append({
            "page": page,
            "text": "\n\n".join(x for x in grouped[page] if x).strip(),
        })
    return out


_KIND_MAP = {
    "table": {"table"},
    "equation": {"equation", "interline_equation"},
    "image_caption": {"image", "figure"},
    "code": {"code"},
    "text": {"text"},
}


def _extract_kind_mineru(content_list: list[dict], kind: str) -> list[dict]:
    """Return blocks whose type matches ``kind`` (grouped via _KIND_MAP)."""
    types = _KIND_MAP.get(kind)
    if not types:
        return []
    out = []
    for i, b in enumerate(content_list):
        if not isinstance(b, dict):
            continue
        if b.get("type") not in types:
            continue
        p = b.get("page_idx")
        page = p + 1 if isinstance(p, int) else None
        if kind == "image_caption":
            cap = b.get("img_caption") or b.get("caption") or ""
            if isinstance(cap, list):
                cap = " ".join(str(c) for c in cap)
            text = str(cap)
        elif kind == "table":
            text = b.get("table_body") or b.get("html") or b.get("text") or ""
            if isinstance(text, list):
                text = " ".join(str(x) for x in text)
            text = str(text)
        else:
            text = str(b.get("text") or "")
        if not text.strip():
            continue
        out.append({
            "index": i,
            "kind": kind,
            "page": page,
            "text": text,
        })
    return out


# ---- grep -------------------------------------------------------------


def _grep_md(md: str, pattern: str) -> list[dict]:
    try:
        rx = re.compile(pattern)
    except re.error as e:
        raise ValueError(f"bad --grep regex: {e}") from e
    out = []
    for i, line in enumerate(md.splitlines(), start=1):
        if rx.search(line):
            out.append({"line": i, "text": line})
    return out


# ---- runner ------------------------------------------------------------


def _load_paper_md(paper_dir: Path) -> str:
    top = paper_dir / "paper.md"
    if not top.is_file():
        raise FileNotFoundError(f"no paper.md at {top} — run `litlib convert` first")
    return top.read_text(encoding="utf-8")


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
    if paper.get("md_status") == "absent":
        print(
            f"error: {citekey} has md_status=absent; run "
            f"`litlib convert {citekey}` first",
            file=sys.stderr,
        )
        return 1

    paper_dir = _paper_dir(lib, citekey)
    sidecar = _read_sidecar(paper_dir)
    converter = sidecar.get("converter", "unknown")

    try:
        md = _load_paper_md(paper_dir)
    except FileNotFoundError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    if paper.get("md_status") == "stale" and not args.json:
        print(
            f"warn: {citekey} is stale (PDF changed since last convert); "
            f"content below reflects the old PDF",
            file=sys.stderr,
        )

    # Mode dispatch. Only one of --section / --pages / --kind / --grep.
    modes = [bool(args.section), bool(args.pages), bool(args.kind), bool(args.grep)]
    if sum(modes) > 1:
        print("error: pick at most one of --section / --pages / --kind / --grep",
              file=sys.stderr)
        return 1

    # --pages / --kind require MinerU
    if (args.pages or args.kind) and converter != "mineru":
        need = "--pages" if args.pages else "--kind"
        print(
            f"error: {need} requires a MinerU-converted paper "
            f"(this paper: {converter}); use --section instead",
            file=sys.stderr,
        )
        return 1

    if args.section:
        return _do_section(args, paper_dir, converter, md, citekey)
    if args.pages:
        content = _load_content_list(paper_dir, converter)
        if content is None:
            print("error: MinerU content_list.json not found on disk", file=sys.stderr)
            return 1
        try:
            wanted = _parse_pages(args.pages)
        except ValueError as e:
            print(f"error: bad --pages spec: {e}", file=sys.stderr)
            return 1
        out = _extract_pages_mineru(content, wanted)
        return _emit_pages(args, out, citekey)
    if args.kind:
        if args.kind not in _KIND_MAP:
            print(
                f"error: --kind must be one of "
                f"{'|'.join(sorted(_KIND_MAP))} (got {args.kind!r})",
                file=sys.stderr,
            )
            return 1
        content = _load_content_list(paper_dir, converter)
        if content is None:
            print("error: MinerU content_list.json not found on disk", file=sys.stderr)
            return 1
        out = _extract_kind_mineru(content, args.kind)
        return _emit_kind(args, out, citekey)
    if args.grep:
        try:
            out = _grep_md(md, args.grep)
        except ValueError as e:
            print(f"error: {e}", file=sys.stderr)
            return 1
        return _emit_grep(args, out, citekey)

    # Default: dump the whole paper.md
    if args.json:
        print(json.dumps({"citekey": citekey, "md_status": paper["md_status"],
                          "converter": converter, "text": md},
                         indent=2, ensure_ascii=False))
    else:
        sys.stdout.write(md)
        if not md.endswith("\n"):
            sys.stdout.write("\n")
    return 0


def _do_section(args, paper_dir: Path, converter: str, md: str, citekey: str) -> int:
    if converter == "mineru":
        content = _load_content_list(paper_dir, converter)
        headings = _headings_from_mineru(content) if content else []
        matched = [h for h in headings if _heading_matches(args.section, h["text"])]
        if content is None:
            # MinerU declared but content_list absent → fall back to markdown headings.
            headings = _headings_from_markdown(md)
            matched = [h for h in headings if _heading_matches(args.section, h["text"])]
            sections = [_extract_section_markdown(md, headings, h) for h in matched]
        else:
            sections = [_extract_section_mineru(content, h) for h in matched]
    else:
        headings = _headings_from_markdown(md)
        matched = [h for h in headings if _heading_matches(args.section, h["text"])]
        sections = [_extract_section_markdown(md, headings, h) for h in matched]

    if args.json:
        print(json.dumps({
            "citekey": citekey,
            "query": args.section,
            "count": len(sections),
            "sections": sections,
        }, indent=2, ensure_ascii=False))
        return 0

    if not sections:
        print(f"(no heading matches for {args.section!r})")
        return 0
    for s in sections:
        p = ""
        if s.get("page_from") is not None:
            if s.get("page_to") is not None and s["page_to"] != s["page_from"]:
                p = f"  (pages {s['page_from']}-{s['page_to']})"
            else:
                p = f"  (page {s['page_from']})"
        print(f"\n## {s['heading']}{p}\n")
        print(s["text"])
    return 0


def _emit_pages(args, out: list[dict], citekey: str) -> int:
    if args.json:
        print(json.dumps({"citekey": citekey, "pages": out},
                         indent=2, ensure_ascii=False))
        return 0
    if not out:
        print("(no matching pages)")
        return 0
    for p in out:
        print(f"\n--- page {p['page']} ---\n")
        print(p["text"])
    return 0


def _emit_kind(args, out: list[dict], citekey: str) -> int:
    if args.json:
        print(json.dumps({"citekey": citekey, "kind": args.kind, "blocks": out},
                         indent=2, ensure_ascii=False))
        return 0
    if not out:
        print(f"(no blocks of kind {args.kind!r})")
        return 0
    for b in out:
        p = f" (page {b['page']})" if b.get("page") else ""
        print(f"\n--- {args.kind}{p} ---\n")
        print(b["text"])
    return 0


def _emit_grep(args, out: list[dict], citekey: str) -> int:
    if args.json:
        print(json.dumps({"citekey": citekey, "matches": out},
                         indent=2, ensure_ascii=False))
        return 0
    if not out:
        print("(no matches)")
        return 0
    for m in out:
        print(f"{m['line']}: {m['text']}")
    return 0


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser("read")
    ap.add_argument("key")
    ap.add_argument("--section", help="Fuzzy match on headings")
    ap.add_argument("--pages", help="Page spec (MinerU only), e.g. 3-5 or 3,7,9-11")
    ap.add_argument("--kind", help="Block kind (MinerU only): table|equation|image_caption|code|text")
    ap.add_argument("--grep", help="Python regex over paper.md")
    ap.add_argument("--json", action="store_true")
    sys.exit(run(ap.parse_args()))
