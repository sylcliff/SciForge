#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///

"""`sf-lit open <key> [target]`

Cross-platform opener that hands a path or URL off to:
  - `wslview` on WSL (detected via /proc/version)
  - `xdg-open` on other Linux
  - `open` on macOS
  - `explorer` on Windows

If no opener is available (or `target=url`), we just print the target
so the caller can decide what to do.

Targets:
  pdf     — the main paper.pdf
  md      — the canonical paper.md (produced by `sf-lit convert`)
  notes   — notes.md
  si      — SI directory (or si:N for the Nth SI file)
  github  — first associated repo URL
  url     — the paper's canonical URL
"""

import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import config as config_mod  # noqa: E402
import db as dbmod  # noqa: E402


def _resolve_key(key: str) -> dict | None:
    for field in ("citekey", "arxiv_id", "doi"):
        row = dbmod.fetchone(f"SELECT * FROM papers WHERE {field} = ?", (key,))
        if row:
            return dict(row)
    return None


def _detect_opener() -> str | None:
    """Return the first available opener command."""
    if platform.system() == "Darwin":
        return "open"
    if platform.system() == "Windows":
        return "explorer"
    # Linux — check for WSL
    try:
        with open("/proc/version") as f:
            if "microsoft" in f.read().lower():
                if shutil.which("wslview"):
                    return "wslview"
                if shutil.which("explorer.exe"):
                    return "explorer.exe"
    except OSError:
        pass
    if shutil.which("xdg-open"):
        return "xdg-open"
    return None


def _open_target(target: str) -> int:
    opener = _detect_opener()
    if opener is None:
        # Just print so the caller can pipe it
        print(target)
        return 0
    try:
        subprocess.Popen(
            [opener, target],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        print(f"opened: {target}")
        return 0
    except FileNotFoundError:
        print(target)
        return 0


def run(args) -> int:
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

        target = args.target or "pdf"

        if target == "pdf":
            if not paper.get("pdf_path"):
                print(f"error: {paper['citekey']} has no PDF", file=sys.stderr)
                return 3
            return _open_target(str(lib / paper["pdf_path"]))

        if target == "md":
            md_path = lib / "papers" / paper["citekey"] / "paper.md"
            if not md_path.is_file():
                print(
                    f"error: {paper['citekey']} has no paper.md — "
                    f"run `sf-lit convert {paper['citekey']}`",
                    file=sys.stderr,
                )
                return 3
            return _open_target(str(md_path))

        if target == "notes":
            if not paper.get("notes_path"):
                print(f"error: {paper['citekey']} has no notes", file=sys.stderr)
                return 3
            return _open_target(str(lib / paper["notes_path"]))

        if target == "url":
            url = paper.get("url")
            if not url:
                print(f"error: {paper['citekey']} has no url", file=sys.stderr)
                return 3
            return _open_target(url)

        if target == "github":
            row = dbmod.fetchone(
                "SELECT url FROM github_projects WHERE citekey = ? LIMIT 1",
                (paper["citekey"],),
            )
            if not row:
                print(f"error: {paper['citekey']} has no GitHub project", file=sys.stderr)
                return 3
            return _open_target(row["url"])

        if target == "si" or target.startswith("si:"):
            rows = dbmod.fetchall(
                "SELECT path, source_url FROM si_files WHERE citekey = ? ORDER BY id",
                (paper["citekey"],),
            )
            if not rows:
                print(f"error: {paper['citekey']} has no SI", file=sys.stderr)
                return 3
            if target == "si":
                # Open the SI directory
                si_dir = lib / "papers" / paper["citekey"] / "si"
                return _open_target(str(si_dir))
            # si:N — the Nth entry (1-indexed)
            try:
                idx = int(target.split(":", 1)[1]) - 1
            except (ValueError, IndexError):
                print(f"error: bad target {target!r}; use si:1, si:2, ...", file=sys.stderr)
                return 2
            if idx < 0 or idx >= len(rows):
                print(f"error: SI index {idx + 1} out of range (have {len(rows)})", file=sys.stderr)
                return 3
            row = rows[idx]
            if row["path"]:
                return _open_target(str(lib / row["path"]))
            if row["source_url"]:
                return _open_target(row["source_url"])
            print(f"error: SI entry has neither path nor url", file=sys.stderr)
            return 3

        print(f"error: unknown target {target!r}", file=sys.stderr)
        return 2
    finally:
        dbmod.close()


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser("open")
    ap.add_argument("key")
    ap.add_argument("target", nargs="?", default="pdf")
    sys.exit(run(ap.parse_args()))