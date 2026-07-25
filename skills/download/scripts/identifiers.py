"""Identifier normalization and safe-filename derivation.

Pure logic — no network, no I/O. All rules match
`references/output-schema.md` §Identifier normalization + §Filename convention.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from urllib.parse import unquote, urlparse


class IdKind(str, Enum):
    ARXIV = "arxiv"
    DOI = "doi"
    OPENALEX = "openalex"
    S2 = "s2"


@dataclass(frozen=True)
class NormalizedId:
    kind: IdKind
    value: str  # canonical form (e.g. "1706.03762", "10.1038/…", "W2741809807")


# --------------------------------------------------------------------------- #
# Recognizers
# --------------------------------------------------------------------------- #

# New-style arXiv IDs: YYMM.NNNNN (4-5 digits) with optional version.
# Old-style (pre-2007): archive/YYMMnnn e.g. cs/0110001 — supported.
_ARXIV_NEW = re.compile(r"^(\d{4}\.\d{4,5})(v\d+)?$")
_ARXIV_OLD = re.compile(r"^([a-z\-]+(?:\.[A-Z]{2})?/\d{7})(v\d+)?$")

# DOI: starts with 10., contains a slash, permissive on the suffix.
# Real DOIs allow a wide range of characters; we accept anything printable
# except whitespace after the slash.
_DOI = re.compile(r"^10\.\d{4,9}/\S+$")

# OpenAlex Work ID: capital W + 8-11 digits.
_OPENALEX = re.compile(r"^W\d{8,11}$")

# Semantic Scholar paper ID: 40-hex.
_S2 = re.compile(r"^[0-9a-f]{40}$")

# URL host patterns
_ARXIV_URL_HOSTS = {"arxiv.org", "www.arxiv.org", "export.arxiv.org"}
_DOI_URL_HOSTS = {"doi.org", "dx.doi.org", "www.doi.org"}
_OPENALEX_URL_HOSTS = {"openalex.org", "api.openalex.org"}


# --------------------------------------------------------------------------- #
# Normalizer
# --------------------------------------------------------------------------- #


def normalize(raw: str) -> NormalizedId:
    """Normalize a user-supplied identifier to canonical form.

    Raises ValueError on unrecognisable input; the CLI maps that to
    status=invalid_input.
    """
    if not isinstance(raw, str):
        raise ValueError(f"identifier must be a string, got {type(raw).__name__}")
    s = raw.strip()
    if not s:
        raise ValueError("empty identifier")

    # URL handling first — extract the wrapped identifier and re-normalize.
    if _looks_like_url(s):
        return _from_url(s)

    # arXiv: <archive/prefix>:ID or bare ID
    if s.lower().startswith("arxiv:"):
        return _mk_arxiv(s.split(":", 1)[1].strip())
    m = _ARXIV_NEW.match(s)
    if m:
        return NormalizedId(IdKind.ARXIV, m.group(1))
    m = _ARXIV_OLD.match(s)
    if m:
        return NormalizedId(IdKind.ARXIV, m.group(1))

    # DOI: may be preceded by "doi:" prefix
    if s.lower().startswith("doi:"):
        s = s.split(":", 1)[1].strip()
    if _DOI.match(s):
        return NormalizedId(IdKind.DOI, s.lower())

    # OpenAlex — accept lowercase input, normalize to W-prefix uppercase.
    if _OPENALEX.match(s):
        return NormalizedId(IdKind.OPENALEX, s)
    if re.fullmatch(r"w\d{8,11}", s):
        return NormalizedId(IdKind.OPENALEX, "W" + s[1:])

    # Semantic Scholar 40-hex — case-insensitive input, lowercase canonical.
    if _S2.match(s.lower()):
        return NormalizedId(IdKind.S2, s.lower())

    raise ValueError(f"unrecognised identifier: {raw!r}")


def _looks_like_url(s: str) -> bool:
    return s.startswith(("http://", "https://"))


def _from_url(url: str) -> NormalizedId:
    parts = urlparse(url)
    host = (parts.netloc or "").lower()
    path = unquote(parts.path or "")

    if host in _ARXIV_URL_HOSTS:
        # /abs/1706.03762  |  /pdf/1706.03762v3.pdf  |  /abs/cs.CL/0110001
        m = re.search(r"/(?:abs|pdf|html)/([A-Za-z0-9\.\-\_/]+?)(?:v\d+)?(?:\.pdf)?$", path)
        if m:
            return _mk_arxiv(m.group(1))

    if host in _DOI_URL_HOSTS:
        # /10.xxxx/…
        tail = path.lstrip("/")
        if _DOI.match(tail):
            return NormalizedId(IdKind.DOI, tail.lower())

    if host in _OPENALEX_URL_HOSTS:
        # openalex.org/W2741809807  |  api.openalex.org/works/W...
        m = re.search(r"/(W\d{8,11})$", path)
        if m:
            return NormalizedId(IdKind.OPENALEX, m.group(1))

    raise ValueError(f"cannot extract identifier from URL: {url!r}")


def _mk_arxiv(raw: str) -> NormalizedId:
    """Build an arXiv NormalizedId, stripping version suffix."""
    s = raw.strip("/").strip()
    m = _ARXIV_NEW.match(s)
    if m:
        return NormalizedId(IdKind.ARXIV, m.group(1))
    m = _ARXIV_OLD.match(s)
    if m:
        return NormalizedId(IdKind.ARXIV, m.group(1))
    raise ValueError(f"not a valid arXiv id: {raw!r}")


# --------------------------------------------------------------------------- #
# Safe filename
# --------------------------------------------------------------------------- #

# One character class covering everything path/query-hostile. Kept simple
# on purpose: A-Z a-z 0-9 . _ - are safe on every filesystem we ship on;
# everything else becomes "_".
_UNSAFE_CHAR = re.compile(r"[^A-Za-z0-9._\-]")

# Windows path length is 260 by default; reserve 60 for directory prefix.
_MAX_STEM_LEN = 200


def safe_filename(nid: NormalizedId) -> str:
    """Return `<safe-identifier>.pdf` per output-schema.md."""
    stem = _UNSAFE_CHAR.sub("_", nid.value)
    # Explicit colon → hyphen (matches doc; already caught by _UNSAFE_CHAR
    # substituting "_", but doc calls out colon specially and someone
    # reading the tree will look for it).
    stem = stem.replace(":", "-")
    if len(stem) > _MAX_STEM_LEN:
        stem = stem[:_MAX_STEM_LEN]
    return f"{stem}.pdf"


__all__ = ["IdKind", "NormalizedId", "normalize", "safe_filename"]
