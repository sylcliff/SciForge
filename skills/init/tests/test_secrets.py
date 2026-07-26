"""Unit tests for secrets: env-var detection, gitignore append."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import secrets as secmod  # noqa: E402


# ---- env_value ----


def test_env_value_returns_value_when_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SCIFORGE_S2_API_KEY", "abc")
    sk = secmod.by_dotted("download.semanticscholar_api_key")
    assert sk is not None
    assert secmod.env_value(sk) == "abc"


def test_env_value_returns_none_when_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SCIFORGE_S2_API_KEY", "")
    sk = secmod.by_dotted("download.semanticscholar_api_key")
    assert sk is not None
    assert secmod.env_value(sk) is None


def test_env_value_returns_none_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SCIFORGE_S2_API_KEY", raising=False)
    sk = secmod.by_dotted("download.semanticscholar_api_key")
    assert sk is not None
    assert secmod.env_value(sk) is None


# ---- by_dotted ----


def test_by_dotted_finds_each_registered_key() -> None:
    for sk in secmod.SECRET_KEYS:
        assert secmod.by_dotted(sk.dotted) is sk


def test_by_dotted_unknown_returns_none() -> None:
    assert secmod.by_dotted("nope.does.not.exist") is None


# ---- format_export_line ----


def test_format_export_line_shape() -> None:
    line = secmod.format_export_line("FOO", "bar-baz")
    assert line == 'export FOO="bar-baz"'


# ---- gitignore ----


def _make_git_repo(root: Path) -> Path:
    (root / ".git").mkdir()
    return root


def test_append_gitignore_creates_when_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _make_git_repo(tmp_path)
    monkeypatch.chdir(tmp_path)
    gi, added = secmod.append_gitignore()
    assert gi is not None and gi.is_file()
    text = gi.read_text(encoding="utf-8")
    assert ".sciforge.toml" in text
    assert "sciforge/" in text
    assert "# SciForge" in text
    assert set(added) == set(secmod.GITIGNORE_ENTRIES)


def test_append_gitignore_idempotent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _make_git_repo(tmp_path)
    monkeypatch.chdir(tmp_path)
    secmod.append_gitignore()
    gi, added = secmod.append_gitignore()
    # Second call adds nothing
    assert added == []
    text = gi.read_text(encoding="utf-8")  # type: ignore[union-attr]
    # Each entry appears exactly once
    assert text.count(".sciforge.toml") == 1
    assert text.count("sciforge/") == 1


def test_append_gitignore_preserves_existing_entries(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _make_git_repo(tmp_path)
    (tmp_path / ".gitignore").write_text("node_modules/\n__pycache__/\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    gi, added = secmod.append_gitignore()
    text = gi.read_text(encoding="utf-8")  # type: ignore[union-attr]
    assert "node_modules/" in text
    assert "__pycache__/" in text
    assert set(added) == set(secmod.GITIGNORE_ENTRIES)


def test_append_gitignore_returns_none_outside_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)  # no .git
    gi, added = secmod.append_gitignore()
    assert gi is None
    assert added == []


def test_append_gitignore_only_partial_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _make_git_repo(tmp_path)
    (tmp_path / ".gitignore").write_text(".sciforge.toml\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    gi, added = secmod.append_gitignore()
    assert added == ["sciforge/"]
    text = gi.read_text(encoding="utf-8")  # type: ignore[union-attr]
    assert text.count(".sciforge.toml") == 1
    assert text.count("sciforge/") == 1


# ---- is_git_repo ----


def test_is_git_repo_true(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _make_git_repo(tmp_path)
    monkeypatch.chdir(tmp_path)
    assert secmod.is_git_repo() is True


def test_is_git_repo_false(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    assert secmod.is_git_repo() is False
