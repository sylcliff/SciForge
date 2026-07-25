"""Unit tests for identifiers.py — pure logic, no network."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Add the skill's scripts dir to sys.path so we can import flat modules.
_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(_SCRIPTS))

from identifiers import (  # noqa: E402
    IdKind,
    NormalizedId,
    normalize,
    safe_filename,
)


# --------------------------------------------------------------------------- #
# normalize()
# --------------------------------------------------------------------------- #


class TestNormalizeArxiv:
    """arXiv IDs — new & old style, bare / prefixed / URL forms."""

    @pytest.mark.parametrize(
        "raw,expected",
        [
            # bare new-style
            ("1706.03762", "1706.03762"),
            ("2301.12345", "2301.12345"),
            # version suffix stripped
            ("1706.03762v1", "1706.03762"),
            ("1706.03762v42", "1706.03762"),
            # "arXiv:" prefix, case-insensitive
            ("arXiv:1706.03762", "1706.03762"),
            ("arxiv:1706.03762v2", "1706.03762"),
            ("ArXiv:2301.12345", "2301.12345"),
            # abs URL
            ("https://arxiv.org/abs/1706.03762", "1706.03762"),
            ("https://arxiv.org/abs/1706.03762v3", "1706.03762"),
            # pdf URL with .pdf suffix
            ("https://arxiv.org/pdf/1706.03762.pdf", "1706.03762"),
            ("https://arxiv.org/pdf/1706.03762v3.pdf", "1706.03762"),
            # old-style pre-2007 identifier
            ("cs/0110001", "cs/0110001"),
            ("cs.CL/0110001", "cs.CL/0110001"),
        ],
    )
    def test_recognised(self, raw: str, expected: str) -> None:
        nid = normalize(raw)
        assert nid == NormalizedId(IdKind.ARXIV, expected)


class TestNormalizeDoi:
    """DOIs — bare, prefixed, URL forms, case handling."""

    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("10.1038/s41586-020-2649-2", "10.1038/s41586-020-2649-2"),
            # case is lowercased
            ("10.1038/S41586-020-2649-2", "10.1038/s41586-020-2649-2"),
            # doi: prefix
            ("doi:10.1038/s41586-020-2649-2", "10.1038/s41586-020-2649-2"),
            ("DOI:10.1038/S41586-020-2649-2", "10.1038/s41586-020-2649-2"),
            # doi.org URLs
            ("https://doi.org/10.1038/s41586-020-2649-2", "10.1038/s41586-020-2649-2"),
            ("https://dx.doi.org/10.1038/s41586-020-2649-2", "10.1038/s41586-020-2649-2"),
            # unusual but valid DOI suffix characters
            ("10.48550/arxiv.1706.03762", "10.48550/arxiv.1706.03762"),
        ],
    )
    def test_recognised(self, raw: str, expected: str) -> None:
        nid = normalize(raw)
        assert nid == NormalizedId(IdKind.DOI, expected)


class TestNormalizeOpenAlex:
    def test_uppercase_bare(self) -> None:
        assert normalize("W2741809807") == NormalizedId(IdKind.OPENALEX, "W2741809807")

    def test_lowercase_normalised_to_upper(self) -> None:
        assert normalize("w2741809807") == NormalizedId(IdKind.OPENALEX, "W2741809807")

    def test_url_form(self) -> None:
        assert normalize("https://openalex.org/W2741809807") == NormalizedId(
            IdKind.OPENALEX, "W2741809807"
        )


class TestNormalizeSemanticScholar:
    def test_lowercase_hex(self) -> None:
        h = "649def34d7c9ab5c02be0c9a10e83e01234abcdef"[:40]
        assert normalize(h) == NormalizedId(IdKind.S2, h)

    def test_uppercase_normalised_to_lower(self) -> None:
        h = "649DEF34D7C9AB5C02BE0C9A10E83E01234ABCDE"
        assert normalize(h) == NormalizedId(IdKind.S2, h.lower())


class TestNormalizeInvalid:
    @pytest.mark.parametrize(
        "raw",
        [
            "",
            "   ",
            "not-a-thing",
            # DOI-like but missing prefix
            "10.abc",
            # Random URL
            "https://example.com/paper",
            # Too-short "openalex" style
            "W1234",
            # Not-quite-40-hex
            "abc123",
        ],
    )
    def test_raises(self, raw: str) -> None:
        with pytest.raises(ValueError):
            normalize(raw)

    def test_non_string_rejected(self) -> None:
        with pytest.raises(ValueError):
            normalize(1706)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# safe_filename()
# --------------------------------------------------------------------------- #


class TestSafeFilename:
    @pytest.mark.parametrize(
        "kind,value,expected",
        [
            (IdKind.ARXIV, "1706.03762", "1706.03762.pdf"),
            (IdKind.DOI, "10.1038/s41586-020-2649-2", "10.1038_s41586-020-2649-2.pdf"),
            (IdKind.DOI, "10.48550/arxiv.1706.03762", "10.48550_arxiv.1706.03762.pdf"),
            (IdKind.OPENALEX, "W2741809807", "W2741809807.pdf"),
            # arXiv old-style has a slash in the id
            (IdKind.ARXIV, "cs/0110001", "cs_0110001.pdf"),
        ],
    )
    def test_common_cases(self, kind: IdKind, value: str, expected: str) -> None:
        assert safe_filename(NormalizedId(kind, value)) == expected

    def test_no_windows_illegal_chars(self) -> None:
        # DOI suffix could legally contain characters like `<`, `>`, `?`;
        # normalization catches all of them.
        nid = NormalizedId(IdKind.DOI, "10.1234/foo?bar<baz>|q")
        name = safe_filename(nid)
        for ch in '\\/*?"<>|:':
            assert ch not in name

    def test_truncation(self) -> None:
        nid = NormalizedId(IdKind.DOI, "10.1234/" + ("a" * 300))
        name = safe_filename(nid)
        # 200 char stem + ".pdf" = 204
        assert len(name) == 204
        assert name.endswith(".pdf")


if __name__ == "__main__":  # pragma: no cover
    sys.exit(pytest.main([__file__, "-v"]))
