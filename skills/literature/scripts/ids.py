#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///

"""ID parsing and citekey generation.

- Detect arXiv / DOI / S2 identifiers from raw user input.
- Generate a stable, human-friendly BibTeX citekey.
"""

import re
import unicodedata
from typing import NamedTuple

ARXIV_RE = re.compile(r"\b(?:arXiv:)?(\d{4}\.\d{4,5})(v\d+)?\b", re.IGNORECASE)
ARXIV_OLD_RE = re.compile(r"\b([a-z-]+(?:\.[A-Z]{2})?/\d{7})(v\d+)?\b")
ARXIV_URL_RE = re.compile(r"arxiv\.org/(?:abs|pdf)/([^\s?]+?)(?:\.pdf)?(?:[?#]|$)", re.IGNORECASE)
DOI_RE = re.compile(r"\b(10\.\d{4,9}/[\w./()<>-]+)")
DOI_URL_RE = re.compile(r"(?:https?://)?(?:dx\.)?doi\.org/(10\.\d{4,9}/[\w./()<>-]+)", re.IGNORECASE)
S2_URL_RE = re.compile(r"semanticscholar\.org/paper/[^\s/]+/([0-9a-f]{40})", re.IGNORECASE)


class DetectedId(NamedTuple):
    kind: str  # "arxiv" | "doi" | "s2"
    value: str  # canonical form (no version suffix, lowercased DOI)


def detect(text: str) -> DetectedId | None:
    """Detect the first arxiv/doi/s2 identifier in `text`."""
    text = text.strip()

    # arXiv URL (before bare arxiv id, because URLs often contain them)
    m = ARXIV_URL_RE.search(text)
    if m:
        raw = m.group(1)
        # strip optional version suffix
        raw = re.sub(r"v\d+$", "", raw)
        return DetectedId("arxiv", raw)

    m = ARXIV_RE.search(text)
    if m:
        return DetectedId("arxiv", m.group(1))

    m = ARXIV_OLD_RE.search(text)
    if m:
        return DetectedId("arxiv", m.group(1))

    m = DOI_URL_RE.search(text)
    if m:
        return DetectedId("doi", m.group(1).lower())

    m = DOI_RE.search(text)
    if m:
        return DetectedId("doi", m.group(1).lower())

    m = S2_URL_RE.search(text)
    if m:
        return DetectedId("s2", m.group(1))

    return None


_STOPWORDS = {
    "a", "an", "the", "of", "on", "in", "for", "and", "or", "to",
    "with", "from", "by", "as", "at", "is", "are", "be", "this",
    "that", "we", "our", "not", "how", "what", "why", "when",
}


def _first_last_name(full_name: str) -> str:
    """Best-effort extract of the last name from 'First Middle Last' or 'Last, First'."""
    name = full_name.strip()
    if not name:
        return "anon"
    if "," in name:
        return name.split(",", 1)[0].strip()
    parts = name.split()
    if not parts:
        return "anon"
    # Strip trailing suffixes like "Jr.", "III" — but only if there's
    # another name part left. Otherwise "V" (a single-letter author)
    # gets swallowed by the roman-numeral pattern.
    while len(parts) > 1 and re.fullmatch(r"[IVX]+|Jr\.?|Sr\.?|III?", parts[-1]):
        parts.pop()
    return parts[-1] if parts else "anon"


def _slugify(text: str) -> str:
    """Lowercase, alphanumeric-only slug.

    Prefer ASCII (NFKD strip + ascii encode). For scripts with no ASCII
    fold (CJK, Cyrillic, Arabic, …), fall back to a pinyin-free hex-ish
    representation using the lowered Unicode letters directly — worse
    for humans but still stable and grep-friendly.
    """
    normalized = unicodedata.normalize("NFKD", text)
    stripped_combining = "".join(c for c in normalized if not unicodedata.combining(c))
    ascii_only = stripped_combining.encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-zA-Z0-9]", "", ascii_only).lower()
    if slug:
        return slug
    # Fallback: keep letters/digits from the original as-is (lowercased where possible).
    # This preserves CJK / non-Latin scripts instead of collapsing to "anon".
    raw = "".join(
        c for c in text
        if c.isalnum() and not unicodedata.combining(c)
    ).lower()
    return raw


def _first_sig_word(title: str) -> str:
    """First significant (non-stopword) word from title, slugified.

    Prefer an ASCII word; on failure, fall back to the slugified head
    of the whole title (up to 12 chars) so non-Latin titles still get
    a meaningful key component.
    """
    for word in re.findall(r"[A-Za-z']+", title):
        w = word.lower().strip("'")
        if w and w not in _STOPWORDS:
            return _slugify(w)
    slug = _slugify(title)
    if slug:
        return slug[:12]
    return "paper"


def make_citekey(first_author: str, year: int | str | None, title: str) -> str:
    """Generate 'lastname{year}{firstsigword}' BibTeX-style citekey."""
    last = _slugify(_first_last_name(first_author)) or "anon"
    year_str = str(year) if year else "nd"
    word = _first_sig_word(title or "")
    return f"{last}{year_str}{word}"


def suffix_for_collision(base: str, existing: set[str]) -> str:
    """Return base + '_a' / '_b' / ... that doesn't collide."""
    if base not in existing:
        return base
    # a, b, ..., z, aa, ab, ...
    from itertools import count, product
    letters = "abcdefghijklmnopqrstuvwxyz"
    for n in count(1):
        for combo in product(letters, repeat=n):
            candidate = base + "_" + "".join(combo)
            if candidate not in existing:
                return candidate


if __name__ == "__main__":
    import sys
    for arg in sys.argv[1:]:
        d = detect(arg)
        if d:
            print(f"{d.kind}\t{d.value}")
        else:
            print(f"none\t{arg}")