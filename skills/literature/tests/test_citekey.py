"""Unit tests for citekey generation (ids.py)."""

from __future__ import annotations

import sys
from pathlib import Path

# ``ids`` is a plain module under scripts/.
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
import ids as ids_mod  # noqa: E402


def test_basic_authoryearword():
    assert ids_mod.make_citekey("Ashish Vaswani", 2017, "Attention Is All You Need") == "vaswani2017attention"


def test_last_first_form():
    assert ids_mod.make_citekey("Vaswani, Ashish", 2017, "Attention") == "vaswani2017attention"


def test_stopword_skipped():
    # "The" is stopword → "Little" wins
    assert ids_mod.make_citekey("Jane Doe", 2024, "The Little Book") == "doe2024little"


def test_missing_year_is_nd():
    assert ids_mod.make_citekey("Alice Smith", None, "Something") == "smithndsomething"


def test_ascii_fold():
    # Diacritic-bearing surnames slugify to their ASCII base (Bär → bar).
    # Title fold is intentionally lossier — `_first_sig_word` matches
    # [A-Za-z]+ before the NFKD combining marks are stripped, so
    # "Étude" degrades to "tude". Document this rather than pretend
    # otherwise.
    ck = ids_mod.make_citekey("Bär, Åsa", 2020, "Étude")
    assert ck.startswith("bar2020")


def test_cjk_fallback_slug():
    # Non-Latin scripts keep the raw lowered characters
    ck = ids_mod.make_citekey("张三", 2024, "注意力")
    assert ck.startswith("张三2024")
    # Whole thing is well-formed
    assert len(ck) > len("张三2024")


def test_suffix_for_collision():
    existing = {"vaswani2017attention"}
    ck = ids_mod.suffix_for_collision("vaswani2017attention", existing)
    assert ck == "vaswani2017attention_a"

    existing.add("vaswani2017attention_a")
    ck = ids_mod.suffix_for_collision("vaswani2017attention", existing)
    assert ck == "vaswani2017attention_b"


def test_suffix_no_collision():
    assert ids_mod.suffix_for_collision("smith2024x", set()) == "smith2024x"


def test_detect_arxiv():
    d = ids_mod.detect("1706.03762")
    assert d and d.kind == "arxiv" and d.value == "1706.03762"

    d = ids_mod.detect("arXiv:1706.03762v2")
    assert d and d.kind == "arxiv" and d.value == "1706.03762"

    d = ids_mod.detect("https://arxiv.org/abs/1706.03762v3")
    assert d and d.kind == "arxiv" and d.value == "1706.03762"


def test_detect_doi():
    d = ids_mod.detect("https://doi.org/10.1038/s41586-020-2649-2")
    assert d and d.kind == "doi" and d.value == "10.1038/s41586-020-2649-2"

    d = ids_mod.detect("10.1038/x")
    assert d and d.kind == "doi"


def test_detect_none():
    assert ids_mod.detect("just some text") is None
