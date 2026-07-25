"""Unit tests for the shared fuzzy heading matcher and md normalization.

These test the pure functions in ``read.py`` directly, without going
through the CLI. Section-extraction integration is exercised in
``test_search_read.py``.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
import read as read_mod  # noqa: E402
import convert as convert_mod  # noqa: E402


# ---- matcher -----------------------------------------------------------


def test_matcher_exact_word():
    assert read_mod._heading_matches("Methods", "## Methods")


def test_matcher_case_insensitive():
    assert read_mod._heading_matches("methods", "Methods")


def test_matcher_diacritic_folded():
    assert read_mod._heading_matches("etude", "Étude")


def test_matcher_multiword_contiguous():
    # Q19/B says token sequence must be a contiguous slice.
    assert read_mod._heading_matches("Encoder and Decoder", "3.1 Encoder and Decoder Stacks")


def test_matcher_multiword_not_scattered():
    # "Encoder Stacks" should NOT match "Encoder and Decoder Stacks" because
    # it's not a contiguous slice of the heading tokens.
    assert not read_mod._heading_matches("Encoder Stacks",
                                         "3.1 Encoder and Decoder Stacks")


def test_matcher_number_token_precision():
    """A dotted number in a heading normalizes to two tokens (``3`` ``2``).

    So `--section "3.2"` matches "3.2 Baselines" (needle ``[3, 2]``
    is a contiguous slice), and `--section "3"` **also** matches (needle
    ``[3]`` is a valid slice starting at position 0). The alternative
    — requiring the needle to align on whole-heading word boundaries —
    would violate Q19/B's "single fuzzy substring matcher" contract.
    Users who want tighter matches pass more specific queries.
    """
    assert read_mod._heading_matches("3.2", "3.2 Baselines")
    assert read_mod._heading_matches("3", "3.2 Baselines")
    # Sanity check: unrelated numbers don't match.
    assert not read_mod._heading_matches("4", "3.2 Baselines")


def test_matcher_partial_title():
    # Substring on the title portion of a numbered heading
    assert read_mod._heading_matches("Baselines", "3.2 Baselines")


def test_matcher_empty_query():
    assert not read_mod._heading_matches("", "## Anything")


# ---- markdown heading extraction --------------------------------------


def test_headings_from_markdown_levels():
    md = "# Top\n\ncontent\n\n## Sub\n\nmore\n\n### Sub-sub\n"
    hs = read_mod._headings_from_markdown(md)
    assert [h["level"] for h in hs] == [1, 2, 3]
    assert [h["text"] for h in hs] == ["Top", "Sub", "Sub-sub"]


def test_headings_from_markdown_captures_line():
    md = "# H1\n\nx\n\n## H2\n"
    hs = read_mod._headings_from_markdown(md)
    assert hs[0]["line"] == 1
    assert hs[1]["line"] == 5


# ---- MinerU-content-list heading extraction ---------------------------


def test_headings_from_mineru_uses_text_level():
    cl = [
        {"type": "text", "text": "Top", "text_level": 1, "page_idx": 0},
        {"type": "text", "text": "body", "page_idx": 0},
        {"type": "text", "text": "Sub", "text_level": 2, "page_idx": 1},
    ]
    hs = read_mod._headings_from_mineru(cl)
    assert len(hs) == 2
    assert hs[0]["text"] == "Top"
    assert hs[0]["level"] == 1
    assert hs[0]["page_from"] == 1  # page_idx=0 → 1-indexed
    assert hs[1]["level"] == 2
    assert hs[1]["page_from"] == 2


# ---- pages spec parser -------------------------------------------------


def test_parse_pages_single():
    assert read_mod._parse_pages("3") == {3}


def test_parse_pages_range():
    assert read_mod._parse_pages("3-5") == {3, 4, 5}


def test_parse_pages_mixed():
    assert read_mod._parse_pages("3,7,9-11") == {3, 7, 9, 10, 11}


# ---- MD text normalization (convert.py) --------------------------------


def test_normalize_strips_bom():
    raw = b"\xef\xbb\xbf# hello\n"
    assert convert_mod._normalize_text(raw) == "# hello\n"


def test_normalize_crlf_to_lf():
    raw = b"line1\r\nline2\r\n"
    assert convert_mod._normalize_text(raw) == "line1\nline2\n"


def test_normalize_bare_cr_also():
    raw = b"a\rb\r\nc\n"
    assert convert_mod._normalize_text(raw) == "a\nb\nc\n"
