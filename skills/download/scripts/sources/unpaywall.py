"""Unpaywall source — authoritative OA PDF URL per DOI. Email required.

References/sources.md §Unpaywall.

If `polite_email` is falsy, `fetch()` returns an empty SourceResult with
`transport_error="no_email"` — the orchestrator treats that as
"skipped" (no request made, no counters incremented). This is the
"graceful degradation" behaviour Q8b=B decided.
"""

from __future__ import annotations

from typing import Optional

import httpx

from output import Meta
from sources import SourceResult

API_ROOT = "https://api.unpaywall.org/v2"


async def fetch(doi: str, client: httpx.AsyncClient, *, polite_email: str = "") -> SourceResult:
    result = SourceResult(source_name="unpaywall")

    if not polite_email:
        # Doc contract: no email → skip Unpaywall entirely. The
        # orchestrator sees transport_error="no_email" and does not
        # add "unpaywall" to sources_queried.
        result.transport_error = "no_email"
        return result

    url = f"{API_ROOT}/{doi}"
    try:
        r = await client.get(url, params={"email": polite_email})
    except httpx.TimeoutException:
        result.transport_error = "timeout"
        return result
    except httpx.RequestError:
        result.transport_error = "network"
        return result

    result.raw_status_code = r.status_code
    if r.status_code == 429:
        result.rate_limited = True
        return result
    if r.status_code >= 500:
        result.transport_error = f"http_{r.status_code}"
        return result
    if r.status_code == 404:
        result.not_found = True
        return result
    if r.status_code >= 400:
        # 400 = bad email etc. Treat as transport-level so fallback continues.
        result.transport_error = f"http_{r.status_code}"
        return result

    try:
        data = r.json()
    except ValueError:
        result.transport_error = "parse_error"
        return result
    if not isinstance(data, dict):
        result.transport_error = "parse_error"
        return result

    result.is_oa = bool(data.get("is_oa"))
    result.meta = _parse_meta(data)
    if result.is_oa:
        result.pdf_url_hint = _pick_oa_pdf(data)
    return result


def _parse_meta(data: dict) -> Optional[Meta]:
    """Fill only the fields Unpaywall is authoritative about."""
    title = data.get("title")
    if not isinstance(title, str) or not title:
        return None
    year = data.get("year") if isinstance(data.get("year"), int) else None
    journal = data.get("journal_name") if isinstance(data.get("journal_name"), str) else None
    doi_val = data.get("doi")
    doi = doi_val.lower() if isinstance(doi_val, str) else None

    # `url` best-effort — Unpaywall stores it under best_oa_location or doi_url.
    url = data.get("doi_url") if isinstance(data.get("doi_url"), str) else None
    if not url:
        best = data.get("best_oa_location")
        if isinstance(best, dict) and isinstance(best.get("url"), str):
            url = best["url"]

    return Meta(
        title=title,
        year=year,
        venue_full=journal,
        doi=doi,
        url=url,
    )


def _pick_oa_pdf(data: dict) -> Optional[str]:
    """Prefer `best_oa_location.url_for_pdf`, fall back to `.url`."""
    best = data.get("best_oa_location")
    if isinstance(best, dict):
        pdf = best.get("url_for_pdf")
        if isinstance(pdf, str) and pdf:
            return pdf
        landing = best.get("url")
        if isinstance(landing, str) and landing:
            return landing
    # Fallback: iterate oa_locations for any url_for_pdf.
    locs = data.get("oa_locations")
    if isinstance(locs, list):
        for loc in locs:
            if isinstance(loc, dict) and isinstance(loc.get("url_for_pdf"), str):
                return loc["url_for_pdf"]
    return None


__all__ = ["fetch", "API_ROOT"]
