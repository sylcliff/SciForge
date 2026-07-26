"""Remote-fallback bridge — invoke `skills/remote-paper` when a paper is paywalled.

This module is called by `fetch.py` when the OA fallback chain has concluded
that the paper is paywalled and the user opted-in with `--fallback-remote <name>`.

Contract:
  - `--fallback-remote` value maps to a backend name via `_DISPATCH`.
  - We `subprocess.run` the remote-paper skill's `fetch.sh --backend <name> <id>`.
  - Success = last stdout line matches `PDF_PATH=<absolute path>` **and** that
    file exists locally after the call.
  - Any other outcome is a soft failure — caller keeps the original `paywalled`
    status (or downgrades to a new `remote_failed` value if we ever add one).

Kept intentionally small: no async, no networking. `fetch.py` is already inside
an asyncio task, so we hand this off to a thread via `asyncio.to_thread`.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


# --------------------------------------------------------------------------- #
# Dispatch table — flag value → (skill dir under skills/, backend name)
# --------------------------------------------------------------------------- #

_DISPATCH: dict[str, dict[str, str]] = {
    "zj": {"skill": "remote-paper", "backend": "zj"},
}


# --------------------------------------------------------------------------- #
# Path resolution — anchor on the SciForge repo root, not absolute paths.
# --------------------------------------------------------------------------- #


def _sciforge_root() -> Path:
    """Locate the project root by walking up from this file.

    This module lives at `skills/download/scripts/remote_fallback.py`,
    so the root is three parents up.
    """
    return Path(__file__).resolve().parents[3]


def _fetch_script_for(backend_flag: str) -> Optional[Path]:
    entry = _DISPATCH.get(backend_flag)
    if entry is None:
        return None
    return _sciforge_root() / "skills" / entry["skill"] / "scripts" / "fetch.sh"


def known_backend(flag_value: str) -> bool:
    return flag_value in _DISPATCH


def list_backends() -> list[str]:
    return sorted(_DISPATCH.keys())


# --------------------------------------------------------------------------- #
# Result
# --------------------------------------------------------------------------- #


@dataclass
class RemoteFetchResult:
    ok: bool
    pdf_path: Optional[str] = None
    bytes: Optional[int] = None
    reason: Optional[str] = None  # e.g. "no_pdf_path_line", "backend_exit_1", "file_missing"


# --------------------------------------------------------------------------- #
# Public entry
# --------------------------------------------------------------------------- #


def fetch_via_remote(identifier: str, backend_flag: str, timeout_seconds: int = 900) -> RemoteFetchResult:
    """Synchronously call `remote-paper` for one identifier.

    `backend_flag` is the user-supplied value of `--fallback-remote`, which
    is also the key into `_DISPATCH`.

    Returns a `RemoteFetchResult`; never raises for well-defined failures
    (missing PDF_PATH=, non-zero exit, file not written). Exception only
    on invalid backend or genuinely unexpected subprocess failure.
    """
    if not known_backend(backend_flag):
        return RemoteFetchResult(
            ok=False, reason=f"unknown_backend:{backend_flag}"
        )

    script = _fetch_script_for(backend_flag)
    if script is None or not script.is_file():
        return RemoteFetchResult(
            ok=False, reason=f"script_missing:{script}"
        )

    backend_name = _DISPATCH[backend_flag]["backend"]

    # `bash` is the intended interpreter; on Windows we rely on Git Bash being
    # available (the whole zj workflow already assumes an ssh client, so a
    # POSIX shell is a fair prerequisite).
    #
    # Bash lookup: on Windows, `subprocess.run(['bash', ...])` picks up
    # `C:\Windows\System32\bash.exe` (WSL) first, which lives in a Linux
    # filesystem and cannot see `D:\...`. `shutil.which("bash")` gives us
    # Git Bash instead. Fall back to plain "bash" only if `which` finds none.
    bash_bin = _find_bash()
    # Path form matters: on Windows, `str(Path)` returns backslashes, and passing
    # a bare `D:\...\fetch.sh` to bash makes bash treat the backslashes as
    # escapes, mangling the path. Feed bash a POSIX-form path (`/d/.../fetch.sh`)
    # so it survives verbatim.
    cmd = [bash_bin, _to_bash_path(script), "--backend", backend_name, identifier]

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            env=os.environ.copy(),
        )
    except FileNotFoundError:
        return RemoteFetchResult(ok=False, reason="bash_not_found")
    except subprocess.TimeoutExpired:
        return RemoteFetchResult(ok=False, reason="timeout")

    stdout = proc.stdout or ""
    stderr = proc.stderr or ""

    # Parse `PDF_PATH=<abspath>` — must be the *last* such line for this run.
    # Multiple identifiers in one call is possible in principle (fetch.sh
    # supports batches), but we only pass one here, so grabbing the last
    # occurrence is safe.
    pdf_path: Optional[str] = None
    for line in reversed(stdout.splitlines()):
        line = line.strip()
        if line.startswith("PDF_PATH="):
            pdf_path = line[len("PDF_PATH="):].strip()
            break

    if pdf_path is None:
        reason = f"no_pdf_path_line:exit={proc.returncode}"
        # Attach a compact tail of stderr for debugging without flooding NDJSON.
        tail = _tail_for_debug(stderr or stdout)
        if tail:
            reason = f"{reason}:{tail}"
        return RemoteFetchResult(ok=False, reason=reason)

    # Resolve — accept both Windows-form (D:\...) and POSIX-form (/d/...).
    resolved = _resolve_local_path(pdf_path)
    if resolved is None or not resolved.is_file():
        return RemoteFetchResult(
            ok=False,
            reason=f"file_missing:{pdf_path}",
        )

    try:
        size = resolved.stat().st_size
    except OSError:
        size = None

    return RemoteFetchResult(
        ok=True,
        pdf_path=str(resolved),
        bytes=size,
    )


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _resolve_local_path(raw: str) -> Optional[Path]:
    """Best-effort conversion of a path emitted by a shell script into a Path.

    Handles both native Windows (`D:\\...`) and POSIX-ish Git-Bash forms
    (`/d/...` or `/c/...`). Returns None if we cannot form any plausible path.
    """
    if not raw:
        return None
    # Direct case first: Path handles both forms on most platforms.
    p = Path(raw)
    if p.is_file():
        return p

    # /d/foo/bar.pdf → D:\foo\bar.pdf (Windows only)
    if os.name == "nt" and len(raw) >= 3 and raw[0] == "/" and raw[2] == "/" and raw[1].isalpha():
        drive = raw[1].upper()
        rest = raw[3:].replace("/", "\\")
        winish = Path(f"{drive}:\\{rest}")
        if winish.is_file():
            return winish

    # D:\... on non-Windows or vice-versa — return the raw form; caller will
    # check `is_file()` again and fail with `file_missing` if it isn't real.
    return p


def _to_bash_path(p: Path) -> str:
    """Convert a Path to a form bash can read verbatim.

    On Windows, Path.__str__ uses backslashes and bash treats those as escapes
    (`D:\\code\\...` → `D:codeSciForge...`). Rewrite as `/d/code/...`.
    Everywhere else, `str(p)` is already fine.
    """
    if os.name != "nt":
        return str(p)
    s = str(p.resolve())
    if len(s) >= 3 and s[1] == ":" and s[0].isalpha():
        drive = s[0].lower()
        rest = s[2:].replace("\\", "/")
        # Ensure it starts with a slash after the drive letter.
        if not rest.startswith("/"):
            rest = "/" + rest
        return f"/{drive}{rest}"
    return s.replace("\\", "/")


def _find_bash() -> str:
    """Locate the bash executable to use for invoking remote-paper scripts.

    On Windows, plain `bash` in PATH resolves to WSL bash which cannot see
    `D:\\...` paths. Prefer Git Bash — `shutil.which("bash")` picks that
    up when it's on PATH, and we fall back to well-known Git-for-Windows
    install locations if the env is unusual.
    """
    if os.name != "nt":
        return "bash"
    which_hit = shutil.which("bash")
    # Avoid System32\bash.exe (WSL) — it starts with 'C:\Windows\System32'.
    if which_hit and "system32" not in which_hit.lower():
        return which_hit
    for candidate in (
        r"C:\Program Files\Git\usr\bin\bash.exe",
        r"C:\Program Files\Git\bin\bash.exe",
        r"C:\Program Files (x86)\Git\usr\bin\bash.exe",
    ):
        if Path(candidate).is_file():
            return candidate
    return which_hit or "bash"


def _tail_for_debug(s: str, max_chars: int = 200) -> str:
    """Compact end of a log for embedding in a reason string."""
    if not s:
        return ""
    s = s.strip().replace("\n", " | ")
    if len(s) > max_chars:
        s = "…" + s[-max_chars:]
    return s


__all__ = [
    "RemoteFetchResult",
    "fetch_via_remote",
    "known_backend",
    "list_backends",
]
