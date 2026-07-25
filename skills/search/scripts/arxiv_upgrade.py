"""arxiv-upgrade phase (Path B) — post-dedup DOI enrichment.

After the initial β dedup groups the search results, some groups may
contain **only** an arxiv record without any DOI/PMID cross-ref. This
module finds those groups, concurrently queries OpenAlex + Semantic
Scholar for a published-version DOI keyed by the arxiv id, injects that
DOI into the record, and re-runs β dedup so the arxiv group merges with
its journal counterpart (if the journal record was also in the search
results).

Design decisions (see /grilling session):
- Both OpenAlex + S2 are queried concurrently; first source to return a
  DOI wins (γ mode).
- No upper limit on lookups per search (A choice).
- Upgrade sources do NOT enter `sources_hit` — they only inject a DOI.
  The final record instead carries `arxiv_upgraded: True` for audit.
- Best-effort: any lookup failure is silent, the record stays arxiv-only.
"""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

import dedup
import merge
from config import SearchConfig
from sources import crossref, openalex, s2


# DataCite issues `10.48550/arxiv.<id>` as the *preprint's own* DOI.
# This is NOT a "published-version" DOI — accepting it would just relabel
# the arxiv record, not merge it with an actual journal article.
_ARXIV_SELF_DOI_RE = re.compile(r"^10\.48550/arxiv\.", re.IGNORECASE)

# English stopwords to strip when comparing titles.
_TITLE_STOPWORDS = frozenset({
    "a", "an", "the", "of", "in", "on", "at", "to", "for", "and", "or",
    "with", "by", "from", "as", "is", "are", "was", "were", "be", "been",
    "vs", "via",
})


def _normalize_title(title: str | None) -> str:
    """Lowercase, strip punctuation, drop stopwords, collapse whitespace."""
    if not title:
        return ""
    s = title.lower()
    s = re.sub(r"[^a-z0-9\s]", " ", s)
    tokens = [t for t in s.split() if t and t not in _TITLE_STOPWORDS]
    return " ".join(tokens)


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 0.0
    return len(a & b) / len(a | b) if (a | b) else 0.0


def _first_author_surname(rec: dict[str, Any]) -> str:
    """Extract first-author surname lowercase from a source record."""
    authors = rec.get("authors") or []
    if not authors:
        return ""
    first = authors[0]
    if isinstance(first, dict):
        first = first.get("name") or first.get("family") or ""
    # Last space-separated token is usually the surname
    parts = str(first).strip().split()
    return parts[-1].lower() if parts else ""


def _is_title_match(arxiv_rec: dict[str, Any],
                     candidate_rec: dict[str, Any]) -> bool:
    """Return True iff `candidate_rec` is plausibly the same paper as
    `arxiv_rec`, judged by title + first-author surname.

    Rules (must satisfy ALL):
      - Normalized titles: either exact equal, or Jaccard ≥ 0.85
      - First-author surnames must match (case-insensitive) OR title
        must be exact equal

    Deliberately strict to keep the "β zero-false-positive dedup"
    promise the user chose earlier.
    """
    a_title = _normalize_title(arxiv_rec.get("title"))
    c_title = _normalize_title(candidate_rec.get("title"))
    if not a_title or not c_title:
        return False

    if a_title == c_title:
        return True

    a_tokens = set(a_title.split())
    c_tokens = set(c_title.split())
    if _jaccard(a_tokens, c_tokens) < 0.85:
        return False

    # Fuzzy match — require first-author surname agreement.
    a_sn = _first_author_surname(arxiv_rec)
    c_sn = _first_author_surname(candidate_rec)
    if not a_sn or not c_sn:
        return False
    return a_sn == c_sn


def _verify_upgrade_by_author_year(arxiv_rec: dict[str, Any],
                                    journal_doi: str,
                                    cfg: SearchConfig,
                                    respect_rate_limit: bool,
                                    year_tolerance: int = 3) -> bool:
    """Cross-check an arxiv → journal_doi upgrade using author + year.

    Fetches `journal_doi` from Crossref and confirms:
      - First-author surnames match (case-insensitive, best-effort)
      - Publication years are within `year_tolerance` (default 3)

    Returns True → keep the upgrade
    Returns False → reject the upgrade (probably wrong DOI)

    Best-effort: if the verification lookup itself fails, returns True
    (don't reject an upgrade because verification network flaked).
    """
    from sources import crossref  # local import to avoid cycle

    verified = crossref.get_by_doi(journal_doi, cfg=cfg,
                                    respect_rate_limit=respect_rate_limit)
    if verified is None:
        # Verification lookup failed — best-effort, don't reject.
        return True

    # Year check
    arxiv_year = arxiv_rec.get("year")
    verified_year = verified.get("year")
    if isinstance(arxiv_year, int) and isinstance(verified_year, int):
        if abs(verified_year - arxiv_year) > year_tolerance:
            return False

    # First-author surname check
    a_sn = _first_author_surname(arxiv_rec)
    v_sn = _first_author_surname(verified)
    if a_sn and v_sn and a_sn != v_sn:
        return False

    return True


def _is_journal_doi(doi: str | None) -> bool:
    """True if the DOI plausibly points at a journal/proceedings, not at
    the arxiv preprint itself.

    Rules:
      - DataCite `10.48550/arxiv.<id>` → NOT a journal DOI (rejected)
      - Everything else → treated as journal DOI (best-effort trust)
    """
    if not doi:
        return False
    stripped = doi.strip()
    if not stripped:
        return False
    return not _ARXIV_SELF_DOI_RE.match(stripped)


def _is_arxiv_only_record(rec: dict[str, Any]) -> bool:
    """A raw source record is 'arxiv-only' if arxiv_id is set and none of
    (doi, pmid, openalex_id, s2_id) are.

    Note: PubMed / Crossref records without arxiv_id also fail this check —
    that's correct, they aren't candidates for upgrade.
    """
    if not rec.get("arxiv_id"):
        return False
    return not any(rec.get(f) for f in ("doi", "pmid", "openalex_id", "s2_id"))


def _group_is_arxiv_only(group: list[dict[str, Any]]) -> bool:
    """A dedup group qualifies for upgrade iff none of its members carries
    any of (doi, pmid, openalex_id, s2_id) — only arxiv_id."""
    for f in ("doi", "pmid", "openalex_id", "s2_id"):
        if any(rec.get(f) for rec in group):
            return False
    # And at least one member must have arxiv_id
    return any(rec.get("arxiv_id") for rec in group)


def _pick_arxiv_id(group: list[dict[str, Any]]) -> str | None:
    for rec in group:
        aid = rec.get("arxiv_id")
        if aid:
            return str(aid).strip()
    return None


def _pick_arxiv_record(group: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Return the first record in `group` that has an arxiv_id (used to
    supply title + first-author for title-search fallback)."""
    for rec in group:
        if rec.get("arxiv_id"):
            return rec
    return None


def _lookup_doi_via_title_search(arxiv_rec: dict[str, Any],
                                  cfg: SearchConfig,
                                  respect_rate_limit: bool) -> str | None:
    """Third-tier fallback: query Crossref by the arxiv paper's title,
    accept a candidate only if title+first-author match plausibly.

    Returns the plausible journal DOI, or None. Never raises.
    """
    title = arxiv_rec.get("title")
    if not title:
        return None
    candidates = crossref.lookup_by_title(
        title, cfg=cfg, respect_rate_limit=respect_rate_limit, rows=3,
    )
    for cand in candidates:
        cand_doi = cand.get("doi")
        if not _is_journal_doi(cand_doi):
            continue
        if _is_title_match(arxiv_rec, cand):
            return cand_doi
    return None


def _lookup_doi(arxiv_id: str, cfg: SearchConfig,
                respect_rate_limit: bool) -> tuple[str | None, dict[str, Any] | None]:
    """Concurrent OpenAlex + S2 lookup; first returning a DOI wins.

    Returns (doi, upgraded_record_or_None). The upgraded record is the
    parsed record from whichever source hit — used only if we want to
    inject additional metadata; currently we only need the DOI.
    """
    def _openalex():
        rec = openalex.lookup_by_arxiv(arxiv_id, cfg=cfg,
                                        respect_rate_limit=respect_rate_limit)
        return ("openalex", rec)

    def _s2():
        rec = s2.lookup_by_arxiv(arxiv_id, cfg=cfg,
                                  respect_rate_limit=respect_rate_limit)
        return ("s2", rec)

    doi_found: str | None = None
    upgraded_rec: dict[str, Any] | None = None

    with ThreadPoolExecutor(max_workers=2) as ex:
        futures = [ex.submit(_openalex), ex.submit(_s2)]
        for fut in as_completed(futures):
            try:
                _src, rec = fut.result()
            except Exception:  # noqa: BLE001
                continue
            # Only accept a lookup result if it carries a *journal* DOI —
            # not the arxiv preprint's own DataCite DOI (10.48550/arxiv.*).
            if rec and _is_journal_doi(rec.get("doi")):
                doi_found = rec["doi"]
                upgraded_rec = rec
                # First qualifying DOI wins; cancel remaining futures.
                for f in futures:
                    if not f.done():
                        f.cancel()
                break

    return doi_found, upgraded_rec


def upgrade(raw_records: list[dict[str, Any]],
            *,
            cfg: SearchConfig,
            respect_rate_limit: bool = False,
            title_search_fallback: bool = False,
            ) -> tuple[list[dict[str, Any]], set[str], set[str]]:
    """Return (new_raw_records, upgraded_arxiv_ids, title_search_arxiv_ids).

    `new_raw_records` is the input list with arxiv-only records having
    their `doi` field filled in when a lookup succeeds. Callers should
    re-run dedup + merge on this list.

    `upgraded_arxiv_ids` — arxiv ids that got upgraded via *any* path.
    `title_search_arxiv_ids` — subset of `upgraded_arxiv_ids` that were
    upgraded via the Crossref title-search fallback (used for audit).
    """
    # Step 1: find arxiv-only groups from the initial dedup.
    groups = dedup.group_by_identifier(raw_records)
    arxiv_only_groups = [g for g in groups if _group_is_arxiv_only(g)]

    if not arxiv_only_groups:
        return raw_records, set(), set()

    # Step 2: concurrent OpenAlex + S2 lookup per arxiv-only group.
    id_to_doi: dict[str, str] = {}
    id_to_source: dict[str, str] = {}   # "id-lookup" or "title-search"

    def _one_id_lookup(group: list[dict[str, Any]]):
        arxiv_id = _pick_arxiv_id(group)
        if not arxiv_id:
            return None, None
        doi, _rec = _lookup_doi(arxiv_id, cfg, respect_rate_limit)
        return arxiv_id, doi

    with ThreadPoolExecutor(max_workers=4) as ex:
        futs = [ex.submit(_one_id_lookup, g) for g in arxiv_only_groups]
        for fut in as_completed(futs):
            try:
                arxiv_id, doi = fut.result()
            except Exception:  # noqa: BLE001
                continue
            if arxiv_id and doi:
                key = str(arxiv_id).lower()
                id_to_doi[key] = str(doi).lower()
                id_to_source[key] = "id-lookup"

    # Step 2b: title-search fallback for any group that STILL has no DOI.
    if title_search_fallback:
        remaining = []
        for g in arxiv_only_groups:
            aid = _pick_arxiv_id(g)
            if aid and str(aid).lower() not in id_to_doi:
                remaining.append(g)

        def _one_title_search(group: list[dict[str, Any]]):
            arxiv_rec = _pick_arxiv_record(group)
            if not arxiv_rec:
                return None, None
            doi = _lookup_doi_via_title_search(arxiv_rec, cfg, respect_rate_limit)
            arxiv_id = arxiv_rec.get("arxiv_id")
            return arxiv_id, doi

        with ThreadPoolExecutor(max_workers=4) as ex:
            futs = [ex.submit(_one_title_search, g) for g in remaining]
            for fut in as_completed(futs):
                try:
                    arxiv_id, doi = fut.result()
                except Exception:  # noqa: BLE001
                    continue
                if arxiv_id and doi:
                    key = str(arxiv_id).lower()
                    id_to_doi[key] = str(doi).lower()
                    id_to_source[key] = "title-search"

    if not id_to_doi:
        return raw_records, set(), set()

    # Step 2c: cross-verify each proposed upgrade using year + first-author
    # from Crossref /works/{doi}. This catches cases where OpenAlex/S2
    # returned a plausible-looking but wrong DOI. Best-effort — a network
    # failure keeps the upgrade rather than rejecting it (fail-open, since
    # rejecting silently is worse than keeping a possibly-good upgrade).
    aid_to_arxiv_rec: dict[str, dict[str, Any]] = {}
    for g in arxiv_only_groups:
        rec = _pick_arxiv_record(g)
        if rec and rec.get("arxiv_id"):
            aid_to_arxiv_rec[str(rec["arxiv_id"]).lower()] = rec

    def _verify_one(key_doi: tuple[str, str]) -> tuple[str, bool]:
        aid_key, doi = key_doi
        arxiv_rec = aid_to_arxiv_rec.get(aid_key)
        if not arxiv_rec:
            return aid_key, True  # can't verify, keep
        ok = _verify_upgrade_by_author_year(
            arxiv_rec, doi, cfg=cfg,
            respect_rate_limit=respect_rate_limit,
        )
        return aid_key, ok

    with ThreadPoolExecutor(max_workers=4) as ex:
        futs = [ex.submit(_verify_one, (k, v)) for k, v in id_to_doi.items()]
        rejected: set[str] = set()
        for fut in as_completed(futs):
            try:
                key, ok = fut.result()
            except Exception:  # noqa: BLE001
                continue
            if not ok:
                rejected.add(key)

    for key in rejected:
        id_to_doi.pop(key, None)
        id_to_source.pop(key, None)

    if not id_to_doi:
        return raw_records, set(), set()

    # Step 3: inject the resolved DOI into every record whose arxiv_id
    # matches. Mutates copies, not the input dicts.
    upgraded_arxiv_ids: set[str] = set()
    title_search_arxiv_ids: set[str] = set()
    new_records: list[dict[str, Any]] = []
    for rec in raw_records:
        aid = rec.get("arxiv_id")
        key = str(aid).lower() if aid else None
        if key and not rec.get("doi") and key in id_to_doi:
            copy = dict(rec)
            copy["doi"] = id_to_doi[key]
            upgraded_arxiv_ids.add(key)
            if id_to_source.get(key) == "title-search":
                title_search_arxiv_ids.add(key)
            new_records.append(copy)
        else:
            new_records.append(rec)

    return new_records, upgraded_arxiv_ids, title_search_arxiv_ids


def annotate_merged(merged: list[dict[str, Any]],
                    upgraded_arxiv_ids: set[str],
                    title_search_arxiv_ids: set[str] | None = None) -> None:
    """Mark each merged record with `arxiv_upgraded: bool`, in place.
    Also stamps `arxiv_upgrade_via: "id-lookup" | "title-search"` when
    the record was upgraded, so users can audit the tier used.

    A merged record is `arxiv_upgraded=True` iff any arxiv id in its
    dedup_group appears in `upgraded_arxiv_ids`.
    """
    title_search_arxiv_ids = title_search_arxiv_ids or set()
    for m in merged:
        aid = (m.get("meta") or {}).get("arxiv_id")
        hit_key: str | None = None
        if aid and str(aid).lower() in upgraded_arxiv_ids:
            hit_key = str(aid).lower()
        else:
            for gid in m.get("dedup_group", []) or []:
                if str(gid).lower() in upgraded_arxiv_ids:
                    hit_key = str(gid).lower()
                    break
        m["arxiv_upgraded"] = hit_key is not None
        if hit_key is not None:
            m["arxiv_upgrade_via"] = (
                "title-search" if hit_key in title_search_arxiv_ids
                else "id-lookup"
            )


__all__ = ["upgrade", "annotate_merged"]
