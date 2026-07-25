"""sf-download CLI dispatch.

Verbs / modes:
  sf-download doctor
  sf-download <identifier>                   [--emit-json --out DIR]
  sf-download --ids "id1,id2,..."            [--emit-json --out DIR]
  sf-download --from-file ids.txt            [--emit-json --out DIR]
  sf-download --title "..."                  [--emit-json --out DIR]

Exit codes (per SKILL.md / ADR-0006):
  0 — normal completion (batch always 0)
  2 — invalid_input at startup
  3 — single-paper identifier_not_found
  1/64+ — internal error
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path
from typing import Iterable

import httpx

# `scripts/` is added to sys.path by the `sf-download` shebang wrapper.
# Import flat modules.
from config import load_config
from fetch import fetch_one
from output import PaperResult, Status, Summary
import doctor as doctor_mod


# --------------------------------------------------------------------------- #
# argparse
# --------------------------------------------------------------------------- #


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="sf-download",
        description="API-first paper fetcher — 5 public sources, NDJSON output.",
    )
    p.add_argument(
        "identifier",
        nargs="?",
        help="DOI / arXiv-id / OpenAlex ID / S2 hash / URL. Omit if using --ids/--from-file/--title/doctor.",
    )
    p.add_argument(
        "--ids",
        metavar="A,B,C",
        help="Comma-separated identifiers for a batch run.",
    )
    p.add_argument(
        "--from-file",
        metavar="PATH",
        help="Path to a file with one identifier per line (blank & # lines ignored).",
    )
    p.add_argument(
        "--title",
        metavar="TITLE",
        help="Exact title for the OpenAlex title-fallback path.",
    )
    p.add_argument(
        "--out",
        metavar="DIR",
        help="Override output directory (default: ~/.sciforge/inbox/, or $SCIFORGE_DOWNLOAD_DIR).",
    )
    p.add_argument(
        "--emit-json",
        action="store_true",
        help="Emit machine-readable NDJSON (default: still one JSON line per paper — kept for parity with fetch skills).",
    )
    p.add_argument(
        "--doctor",
        action="store_true",
        help=argparse.SUPPRESS,  # `sf-download doctor` is preferred syntax
    )
    return p


def _is_doctor(argv: list[str]) -> bool:
    """Accept `sf-download doctor` as a first-class subcommand."""
    return len(argv) >= 1 and argv[0] == "doctor"


# --------------------------------------------------------------------------- #
# Input loading
# --------------------------------------------------------------------------- #


def _read_from_file(path: str) -> list[str]:
    p = Path(path).expanduser()
    if not p.is_file():
        raise FileNotFoundError(f"--from-file: {path} is not a file")
    ids: list[str] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        ids.append(s)
    return ids


def _resolve_inputs(args: argparse.Namespace) -> tuple[list[str], bool]:
    """Return (identifiers, is_batch). Raises ValueError on conflicting/empty input."""
    provided = [bool(args.identifier), bool(args.ids), bool(args.from_file), bool(args.title)]
    if sum(provided) == 0:
        raise ValueError("no input: pass a positional identifier, --ids, --from-file, or --title")
    if sum(provided) > 1:
        raise ValueError("conflicting inputs: pass exactly one of positional / --ids / --from-file / --title")

    if args.identifier:
        return [args.identifier], False
    if args.ids:
        parts = [p.strip() for p in args.ids.split(",") if p.strip()]
        if not parts:
            raise ValueError("--ids was empty after splitting")
        return parts, True
    if args.from_file:
        parts = _read_from_file(args.from_file)
        if not parts:
            raise ValueError(f"--from-file {args.from_file} had no identifiers")
        return parts, True
    if args.title:
        return [args.title], False
    raise ValueError("unreachable")  # for type-checkers


# --------------------------------------------------------------------------- #
# Async runner
# --------------------------------------------------------------------------- #


async def _run_all(
    identifiers: list[str],
    treat_as_title: bool,
    out_dir: Path,
    cfg,
) -> tuple[list[PaperResult], list[str]]:
    """Kick off all identifiers with a shared client + concurrency limit.

    Returns (per-paper results in *completion* order, warnings).
    """
    sem = asyncio.Semaphore(max(1, int(cfg.max_concurrency)))
    warnings: list[str] = []
    completed: list[PaperResult] = []
    saw_s2_429 = False

    timeout = httpx.Timeout(cfg.http_timeout_seconds)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:

        async def one(raw: str, idx: int) -> PaperResult:
            nonlocal saw_s2_429
            async with sem:
                result = await fetch_one(raw, idx, cfg, out_dir, client, treat_as_title=treat_as_title)
            # Flush this line as soon as it's ready.
            _print_json_line(result.to_ndjson())
            if result.status == Status.RATE_LIMITED and "semanticscholar" in (result.sources_queried or []):
                saw_s2_429 = True
            return result

        tasks = [asyncio.create_task(one(raw, i)) for i, raw in enumerate(identifiers)]
        for task in asyncio.as_completed(tasks):
            r = await task
            completed.append(r)

    if saw_s2_429 and not cfg.semanticscholar_api_key:
        warnings.append("s2_no_key_seen_429")
    return completed, warnings


# --------------------------------------------------------------------------- #
# Output helpers
# --------------------------------------------------------------------------- #


def _print_json_line(line: str) -> None:
    print(line, flush=True)


def _tally(results: Iterable[PaperResult]) -> Summary:
    s = Summary()
    for r in results:
        s.total += 1
        # match on Status.value string via getattr
        counter = r.status.value
        if hasattr(s, counter):
            setattr(s, counter, getattr(s, counter) + 1)
    return s


# --------------------------------------------------------------------------- #
# Exit-code mapping (single-paper mode)
# --------------------------------------------------------------------------- #


def _single_exit_code(r: PaperResult) -> int:
    """Per SKILL.md §Exit codes."""
    s = r.status
    if s == Status.IDENTIFIER_NOT_FOUND:
        return 3
    if s == Status.INVALID_INPUT:
        return 2
    # downloaded / metadata_only / paywalled / pdf_link_broken / title_ambiguous / rate_limited / network_error → 0
    return 0


# --------------------------------------------------------------------------- #
# Entry
# --------------------------------------------------------------------------- #


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:]) if argv is None else list(argv)

    if _is_doctor(argv):
        return doctor_mod.run()

    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.doctor:
        return doctor_mod.run()

    try:
        identifiers, is_batch = _resolve_inputs(args)
    except ValueError as exc:
        print(f"sf-download: {exc}", file=sys.stderr)
        return 2
    except FileNotFoundError as exc:
        print(f"sf-download: {exc}", file=sys.stderr)
        return 2

    cfg = load_config()
    out_dir = Path(args.out).expanduser().resolve() if args.out else cfg.resolved_download_dir
    treat_as_title = bool(args.title)

    t0 = time.monotonic()
    try:
        results, warnings = asyncio.run(_run_all(identifiers, treat_as_title, out_dir, cfg))
    except KeyboardInterrupt:
        print("sf-download: interrupted", file=sys.stderr)
        return 130

    if is_batch:
        summary = _tally(results)
        summary.elapsed_seconds = round(time.monotonic() - t0, 2)
        summary.warnings = warnings
        _print_json_line(summary.to_ndjson())
        return 0

    # Single-paper mode — one line already printed; exit code follows status.
    if not results:
        return 1
    return _single_exit_code(results[0])


if __name__ == "__main__":
    sys.exit(main())
