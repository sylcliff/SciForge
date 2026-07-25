"""PDF download + verification + filename builder.

References/status-codes.md defines what counts as a successful PDF:
  - HTTP 2xx from the URL
  - Non-zero size
  - First 4 bytes are `%PDF`
  - When Content-Type is present, it is `application/pdf`

Anything else → the attempt is recorded as a failed pdf attempt with
one of the canonical `reason` values.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import httpx

_PDF_MAGIC = b"%PDF"


@dataclass
class PdfAttemptOutcome:
    """The outcome of trying one PDF URL."""

    ok: bool
    reason: Optional[str] = None  # only when ok=False; one of the canonical slugs
    bytes: Optional[int] = None
    path: Optional[Path] = None


async def try_download(
    url: str,
    dest: Path,
    client: httpx.AsyncClient,
) -> PdfAttemptOutcome:
    """Try to download `url` into `dest`. Only overwrites `dest` on success.

    dest.parent must already exist (caller sets download_dir up).
    """
    tmp = dest.with_suffix(dest.suffix + ".part")
    try:
        async with client.stream("GET", url, follow_redirects=True) as r:
            if r.status_code == 200:
                # Optional content-type check — some CDNs omit it; only
                # fail when it is explicitly wrong.
                ct = r.headers.get("content-type", "").split(";")[0].strip().lower()
                if ct and ct != "application/pdf" and not _accepts_pdf_ish(ct):
                    return PdfAttemptOutcome(False, reason="not_pdf_content_type")
                total = 0
                magic_ok = None
                with open(tmp, "wb") as f:
                    async for chunk in r.aiter_bytes(chunk_size=64 * 1024):
                        if magic_ok is None:
                            # Check first bytes as they arrive
                            magic_ok = chunk.startswith(_PDF_MAGIC)
                            if not magic_ok:
                                # Drain but return failure
                                f.write(chunk)
                                break
                        f.write(chunk)
                        total += len(chunk)
                    else:
                        pass  # normal loop exit
                if magic_ok is False:
                    _safe_unlink(tmp)
                    return PdfAttemptOutcome(False, reason="not_pdf_magic")
                if total == 0:
                    _safe_unlink(tmp)
                    return PdfAttemptOutcome(False, reason="zero_bytes")
                # Atomic rename
                tmp.replace(dest)
                return PdfAttemptOutcome(True, bytes=total, path=dest)
            elif r.status_code == 429:
                return PdfAttemptOutcome(False, reason=f"http_{r.status_code}")
            elif r.status_code >= 400:
                return PdfAttemptOutcome(False, reason=f"http_{r.status_code}")
            else:
                return PdfAttemptOutcome(False, reason=f"http_{r.status_code}")
    except httpx.TimeoutException:
        _safe_unlink(tmp)
        return PdfAttemptOutcome(False, reason="timeout")
    except httpx.RequestError:
        _safe_unlink(tmp)
        return PdfAttemptOutcome(False, reason="verify_error")
    except OSError:
        _safe_unlink(tmp)
        return PdfAttemptOutcome(False, reason="verify_error")


def cached_ok(dest: Path) -> Optional[int]:
    """If dest already exists and looks like a valid PDF, return its size.

    Used to short-circuit re-download when the same identifier is
    fetched twice. Callers set source_used="cache" on hit.
    """
    if not dest.is_file():
        return None
    try:
        size = dest.stat().st_size
        if size <= 0:
            return None
        with open(dest, "rb") as f:
            head = f.read(4)
        if head != _PDF_MAGIC:
            return None
        return size
    except OSError:
        return None


def _accepts_pdf_ish(ct: str) -> bool:
    """Some publishers serve PDFs under quirky content types."""
    # e.g. "application/octet-stream" from a few Elsevier CDNs
    return ct in {"application/octet-stream", "binary/octet-stream"}


def _safe_unlink(p: Path) -> None:
    try:
        p.unlink()
    except OSError:
        pass


__all__ = ["try_download", "cached_ok", "PdfAttemptOutcome"]
