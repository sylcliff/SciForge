"""Shared test fixtures.

- auto_mock_verification: makes `crossref.get_by_doi` return None by default
  so the arxiv_upgrade post-hoc verify step doesn't hit real network.
  Individual tests can still override this via monkeypatch.
"""

import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from sources import crossref  # noqa: E402


@pytest.fixture(autouse=True)
def auto_mock_verification(monkeypatch):
    """By default, mock crossref.get_by_doi to return None.

    In arxiv_upgrade._verify_upgrade_by_author_year, a None return means
    "verification lookup failed, keep the upgrade fail-open". This
    matches the safe production behavior.

    Individual tests that want to test verification rejection should
    explicitly override with their own monkeypatch.setattr.
    """
    monkeypatch.setattr(
        crossref, "get_by_doi",
        lambda doi, **kw: None,
    )
