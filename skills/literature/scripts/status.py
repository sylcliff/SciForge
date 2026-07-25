#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///

"""`sf-lit status` / `sf-lit list --md-status` — MD conversion state.

The DB's ``papers.md_status`` is the source of truth, but the DB doesn't
know if the user hand-deleted ``paper.md`` or replaced the PDF outside
the CLI. Each ``status`` call therefore re-validates the on-disk state:

- If ``md_status='ready'`` but ``paper.md`` is missing/empty → downgrade
  to ``stale``.
- If ``paper.pdf`` sha256 no longer matches ``converter.json.pdf_sha256``
  → downgrade to ``stale``.

Any downgrade is persisted so subsequent ``search`` / ``show`` see the
same truth. This is a read command with a small side-effect (the fix-up
write); we consider that acceptable because it converges the DB toward
reality rather than away from it.
"""

import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import config as config_mod  # noqa: E402
import db as dbmod  # noqa: E402

VALID_STATUSES = ("absent", "ready", "failed", "stale")


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _resolve_key(key: str) -> dict | None:
    for field in ("citekey", "arxiv_id", "doi"):
        row = dbmod.fetchone(f"SELECT * FROM papers WHERE {field} = ?", (key,))
        if row:
            return dict(row)
    return None


def _paper_dir(lib: Path, citekey: str) -> Path:
    return lib / "papers" / citekey


def _load_sidecar(paper_dir: Path) -> dict:
    p = paper_dir / "converter.json"
    if not p.is_file():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _revalidate(paper: dict, lib: Path) -> tuple[str, dict]:
    """Return (effective_status, sidecar_dict).

    Persists the update if the effective status differs from the DB.
    Never upgrades — only downgrades ``ready`` → ``stale`` when the disk
    disagrees. ``absent`` / ``failed`` are respected as-is.
    """
    citekey = paper["citekey"]
    status = paper["md_status"]
    paper_dir = _paper_dir(lib, citekey)
    sidecar = _load_sidecar(paper_dir)

    if status != "ready":
        return status, sidecar

    top = paper_dir / "paper.md"
    if not top.is_file() or top.stat().st_size == 0:
        _persist_status(citekey, "stale", "paper.md missing on disk")
        return "stale", sidecar

    pdf_rel = paper.get("pdf_path")
    if pdf_rel:
        pdf = lib / pdf_rel
        if pdf.is_file():
            recorded = sidecar.get("pdf_sha256")
            if recorded:
                try:
                    actual = _sha256(pdf)
                except OSError:
                    actual = None
                if actual and actual != recorded:
                    _persist_status(citekey, "stale",
                                    "paper.pdf sha256 diverged from converter.json")
                    return "stale", sidecar
    return "ready", sidecar


def _persist_status(citekey: str, new_status: str, note: str | None) -> None:
    with dbmod.Atomic():
        if new_status == "stale":
            dbmod.execute(
                "UPDATE papers SET md_status = 'stale', md_last_error = ?, "
                "updated_at = datetime('now') WHERE citekey = ?",
                (note, citekey),
            )
        else:
            dbmod.execute(
                "UPDATE papers SET md_status = ?, updated_at = datetime('now') "
                "WHERE citekey = ?",
                (new_status, citekey),
            )


# ---- status (single paper) --------------------------------------------


def cmd_status(args) -> int:
    cfg = config_mod.load_config()
    lib = Path(cfg["_library_path"])
    if not (lib / "index.db").exists():
        print("error: no library yet — run `sf-lit init`", file=sys.stderr)
        return 3
    dbmod.connect(lib / "index.db")
    try:
        paper = _resolve_key(args.key)
        if paper is None:
            print(f"error: no paper matches {args.key!r}", file=sys.stderr)
            return 3
        effective, sidecar = _revalidate(paper, lib)

        record = {
            "citekey": paper["citekey"],
            "md_status": effective,
            "converter": sidecar.get("converter"),
            "converter_version": sidecar.get("converter_version"),
            "converted_at": sidecar.get("converted_at"),
            "pdf_sha256": sidecar.get("pdf_sha256"),
        }
        # Pull char count from DB if we have an md row.
        row = dbmod.fetchone(
            "SELECT char_count FROM papers_md WHERE citekey = ?", (paper["citekey"],)
        )
        if row:
            record["char_count"] = row["char_count"]
        if effective == "failed" or effective == "stale":
            record["last_error"] = paper.get("md_last_error")

        if args.json:
            print(json.dumps(record, indent=2, ensure_ascii=False))
        else:
            for k, v in record.items():
                if v is not None:
                    print(f"{k}={v}")
        return 0
    finally:
        dbmod.close()


# ---- list (batch) ------------------------------------------------------


def cmd_list(args) -> int:
    cfg = config_mod.load_config()
    lib = Path(cfg["_library_path"])
    if not (lib / "index.db").exists():
        print("error: no library yet — run `sf-lit init`", file=sys.stderr)
        return 3
    dbmod.connect(lib / "index.db")
    try:
        where = []
        params: list = []
        if args.md_status:
            if args.md_status not in VALID_STATUSES:
                print(f"error: --md-status must be one of {'|'.join(VALID_STATUSES)}",
                      file=sys.stderr)
                return 2
            where.append("md_status = ?")
            params.append(args.md_status)
        sql = ("SELECT citekey, title, year, md_status, md_last_error, pdf_path "
               "FROM papers")
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY citekey ASC"
        rows = dbmod.fetchall(sql, tuple(params))

        # Optional lightweight revalidation for ``ready`` rows — cheap on
        # small libraries, and useful when the caller is asking "what's
        # stale?" That said, don't force sha256 on every row every time
        # ``list`` is called with no filter; only revalidate when the
        # user explicitly asked for a status.
        if args.md_status:
            fresh_rows = []
            for r in rows:
                paper = dict(r)
                new_status, _ = _revalidate(paper, lib)
                if new_status == args.md_status:
                    paper["md_status"] = new_status
                    fresh_rows.append(paper)
            rows = fresh_rows

        if args.json:
            print(json.dumps(
                [{"citekey": r["citekey"], "title": r["title"], "year": r["year"],
                  "md_status": r["md_status"], "has_pdf": bool(r["pdf_path"]),
                  "last_error": r["md_last_error"]}
                 for r in rows],
                indent=2, ensure_ascii=False,
            ))
        else:
            if not rows:
                print("(no matches)")
                return 0
            key_w = max(len(r["citekey"]) for r in rows) if rows else 20
            for r in rows:
                title = (r["title"] or "").strip()
                if len(title) > 60:
                    title = title[:57] + "..."
                print(f"{r['citekey']:<{key_w}}  {r['md_status']:<7}  {title}")
        return 0
    finally:
        dbmod.close()


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser("status")
    sub = ap.add_subparsers(dest="mode", required=True)
    sp_st = sub.add_parser("status")
    sp_st.add_argument("key")
    sp_st.add_argument("--json", action="store_true")
    sp_st.set_defaults(func=cmd_status)
    sp_ls = sub.add_parser("list")
    sp_ls.add_argument("--md-status", dest="md_status", default=None)
    sp_ls.add_argument("--json", action="store_true")
    sp_ls.set_defaults(func=cmd_list)
    a = ap.parse_args()
    sys.exit(a.func(a))
