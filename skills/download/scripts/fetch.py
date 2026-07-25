"""Orchestrator — the fallback chain + metadata union + PDF fetch.

Given one identifier, produce one `PaperResult`. Batch handling is a
thin wrapper: the CLI calls `fetch_one` for each identifier under an
`asyncio.Semaphore(max_concurrency)` and streams results as they land.

References:
  - PDF fallback: arxiv → unpaywall → semanticscholar → crossref
  - Metadata priority: crossref > s2 > openalex > arxiv
  - Status decisions: references/status-codes.md
  - Concurrency + retries: SKILL.md §Interaction rules
"""

from __future__ import annotations

import asyncio
import random
from pathlib import Path
from typing import Optional

import httpx

from config import DownloadConfig
from identifiers import IdKind, NormalizedId, normalize, safe_filename
from output import Candidate, Meta, PaperResult, PdfAttempt, Status
from pdf import cached_ok, try_download
from sources import SourceResult
from sources import arxiv as arxiv_src
from sources import crossref as crossref_src
from sources import openalex as openalex_src
from sources import semanticscholar as s2_src
from sources import unpaywall as unpaywall_src


# --------------------------------------------------------------------------- #
# Public entry point
# --------------------------------------------------------------------------- #


async def fetch_one(
    identifier_raw: str,
    index: int,
    cfg: DownloadConfig,
    out_dir: Path,
    client: httpx.AsyncClient,
    *,
    treat_as_title: bool = False,
) -> PaperResult:
    """Resolve one identifier, run fallback, download PDF, return one line."""

    # ------------------------------------------------------------------ #
    # 0. Normalize identifier — or route to title fallback.
    # ------------------------------------------------------------------ #
    if treat_as_title:
        return await _fetch_by_title(identifier_raw, index, cfg, out_dir, client)

    try:
        nid = normalize(identifier_raw)
    except ValueError:
        # Not an ID-shape. If the caller didn't ask for title mode, we
        # still fall back to title search for batch friendliness — a
        # user's file might mix DOIs and titles. Doc: invalid_input is
        # only for shape errors we truly can't guess.
        # However, an empty string or something clearly non-title stays
        # invalid.
        if identifier_raw.strip() and len(identifier_raw.strip()) >= 10:
            return await _fetch_by_title(identifier_raw, index, cfg, out_dir, client)
        return _mk_invalid_input(index, identifier_raw)

    # ------------------------------------------------------------------ #
    # Cache short-circuit: if the target file already exists and looks
    # like a valid PDF, return immediately without any network I/O. Users
    # who want fresh metadata delete the file or pass a different --out.
    # ------------------------------------------------------------------ #
    dest = out_dir / safe_filename(nid)
    cached = cached_ok(dest)
    if cached is not None:
        return PaperResult(
            index=index,
            identifier=identifier_raw,
            status=Status.DOWNLOADED,
            pdf_path=str(dest),
            source_used="cache",
            sources_queried=[],
            bytes=cached,
        )

    # ------------------------------------------------------------------ #
    # 1. Gather source responses (retries + rate-limit handling inside).
    # ------------------------------------------------------------------ #
    results = await _gather_sources(nid, cfg, client)

    # ------------------------------------------------------------------ #
    # 2. Metadata union — priority Crossref > S2 > OpenAlex > arXiv.
    # ------------------------------------------------------------------ #
    meta = _union_metadata(results)

    # ------------------------------------------------------------------ #
    # 3. PDF acquisition — first hit wins along the priority chain.
    # ------------------------------------------------------------------ #
    pdf_outcome, pdf_attempts, source_used = await _try_pdfs(
        nid, results, out_dir, client
    )

    # ------------------------------------------------------------------ #
    # 4. Decide the status.
    # ------------------------------------------------------------------ #
    sources_queried = [r.source_name for r in results if _was_queried(r)]

    if pdf_outcome is not None and pdf_outcome.ok:
        return PaperResult(
            index=index,
            identifier=identifier_raw,
            status=Status.DOWNLOADED,
            pdf_path=str(pdf_outcome.path) if pdf_outcome.path else None,
            source_used=source_used,
            sources_queried=sources_queried,
            bytes=pdf_outcome.bytes,
            meta=meta,
        )

    # No PDF was saved. Distinguish four failure kinds:
    if _all_not_found(results):
        return PaperResult(
            index=index,
            identifier=identifier_raw,
            status=Status.IDENTIFIER_NOT_FOUND,
            pdf_path=None,
            sources_queried=sources_queried,
            meta=None,
        )

    if _all_transport_failed(results):
        return PaperResult(
            index=index,
            identifier=identifier_raw,
            status=Status.NETWORK_ERROR,
            sources_queried=sources_queried,
            meta=meta,
        )

    if _mostly_rate_limited(results):
        return PaperResult(
            index=index,
            identifier=identifier_raw,
            status=Status.RATE_LIMITED,
            sources_queried=sources_queried,
            meta=meta,
        )

    # Unpaywall said closed-access — that dominates metadata_only.
    unpay = next((r for r in results if r.source_name == "unpaywall"), None)
    if unpay is not None and unpay.is_oa is False:
        return PaperResult(
            index=index,
            identifier=identifier_raw,
            status=Status.PAYWALLED,
            sources_queried=sources_queried,
            meta=meta,
        )

    # Somebody gave URLs but every attempt failed → pdf_link_broken.
    if pdf_attempts:
        return PaperResult(
            index=index,
            identifier=identifier_raw,
            status=Status.PDF_LINK_BROKEN,
            sources_queried=sources_queried,
            meta=meta,
            pdf_attempts=pdf_attempts,
        )

    # Metadata came in but nobody offered a PDF URL at all.
    if meta is not None:
        return PaperResult(
            index=index,
            identifier=identifier_raw,
            status=Status.METADATA_ONLY,
            sources_queried=sources_queried,
            meta=meta,
        )

    # No metadata and no PDF hint — everything failed softly. Report as
    # not-found so callers have a clear "nothing here" signal.
    return PaperResult(
        index=index,
        identifier=identifier_raw,
        status=Status.IDENTIFIER_NOT_FOUND,
        sources_queried=sources_queried,
    )


# --------------------------------------------------------------------------- #
# Title fallback
# --------------------------------------------------------------------------- #


async def _fetch_by_title(
    title: str,
    index: int,
    cfg: DownloadConfig,
    out_dir: Path,
    client: httpx.AsyncClient,
) -> PaperResult:
    """--title path: strict top-1 match → recurse via resolved DOI/arxiv."""
    search = await _with_retries(
        lambda: openalex_src.title_search(title, client, polite_email=cfg.polite_email)
    )
    if search.rate_limited:
        return PaperResult(
            index=index,
            identifier=title,
            status=Status.RATE_LIMITED,
            sources_queried=["openalex"],
        )
    if search.transport_error is not None:
        return PaperResult(
            index=index,
            identifier=title,
            status=Status.NETWORK_ERROR,
            sources_queried=["openalex"],
        )
    if search.not_found:
        return PaperResult(
            index=index,
            identifier=title,
            status=Status.IDENTIFIER_NOT_FOUND,
            sources_queried=["openalex"],
        )

    if search.resolved_doi:
        inner = await fetch_one(search.resolved_doi, index, cfg, out_dir, client)
        # Preserve original identifier as user typed it.
        inner.identifier = title
        return inner
    if search.resolved_arxiv_id:
        inner = await fetch_one(search.resolved_arxiv_id, index, cfg, out_dir, client)
        inner.identifier = title
        return inner

    # Ambiguous.
    candidates = [Candidate(**c) for c in search.candidates] if search.candidates else None
    return PaperResult(
        index=index,
        identifier=title,
        status=Status.TITLE_AMBIGUOUS,
        sources_queried=["openalex"],
        candidates=candidates,
    )


# --------------------------------------------------------------------------- #
# Source dispatch (per identifier kind)
# --------------------------------------------------------------------------- #


async def _gather_sources(
    nid: NormalizedId,
    cfg: DownloadConfig,
    client: httpx.AsyncClient,
) -> list[SourceResult]:
    """Query sources appropriate to this identifier kind.

    Everyone gets called concurrently; the orchestrator merges after.
    Sources that don't apply (e.g. Unpaywall for an arXiv-only paper
    with no DOI known) are skipped after the fact — we still make the
    call using the arXiv-derived DOI if one comes back.
    """
    doi: Optional[str] = None
    arxiv_id: Optional[str] = None
    s2_hash: Optional[str] = None
    openalex_id: Optional[str] = None

    if nid.kind == IdKind.ARXIV:
        arxiv_id = nid.value
    elif nid.kind == IdKind.DOI:
        doi = nid.value
    elif nid.kind == IdKind.S2:
        s2_hash = nid.value
    elif nid.kind == IdKind.OPENALEX:
        openalex_id = nid.value

    # Round 1: sources that accept the ID directly, so we can learn any
    # DOI/arxiv-id we don't already know.
    r1_factories: list = []
    if arxiv_id:
        r1_factories.append(lambda aid=arxiv_id: arxiv_src.fetch(aid, client))
    if openalex_id:
        r1_factories.append(
            lambda oid=openalex_id: openalex_src.fetch_by_openalex_id(oid, client, polite_email=cfg.polite_email)
        )
    if s2_hash:
        r1_factories.append(
            lambda h=s2_hash: s2_src.fetch(h, client, api_key=cfg.semanticscholar_api_key)
        )
    # (DOI-first flow fires everything in round 2 below.)

    r1_results: list[SourceResult] = []
    if r1_factories:
        r1_results = list(
            await asyncio.gather(*(_with_retries(f) for f in r1_factories))
        )

    # Extract any DOI / arxiv-id we discovered.
    for r in r1_results:
        if r.meta is not None:
            if not doi and r.meta.doi:
                doi = r.meta.doi
            if not arxiv_id and r.meta.arxiv_id:
                arxiv_id = r.meta.arxiv_id

    # Round 2: sources that consume DOI or arxiv-id, minus ones round 1
    # already answered for this paper.
    already: set[str] = {r.source_name for r in r1_results}
    r2_factories: list = []

    if doi:
        if "crossref" not in already:
            r2_factories.append(
                lambda d=doi: crossref_src.fetch(d, client, polite_email=cfg.polite_email)
            )
        if "unpaywall" not in already:
            r2_factories.append(
                lambda d=doi: unpaywall_src.fetch(d, client, polite_email=cfg.polite_email)
            )
        if "semanticscholar" not in already:
            r2_factories.append(
                lambda d=doi: s2_src.fetch(f"DOI:{d}", client, api_key=cfg.semanticscholar_api_key)
            )
        if "openalex" not in already:
            r2_factories.append(
                lambda d=doi: openalex_src.fetch_by_doi(d, client, polite_email=cfg.polite_email)
            )
    elif arxiv_id:
        # arXiv only — still worth an S2 lookup for enrichment (abstract, tldr).
        if "semanticscholar" not in already:
            r2_factories.append(
                lambda aid=arxiv_id: s2_src.fetch(f"ArXiv:{aid}", client, api_key=cfg.semanticscholar_api_key)
            )

    r2_results: list[SourceResult] = []
    if r2_factories:
        r2_results = list(
            await asyncio.gather(*(_with_retries(f) for f in r2_factories))
        )

    return r1_results + r2_results


# --------------------------------------------------------------------------- #
# Metadata union
# --------------------------------------------------------------------------- #


# Priority: higher number wins.
_PRIORITY = {
    "crossref": 4,
    "semanticscholar": 3,
    "openalex": 2,
    "arxiv": 1,
    "unpaywall": 0,  # weakest; fills gaps only
}


def _union_metadata(results: list[SourceResult]) -> Optional[Meta]:
    """Merge non-empty fields across sources, honoring priority."""
    contributing = [r for r in results if r.meta is not None]
    if not contributing:
        return None
    # Sort highest priority first
    contributing.sort(key=lambda r: -_PRIORITY.get(r.source_name, 0))

    merged: dict = {}
    for r in contributing:
        d = r.meta.model_dump(exclude_none=True)
        for k, v in d.items():
            if k not in merged or _is_empty(merged.get(k)):
                merged[k] = v

    if "title" not in merged:
        # A meta without a title is useless — pydantic requires it.
        return None
    try:
        return Meta(**merged)
    except Exception:  # noqa: BLE001 — a shape mismatch should never crash the run
        return Meta(title=merged["title"])


def _is_empty(v) -> bool:
    if v is None:
        return True
    if isinstance(v, (list, str)) and not v:
        return True
    return False


# --------------------------------------------------------------------------- #
# PDF acquisition
# --------------------------------------------------------------------------- #


async def _try_pdfs(
    nid: NormalizedId,
    results: list[SourceResult],
    out_dir: Path,
    client: httpx.AsyncClient,
):
    """Attempt PDFs in priority order, return (outcome, attempts, source_used)."""
    dest = out_dir / safe_filename(nid)
    out_dir.mkdir(parents=True, exist_ok=True)

    # Build the ordered list of (source, url) pairs.
    ordered: list[tuple[str, str]] = []
    for src_name in ("arxiv", "unpaywall", "semanticscholar", "crossref"):
        r = next((x for x in results if x.source_name == src_name), None)
        if r is not None and r.pdf_url_hint:
            ordered.append((src_name, r.pdf_url_hint))

    attempts: list[PdfAttempt] = []
    for src_name, url in ordered:
        outcome = await try_download(url, dest, client)
        if outcome.ok:
            return outcome, attempts, src_name
        attempts.append(PdfAttempt(source=src_name, url=url, reason=outcome.reason or "verify_error"))

    return None, attempts, None


# --------------------------------------------------------------------------- #
# Retries — exponential backoff, 3 attempts.
# --------------------------------------------------------------------------- #


async def _with_retries(coro_factory) -> SourceResult:
    """Call `coro_factory()` up to 3 times with exponential backoff on
    transport errors.

    `coro_factory` is a zero-arg lambda that returns a coroutine — we
    can't re-await the same coroutine object.
    """
    delays = [1.0, 2.0, 4.0]
    last: Optional[SourceResult] = None
    for attempt in range(len(delays) + 1):
        result = await coro_factory()
        last = result
        if result.transport_error is None:
            return result
        # Only retry actual transport errors (timeout / network / 5xx / parse)
        if result.transport_error in {"no_email"}:
            return result  # graceful-degradation, not an error
        if attempt >= len(delays):
            break
        # Small jitter helps when many parallel calls all backoff together.
        jitter = 0.5 + 0.5 * (attempt / max(len(delays), 1))
        await asyncio.sleep(delays[attempt] * jitter * (0.9 + 0.2 * _rand01()))
    return last if last is not None else SourceResult(source_name="unknown", transport_error="network")


def _rand01() -> float:
    return random.random()


# --------------------------------------------------------------------------- #
# Classification helpers
# --------------------------------------------------------------------------- #


def _was_queried(r: SourceResult) -> bool:
    """A source counts as "queried" unless we skipped it before sending anything."""
    return r.transport_error != "no_email"


def _all_not_found(results: list[SourceResult]) -> bool:
    queried = [r for r in results if _was_queried(r)]
    if not queried:
        return False
    return all(r.not_found for r in queried)


def _all_transport_failed(results: list[SourceResult]) -> bool:
    queried = [r for r in results if _was_queried(r)]
    if not queried:
        return False
    return all(r.transport_error is not None for r in queried)


def _mostly_rate_limited(results: list[SourceResult]) -> bool:
    queried = [r for r in results if _was_queried(r)]
    if not queried:
        return False
    if not any(r.rate_limited for r in queried):
        return False
    # Rate-limited dominates only when nothing else succeeded.
    ok = [r for r in queried if r.meta is not None or r.not_found]
    return not ok


# --------------------------------------------------------------------------- #
# Invalid input helper
# --------------------------------------------------------------------------- #


def _mk_invalid_input(index: int, identifier_raw: str) -> PaperResult:
    return PaperResult(
        index=index,
        identifier=identifier_raw,
        status=Status.INVALID_INPUT,
    )


__all__ = ["fetch_one"]
