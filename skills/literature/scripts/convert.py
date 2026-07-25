#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///

"""`sf-lit convert <citekey>` — render a paper's PDF to canonical Markdown.

Second phase of the two-phase ingest workflow. ``add`` catalogues the
paper's metadata + copies its PDF into the library; ``convert`` spawns
MinerU (default) or Docling to produce Markdown, promotes it as the
canonical ``paper.md``, and indexes it into the paper-level FTS store.

Key behaviors:

- **CLI subprocess.** MinerU/Docling live in the user's environment (or a
  venv/conda env / Docker container the user has wired up); this script
  only shells out. Command is resolved via ``[converter]`` config with
  env-var overrides (``LITLIB_MINERU_BIN`` / ``LITLIB_DOCLING_BIN``).
- **Synchronous.** stdout stays machine-readable; converter chatter goes
  to stderr (streamed live). No background workers, no ``pending``
  state.
- **Full-fidelity on disk.** Everything the converter writes lands under
  ``library/papers/<citekey>/converter_output/<converter>/``. The top-level
  ``paper.md`` is a promoted copy of that converter's markdown output.
- **Idempotency fuse.** If ``paper.md`` + ``converter.json`` already
  reflect the current PDF's sha256 + the same converter + the same
  version, a bare ``--reconvert`` exits 0 without doing anything.
  ``--force`` overrides.

Exit codes:
  0  success (or no-op when the fuse tripped)
  1  generic error (PDF missing, converter failed, DB error)
  4  converter binary not found in PATH / not configured
"""

import hashlib
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import config as config_mod  # noqa: E402
import db as dbmod  # noqa: E402


# ---- helpers -----------------------------------------------------------


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _normalize_text(raw: bytes) -> str:
    """UTF-8 decode + strip BOM + CRLF → LF."""
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    text = raw.decode("utf-8", errors="replace")
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _resolve_key(key: str) -> dict | None:
    for field in ("citekey", "arxiv_id", "doi"):
        row = dbmod.fetchone(f"SELECT * FROM papers WHERE {field} = ?", (key,))
        if row:
            return dict(row)
    return None


def _paper_dir(lib: Path, citekey: str) -> Path:
    return lib / "papers" / citekey


def _converter_out_dir(paper_dir: Path, converter: str) -> Path:
    return paper_dir / "converter_output" / converter


def _load_sidecar(paper_dir: Path) -> dict:
    p = paper_dir / "converter.json"
    if not p.is_file():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save_sidecar(paper_dir: Path, data: dict) -> None:
    (paper_dir / "converter.json").write_text(
        json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False),
        encoding="utf-8",
    )


# ---- converter runners -------------------------------------------------


class ConverterError(RuntimeError):
    """Raised when a converter subprocess fails or is misconfigured."""


def _converter_version(argv: list[str]) -> str | None:
    """Best-effort ``--version`` probe. Never fatal."""
    try:
        proc = subprocess.run(
            [*argv, "--version"],
            capture_output=True, text=True, timeout=15, check=False,
        )
        out = (proc.stdout or proc.stderr or "").strip().splitlines()
        return out[0] if out else None
    except (OSError, subprocess.SubprocessError):
        return None


def _run_mineru(pdf_path: Path, out_dir: Path, extra: list[str] | None = None) -> None:
    """Invoke ``mineru -p <pdf> -o <out_dir>``.

    Streams the converter's own stdout/stderr live to our stderr so the
    user sees progress. Raises ConverterError on non-zero exit.
    """
    argv = config_mod.get_converter_command("mineru")
    if not argv:
        raise ConverterError("mineru is not configured (see [converter.mineru] in config)")
    if not _which(argv[0]):
        raise ConverterError(
            f"mineru command not found in PATH: {argv[0]!r}. Install MinerU "
            f"(e.g. `pipx install mineru`) or set LITLIB_MINERU_BIN."
        )
    out_dir.mkdir(parents=True, exist_ok=True)
    cmd = [*argv, "-p", str(pdf_path), "-o", str(out_dir)]
    if extra:
        cmd.extend(extra)
    print(f"[convert] $ {' '.join(cmd)}", file=sys.stderr)
    proc = subprocess.run(cmd, stdout=sys.stderr, stderr=sys.stderr)
    if proc.returncode != 0:
        raise ConverterError(f"mineru exited with status {proc.returncode}")


def _run_docling(pdf_path: Path, out_dir: Path, extra: list[str] | None = None) -> None:
    """Invoke ``docling <pdf> --to md --output <out_dir>``."""
    argv = config_mod.get_converter_command("docling")
    if not argv:
        raise ConverterError("docling is not configured (see [converter.docling] in config)")
    if not _which(argv[0]):
        raise ConverterError(
            f"docling command not found in PATH: {argv[0]!r}. Install Docling "
            f"(e.g. `pipx install docling`) or set LITLIB_DOCLING_BIN."
        )
    out_dir.mkdir(parents=True, exist_ok=True)
    cmd = [*argv, str(pdf_path), "--to", "md", "--output", str(out_dir)]
    if extra:
        cmd.extend(extra)
    print(f"[convert] $ {' '.join(cmd)}", file=sys.stderr)
    proc = subprocess.run(cmd, stdout=sys.stderr, stderr=sys.stderr)
    if proc.returncode != 0:
        raise ConverterError(f"docling exited with status {proc.returncode}")


def _which(cmd: str) -> str | None:
    """Locate a command on PATH. Returns absolute path or None."""
    return shutil.which(cmd)


# ---- output shape resolution -------------------------------------------


def _pick_canonical_md(out_dir: Path, converter: str, pdf_stem: str) -> Path:
    """Locate the .md file the converter wrote inside ``out_dir``.

    MinerU nests output as ``<out_dir>/<pdf_stem>/{auto,ocr,...}/<pdf_stem>.md``
    but the exact backend subdirectory depends on the mode. Docling writes
    a single ``<pdf_stem>.md`` at the top of ``out_dir``.

    Falls back to any ``*.md`` under ``out_dir``, preferring the newest.
    """
    candidates: list[Path] = []
    for md in out_dir.rglob("*.md"):
        candidates.append(md)
    if not candidates:
        raise ConverterError(
            f"{converter} produced no .md files under {out_dir}"
        )
    # Prefer the one whose stem matches the PDF's stem — that's the main
    # output rather than an intermediate.
    named = [p for p in candidates if p.stem == pdf_stem]
    if named:
        return max(named, key=lambda p: p.stat().st_mtime)
    return max(candidates, key=lambda p: p.stat().st_mtime)


# ---- persistence -------------------------------------------------------


def _ingest_md(citekey: str, md_path: Path, converter: str, version: str | None,
               pdf_sha: str) -> int:
    """Write ``papers_md`` row + update ``papers.md_status='ready'``.

    Returns the ingested char count.
    """
    raw = md_path.read_bytes()
    text = _normalize_text(raw)
    now = _utcnow_iso()
    with dbmod.Atomic():
        dbmod.execute("DELETE FROM papers_md WHERE citekey = ?", (citekey,))
        dbmod.execute(
            """
            INSERT INTO papers_md (citekey, markdown, converter, converter_version,
                                    converted_at, pdf_sha256, char_count)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (citekey, text, converter, version, now, pdf_sha, len(text)),
        )
        dbmod.execute(
            """
            UPDATE papers SET
              md_status = 'ready',
              md_last_error = NULL,
              updated_at = datetime('now')
            WHERE citekey = ?
            """,
            (citekey,),
        )
    return len(text)


def _mark_failed(citekey: str, message: str) -> None:
    with dbmod.Atomic():
        dbmod.execute(
            "UPDATE papers SET md_status = 'failed', md_last_error = ?, "
            "updated_at = datetime('now') WHERE citekey = ?",
            (message[:2000], citekey),
        )


# ---- main flow ---------------------------------------------------------


def _fuse_check(paper_dir: Path, converter: str, version: str | None,
                pdf_sha: str) -> bool:
    """Return True if paper.md + sidecar already reflect the current inputs."""
    top_md = paper_dir / "paper.md"
    sidecar = _load_sidecar(paper_dir)
    if not top_md.is_file() or top_md.stat().st_size == 0:
        return False
    if sidecar.get("converter") != converter:
        return False
    if sidecar.get("pdf_sha256") != pdf_sha:
        return False
    # version match is optional — if either side is None, skip the check
    sv = sidecar.get("converter_version")
    if version and sv and version != sv:
        return False
    return True


def _promote_canonical(source_md: Path, paper_dir: Path) -> Path:
    """Copy the converter's .md to ``paper.md`` at the paper root."""
    dest = paper_dir / "paper.md"
    shutil.copyfile(source_md, dest)
    return dest


def _copy_converted_dir(src: Path, dest: Path) -> None:
    """Copy an already-produced converter output tree into place."""
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(src, dest)


def run(args) -> int:
    cfg = config_mod.load_config()
    lib = Path(cfg["_library_path"])
    if not (lib / "index.db").exists():
        print("error: no library yet — run `sf-lit init`", file=sys.stderr)
        return 3
    dbmod.connect(lib / "index.db")
    try:
        return _run(args, lib)
    finally:
        dbmod.close()


def _run(args, lib: Path) -> int:
    paper = _resolve_key(args.key)
    if paper is None:
        print(f"error: no paper matches {args.key!r}", file=sys.stderr)
        return 3
    citekey = paper["citekey"]
    paper_dir = _paper_dir(lib, citekey)
    if not paper.get("pdf_path"):
        print(f"error: {citekey} has no PDF; convert requires paper.pdf",
              file=sys.stderr)
        return 3
    pdf_path = lib / paper["pdf_path"]
    if not pdf_path.is_file():
        print(f"error: paper.pdf missing on disk: {pdf_path}", file=sys.stderr)
        return 3

    converter = args.converter or config_mod.default_converter()
    if converter not in ("mineru", "docling"):
        print(f"error: unknown converter {converter!r} (mineru | docling)",
              file=sys.stderr)
        return 2

    already_ready = paper.get("md_status") == "ready"
    if already_ready and not args.reconvert:
        print(
            f"error: {citekey} already has md_status=ready. "
            f"Pass --reconvert to re-run (adds --force to bypass sha256 fuse).",
            file=sys.stderr,
        )
        return 2

    pdf_sha = _sha256(pdf_path)
    argv_probe = config_mod.get_converter_command(converter) or [converter]
    version = _converter_version(argv_probe)

    # Fuse: same PDF, same converter, same version already promoted → no-op.
    if args.reconvert and not args.force:
        if _fuse_check(paper_dir, converter, version, pdf_sha):
            print(f"citekey={citekey}")
            print(f"converter={converter}")
            print(f"md_status=ready")
            print("action=noop")
            print("reason=inputs unchanged (paper.md + converter.json match); "
                  "pass --force to re-run anyway")
            return 0

    out_dir = _converter_out_dir(paper_dir, converter)

    # --converted-dir escape hatch: user brought their own converter output.
    started = _utcnow_iso()
    try:
        if args.converted_dir:
            src = Path(args.converted_dir).expanduser().resolve()
            if not src.is_dir():
                raise ConverterError(f"--converted-dir not a directory: {src}")
            _copy_converted_dir(src, out_dir)
        else:
            # Fresh conversion into a clean out_dir.
            if out_dir.exists():
                shutil.rmtree(out_dir)
            out_dir.mkdir(parents=True, exist_ok=True)
            if converter == "mineru":
                _run_mineru(pdf_path, out_dir)
            else:
                _run_docling(pdf_path, out_dir)

        # Locate canonical .md within the fresh (or copied) output.
        md_src = _pick_canonical_md(out_dir, converter, pdf_path.stem)
    except ConverterError as e:
        _mark_failed(citekey, str(e))
        print(f"error: {e}", file=sys.stderr)
        # ADR-0006: `4` is reserved for destructive-refused. Converter
        # binary missing or misconfigured is a system/runtime problem → `1`.
        return 1
    except Exception as e:  # noqa: BLE001
        _mark_failed(citekey, f"{type(e).__name__}: {e}")
        print(f"error: {type(e).__name__}: {e}", file=sys.stderr)
        return 1

    try:
        top_md = _promote_canonical(md_src, paper_dir)
        _save_sidecar(paper_dir, {
            "converter": converter,
            "converter_version": version,
            "converted_at": started,
            "pdf_sha256": pdf_sha,
            "source_md": str(md_src.relative_to(paper_dir)),
        })
        chars = _ingest_md(citekey, top_md, converter, version, pdf_sha)
    except (OSError, Exception) as e:  # noqa: BLE001
        _mark_failed(citekey, f"post-convert: {type(e).__name__}: {e}")
        print(f"error: post-convert step failed: {e}", file=sys.stderr)
        return 1

    print(f"citekey={citekey}")
    print(f"converter={converter}")
    if version:
        print(f"converter_version={version}")
    print(f"md={top_md}")
    print(f"chars={chars}")
    print(f"md_status=ready")
    return 0


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser("convert")
    ap.add_argument("key", help="citekey | arxiv_id | doi")
    ap.add_argument(
        "--converter", choices=["mineru", "docling"], default=None,
        help="Converter to use (default: [converter].default)",
    )
    ap.add_argument(
        "--reconvert", action="store_true",
        help="Re-run even if md_status is already 'ready'",
    )
    ap.add_argument(
        "--force", action="store_true",
        help="With --reconvert, bypass the sha256+converter+version fuse",
    )
    ap.add_argument(
        "--converted-dir", dest="converted_dir", default=None,
        help="Skip the converter subprocess; copy an existing output tree instead",
    )
    sys.exit(run(ap.parse_args()))
