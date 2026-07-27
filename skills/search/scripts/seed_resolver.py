"""Seed-ID classification for `sf-search refs` / `sf-search cited-by`.

Given the positional `<id>` argument, decide which kind of identifier it
is and, for the sciforge:// URI shape, shell out to `sf-lit show --json`
to resolve it into an external identifier.

Recognized kinds (in priority order):
    - "sciforge_uri"  → `sciforge://literature/<citekey>`. Resolved by
                        forking `sf-lit show <key> --json` (skill contract);
                        pulls doi/arxiv_id/s2_paper_id from the JSON.
    - "doi"           → matches `^10\\.\\d{4,9}/…`. A DataCite arXiv
                        self-DOI (`10.48550/arxiv.*`) is downgraded to
                        arxiv kind (extracts the embedded arxiv id).
    - "arxiv"         → `2101.00001`, `2101.00001v3`, or old-style
                        `hep-th/9901001`.
    - "pmid"          → 8-9 digit integer.
    - "openalex"      → `W\\d+`.
    - "s2"            → 40-char lowercase hex (SHA1).
    - unknown         → raise `SeedError` (caller maps to exit 2).

The resolver returns a `SeedId` dataclass carrying:
    - `kind` — the primary kind chosen (used for query routing)
    - `primary` — the raw string of that primary id
    - `ids` — a dict `{doi, pmid, arxiv_id, openalex_id, s2_id}` with the
      values we know (only sciforge URIs typically populate multiple)

Never talks to the network; the `sf-lit` subprocess is local.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from typing import Any


# --------------------------------------------------------------------------- #
# Regexes
# --------------------------------------------------------------------------- #

# DataCite arxiv self-DOI: 10.48550/arxiv.<id>[vN]
_ARXIV_SELF_DOI_RE = re.compile(
    r"^10\.48550/arxiv\.(?P<a>[a-z\-]+/\d{7}|\d{4}\.\d{4,5})(?:v\d+)?$",
    re.IGNORECASE,
)

# Generic DOI (must not be an arxiv self-DOI — handled first)
_DOI_RE = re.compile(r"^10\.\d{4,9}/\S+$")

# arXiv modern (2101.00001) or old-style (hep-th/9901001); optional vN
_ARXIV_RE = re.compile(r"^(?P<id>\d{4}\.\d{4,5}|[a-z\-]+/\d{7})(?:v\d+)?$", re.IGNORECASE)

# PubMed ID: 8-9 digit int (allow leading digits shorter for older ids too,
# but 7-9 covers modern; keep it strict to avoid clashing with arxiv 9-digit
# old-style ids that also contain a slash so are safely excluded by regex).
_PMID_RE = re.compile(r"^\d{7,9}$")

# OpenAlex work id
_OPENALEX_RE = re.compile(r"^W\d+$", re.IGNORECASE)

# S2 paper id (SHA1)
_S2_RE = re.compile(r"^[0-9a-f]{40}$", re.IGNORECASE)

# sciforge URI in literature namespace
_SCIFORGE_URI_PREFIX = "sciforge://literature/"


# --------------------------------------------------------------------------- #
# Data class
# --------------------------------------------------------------------------- #


@dataclass
class SeedId:
    """Resolved seed identifier.

    `kind` is the primary chosen classification; `primary` is that raw id
    string (already normalized where relevant — DOIs lowercased, arxiv
    stripped of trailing vN).

    `ids` holds every id we know about the seed. For 5-external-ID inputs
    only one field is populated; for a sciforge URI we may know several.
    """

    kind: str  # "doi" | "pmid" | "arxiv" | "openalex" | "s2"
    primary: str
    ids: dict[str, str | None] = field(default_factory=dict)

    def get(self, key: str) -> str | None:
        return self.ids.get(key)


class SeedError(ValueError):
    """Raised when `<id>` cannot be classified. Caller maps to exit 2."""


class SeedResolveError(RuntimeError):
    """Raised when a sciforge URI cannot be resolved (sf-lit not on PATH,
    unknown citekey, etc.). Caller maps to exit 3."""


# --------------------------------------------------------------------------- #
# Public entry point
# --------------------------------------------------------------------------- #


def classify(id_str: str, *, sf_lit_cmd: str = "sf-lit") -> SeedId:
    """Classify a raw `<id>` string. See module docstring for shapes.

    `sf_lit_cmd` is the executable name to invoke for sciforge:// URIs
    (override in tests). It is passed to `subprocess.run` shell=False, so
    it must be on PATH.
    """
    if id_str is None:
        raise SeedError("seed id is empty")
    s = id_str.strip()
    if not s:
        raise SeedError("seed id is empty")

    # 1. sciforge URI
    if s.startswith(_SCIFORGE_URI_PREFIX):
        citekey = s[len(_SCIFORGE_URI_PREFIX):]
        if not citekey:
            raise SeedError(f"empty citekey in URI: {s!r}")
        return _resolve_sciforge_uri(citekey, sf_lit_cmd=sf_lit_cmd)

    # 2. arxiv self-DOI → downgrade to arxiv
    m = _ARXIV_SELF_DOI_RE.match(s)
    if m:
        aid = m.group("a")
        return SeedId(kind="arxiv", primary=aid, ids={"arxiv_id": aid})

    # 3. plain DOI
    if _DOI_RE.match(s):
        doi = s.lower()
        return SeedId(kind="doi", primary=doi, ids={"doi": doi})

    # 4. OpenAlex W-id
    if _OPENALEX_RE.match(s):
        wid = s.upper()  # W123 canonical uppercase
        return SeedId(kind="openalex", primary=wid, ids={"openalex_id": wid})

    # 5. arxiv (modern or old-style). Match AFTER PMID? No — modern arxiv
    # always contains a dot; old-style contains a slash; neither collide
    # with 7-9 digit ints.
    m = _ARXIV_RE.match(s)
    if m:
        aid = m.group("id")
        return SeedId(kind="arxiv", primary=aid, ids={"arxiv_id": aid})

    # 6. PMID
    if _PMID_RE.match(s):
        return SeedId(kind="pmid", primary=s, ids={"pmid": s})

    # 7. S2 SHA1
    if _S2_RE.match(s):
        sid = s.lower()
        return SeedId(kind="s2", primary=sid, ids={"s2_id": sid})

    raise SeedError(
        f"unrecognized seed id format: {s!r} "
        f"(expected DOI, PMID, arxiv id, OpenAlex W-id, S2 SHA1, or sciforge://literature/<key>)"
    )


# --------------------------------------------------------------------------- #
# sciforge URI resolver — subprocess
# --------------------------------------------------------------------------- #


def _resolve_sciforge_uri(citekey: str, *, sf_lit_cmd: str) -> SeedId:
    """Shell out to `sf-lit show <citekey> --json` and pick a primary ID.

    Preferred order for `primary`: doi → arxiv_id → s2_paper_id.
    If none of the three are set on the metadata, we raise
    `SeedResolveError` — the paper is known to sf-lit but has no external
    identifier to hand to any citation source.
    """
    # `subprocess.run(shell=False)` on Windows requires a full path or
    # an executable that CreateProcess recognizes (.exe / .com). It
    # will NOT auto-resolve extensionless names or `.bat` shims on PATH.
    # We use `shutil.which` (which respects PATHEXT) to normalize the
    # command to a full path before spawning.
    resolved_cmd = shutil.which(sf_lit_cmd) or sf_lit_cmd
    try:
        proc = subprocess.run(  # noqa: S603 — sf_lit_cmd controlled
            [resolved_cmd, "show", citekey, "--json"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except FileNotFoundError as e:
        raise SeedResolveError(
            f"sf-lit not on PATH (needed to resolve sciforge://literature/{citekey}). "
            f"Install the literature skill or use a direct external id."
        ) from e
    except subprocess.TimeoutExpired as e:
        raise SeedResolveError(
            f"sf-lit show {citekey!r} timed out after 30s"
        ) from e

    if proc.returncode != 0:
        stderr = (proc.stderr or "").strip() or f"exit {proc.returncode}"
        raise SeedResolveError(
            f"sf-lit show {citekey!r} failed: {stderr}"
        )

    try:
        meta = json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        raise SeedResolveError(
            f"sf-lit show {citekey!r} did not emit valid JSON: {e}"
        ) from e

    if not isinstance(meta, dict):
        raise SeedResolveError(
            f"sf-lit show {citekey!r} JSON was not an object"
        )

    doi = _clean(meta.get("doi"))
    arxiv_id = _clean(meta.get("arxiv_id"))
    s2_id = _clean(meta.get("s2_paper_id"))

    ids: dict[str, str | None] = {}
    if doi:
        ids["doi"] = doi.lower()
    if arxiv_id:
        ids["arxiv_id"] = arxiv_id
    if s2_id:
        ids["s2_id"] = s2_id.lower()

    if doi:
        return SeedId(kind="doi", primary=doi.lower(), ids=ids)
    if arxiv_id:
        return SeedId(kind="arxiv", primary=arxiv_id, ids=ids)
    if s2_id:
        return SeedId(kind="s2", primary=s2_id.lower(), ids=ids)

    raise SeedResolveError(
        f"sciforge://literature/{citekey} has no doi / arxiv_id / s2_paper_id "
        f"— nothing to hand to the citation sources"
    )


def _clean(v: Any) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    return s or None


__all__ = ["classify", "SeedId", "SeedError", "SeedResolveError"]
