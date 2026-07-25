"""Environment self-check for sf-download.

Prints:
  - Resolved config values (with source hint)
  - Reachability + latency of the 5 source endpoints

Never fails hard on unset credentials (they downgrade features, not
crash the run). Exit codes:
  0 — process completed
  1 — internal error (unhandled exception)
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Iterable

import httpx

from config import DownloadConfig, load_config


# Sources probed by `doctor`. Each probe is a cheap GET that returns 200
# quickly for a healthy service. We do NOT hit their query endpoints —
# just the API root or a well-known landing — so this stays polite.
PROBES: list[tuple[str, str]] = [
    ("arxiv", "https://export.arxiv.org/api/query?search_query=id:1706.03762&max_results=1"),
    ("crossref", "https://api.crossref.org/works?rows=0"),
    ("unpaywall", "https://api.unpaywall.org/v2/10.1038/nature12373"),  # 400 without email is fine — we only check reachability
    ("openalex", "https://api.openalex.org/works?per_page=1"),
    ("semanticscholar", "https://api.semanticscholar.org/graph/v1/paper/search?query=test&limit=1"),
]


def _fmt_bool_warn(v: bool | None, ok_label: str = "ok", warn_label: str = "warn") -> str:
    return f"[{ok_label}]" if v else f"[{warn_label}]"


async def _probe(client: httpx.AsyncClient, url: str) -> tuple[int | None, float | None, str | None]:
    """Return (status_code, latency_ms, error) — one of the first two is None."""
    import time

    t0 = time.monotonic()
    try:
        r = await client.get(url)
        elapsed = (time.monotonic() - t0) * 1000
        return r.status_code, elapsed, None
    except Exception as exc:  # noqa: BLE001 — doctor summarises anything that goes wrong
        elapsed = (time.monotonic() - t0) * 1000
        return None, elapsed, type(exc).__name__


async def _run_probes(timeout: float) -> list[tuple[str, str, int | None, float | None, str | None]]:
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as c:
        tasks = [_probe(c, url) for _, url in PROBES]
        results = await asyncio.gather(*tasks)
    return [
        (name, url, status, latency, err)
        for (name, url), (status, latency, err) in zip(PROBES, results)
    ]


def _print_kv(rows: Iterable[tuple[str, str, str]]) -> None:
    """Print aligned `key   value   [tag]` rows."""
    rows = list(rows)
    if not rows:
        return
    key_w = max(len(r[0]) for r in rows)
    val_w = max(len(r[1]) for r in rows)
    for k, v, tag in rows:
        print(f"{k.ljust(key_w)}  {v.ljust(val_w)}  {tag}".rstrip())


def _config_rows(cfg: DownloadConfig) -> list[tuple[str, str, str]]:
    rows: list[tuple[str, str, str]] = []

    # polite_email
    email = cfg.polite_email
    if email:
        rows.append(("polite_email", email, "[ok]"))
    else:
        rows.append(("polite_email", "(unset)", "[warn: Unpaywall will be skipped]"))

    # semanticscholar_api_key
    if cfg.semanticscholar_api_key:
        rows.append(("s2_api_key", "(set)", "[ok]"))
    else:
        rows.append(("s2_api_key", "(unset)", "[warn: 429 more likely]"))

    rows.append(("http_timeout_seconds", str(cfg.http_timeout_seconds), "[ok]"))
    rows.append(("max_concurrency", str(cfg.max_concurrency), "[ok]"))

    # download_dir — check writable
    dl = cfg.resolved_download_dir
    dl_status = "[ok, writable]"
    try:
        dl.mkdir(parents=True, exist_ok=True)
        probe = dl / ".sf-download-doctor-probe"
        probe.write_bytes(b"")
        probe.unlink(missing_ok=True)
    except Exception as exc:  # noqa: BLE001
        dl_status = f"[error: {type(exc).__name__}]"
    rows.append(("download_dir", str(dl), dl_status))

    rows.append(("config_source", cfg._source_path, ""))
    return rows


def _probe_rows(results: list[tuple[str, str, int | None, float | None, str | None]]) -> list[tuple[str, str, str]]:
    rows: list[tuple[str, str, str]] = []
    for name, url, status, latency, err in results:
        if err is not None:
            tag = f"[error: {err}]"
        elif status is not None and 200 <= status < 500:
            # Any non-5xx is "reachable" — Unpaywall's 400 for missing
            # email counts as service-alive.
            tag = f"[ok, {int(latency):d}ms]" if latency is not None else "[ok]"
        else:
            tag = f"[error: HTTP {status}]" if status is not None else "[error]"
        rows.append((f"reachability {name}", url, tag))
    return rows


def run() -> int:
    cfg = load_config()

    print("Configuration:")
    _print_kv(_config_rows(cfg))
    print()

    print("Source reachability:")
    try:
        probe_results = asyncio.run(_run_probes(min(cfg.http_timeout_seconds, 10)))
    except Exception as exc:  # noqa: BLE001
        print(f"  probe run failed: {type(exc).__name__}: {exc}")
        return 1
    _print_kv(_probe_rows(probe_results))
    return 0


if __name__ == "__main__":  # pragma: no cover
    # Allow direct invocation: python doctor.py
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    sys.exit(run())
