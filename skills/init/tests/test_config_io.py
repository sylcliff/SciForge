"""Unit tests for config_io: merge, backup, atomic write, [init] metadata."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
import tomlkit

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import config_io as cio  # noqa: E402


# ---- load_document ----


def test_load_missing_returns_empty(tmp_path: Path) -> None:
    doc = cio.load_document(tmp_path / "nope.toml")
    assert tomlkit.dumps(doc).strip() == ""


def test_load_malformed_returns_empty(tmp_path: Path) -> None:
    f = tmp_path / "bad.toml"
    f.write_text("this is = not = valid = toml\n[unclosed", encoding="utf-8")
    doc = cio.load_document(f)
    assert tomlkit.dumps(doc).strip() == ""


def test_load_preserves_comments_and_unknown_keys(tmp_path: Path) -> None:
    f = tmp_path / "cfg.toml"
    f.write_text(
        """\
# top-level user comment
[download]
polite_email = "you@example.com"
custom_debug_flag = true         # user's own note

[sources.custom]
url = "https://example.com/api"
""",
        encoding="utf-8",
    )
    doc = cio.load_document(f)
    out = tomlkit.dumps(doc)
    assert "top-level user comment" in out
    assert "custom_debug_flag" in out
    assert "user's own note" in out
    assert "[sources.custom]" in out


# ---- set_value + merge ----


def test_set_value_creates_section(tmp_path: Path) -> None:
    doc = tomlkit.document()
    cio.set_value(doc, "download.polite_email", "a@b.c")
    assert cio.get_value(doc, "download.polite_email") == "a@b.c"


def test_set_value_preserves_existing_sibling(tmp_path: Path) -> None:
    doc = tomlkit.parse('[download]\ncustom = 42\n')
    cio.set_value(doc, "download.polite_email", "a@b.c")
    out = tomlkit.dumps(doc)
    assert "custom = 42" in out
    assert 'polite_email = "a@b.c"' in out


def test_set_value_secret_adds_warning_comment(tmp_path: Path) -> None:
    doc = tomlkit.document()
    cio.set_value(doc, "download.semanticscholar_api_key", "abc123", secret=True)
    out = tomlkit.dumps(doc)
    assert "WARNING: secret" in out
    assert "abc123" in out


def test_set_value_secret_does_not_duplicate_warning(tmp_path: Path) -> None:
    doc = tomlkit.document()
    cio.set_value(doc, "download.semanticscholar_api_key", "a1", secret=True)
    cio.set_value(doc, "download.semanticscholar_api_key", "a2", secret=True)
    out = tomlkit.dumps(doc)
    # Only one warning banner
    assert out.count("WARNING: secret") == 1
    # Latest value wins
    assert "a2" in out
    assert '"a1"' not in out


# ---- init metadata ----


def test_record_init_meta_writes_version_and_time(tmp_path: Path) -> None:
    doc = tomlkit.document()
    cio.record_init_meta(doc, skipped=["a", "b"])
    init = doc["init"]
    assert str(init["version"]) == cio.INIT_SCHEMA_VERSION
    # ISO-like timestamp with a Z suffix
    assert str(init["last_run_at"]).endswith("Z")
    assert set(init["skipped_keys"]) == {"a", "b"}


def test_record_init_meta_unions_skipped(tmp_path: Path) -> None:
    doc = tomlkit.document()
    cio.record_init_meta(doc, skipped=["a"])
    cio.record_init_meta(doc, skipped=["b", "a"])
    assert set(doc["init"]["skipped_keys"]) == {"a", "b"}


def test_prior_skipped_keys_returns_set(tmp_path: Path) -> None:
    doc = tomlkit.parse(
        """\
[init]
version = "1"
last_run_at = "2026-01-01T00:00:00Z"
skipped_keys = ["download.semanticscholar_api_key"]
"""
    )
    assert cio.prior_skipped_keys(doc) == {"download.semanticscholar_api_key"}


def test_prior_skipped_keys_empty_when_no_init_section() -> None:
    doc = tomlkit.document()
    assert cio.prior_skipped_keys(doc) == set()


# ---- backup ----


def test_backup_creates_timestamped_copy(tmp_path: Path) -> None:
    f = tmp_path / "cfg.toml"
    f.write_text("hello", encoding="utf-8")
    b = cio.backup(f)
    assert b is not None and b.is_file()
    assert b.read_text(encoding="utf-8") == "hello"
    assert b.name.startswith("cfg.toml.bak-")


def test_backup_returns_none_when_file_missing(tmp_path: Path) -> None:
    assert cio.backup(tmp_path / "nope.toml") is None


# ---- atomic_write ----


def test_atomic_write_creates_file_and_content(tmp_path: Path) -> None:
    doc = tomlkit.document()
    cio.set_value(doc, "download.polite_email", "a@b.c")
    target = tmp_path / "out" / "cfg.toml"
    cio.atomic_write(target, doc)
    assert target.is_file()
    assert "polite_email" in target.read_text(encoding="utf-8")


def test_atomic_write_replaces_existing(tmp_path: Path) -> None:
    target = tmp_path / "cfg.toml"
    target.write_text("old content", encoding="utf-8")
    doc = tomlkit.parse('[download]\npolite_email = "new@x.com"\n')
    cio.atomic_write(target, doc)
    assert "new@x.com" in target.read_text(encoding="utf-8")
    assert "old content" not in target.read_text(encoding="utf-8")


def test_atomic_write_leaves_no_tmp_files_behind(tmp_path: Path) -> None:
    target = tmp_path / "cfg.toml"
    cio.atomic_write(target, tomlkit.document())
    stragglers = [p for p in tmp_path.iterdir() if p.suffix == ".tmp"]
    assert stragglers == []


# ---- redact_secrets ----


def test_redact_secrets_masks_values() -> None:
    doc = tomlkit.parse(
        '[download]\nsemanticscholar_api_key = "secretvalue"\npolite_email = "e@x.com"\n'
    )
    r = cio.redact_secrets(doc)
    out = tomlkit.dumps(r)
    assert "secretvalue" not in out
    assert "<redacted>" in out
    # Non-secrets untouched
    assert "e@x.com" in out


def test_redact_secrets_no_change_when_secret_absent() -> None:
    doc = tomlkit.parse('[download]\npolite_email = "e@x.com"\n')
    r = cio.redact_secrets(doc)
    assert "polite_email" in tomlkit.dumps(r)


# ---- path resolution ----


def test_user_global_config_path_uses_xdg(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    p = cio.user_global_config_path()
    assert p == tmp_path / "xdg" / "sciforge" / "config.toml"


def test_find_active_config_prefers_env_over_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env_cfg = tmp_path / "env.toml"
    env_cfg.write_text("", encoding="utf-8")
    monkeypatch.setenv("SCIFORGE_CONFIG", str(env_cfg))
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".sciforge.toml").write_text("", encoding="utf-8")
    assert cio.find_active_config_path() == env_cfg


def test_project_local_stops_at_git_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "outer").mkdir()
    (tmp_path / "outer" / "inner").mkdir()
    (tmp_path / "outer" / ".git").mkdir()
    # A .sciforge.toml above the git root should NOT be found.
    (tmp_path / ".sciforge.toml").write_text("", encoding="utf-8")
    monkeypatch.chdir(tmp_path / "outer" / "inner")
    assert cio.project_local_config_path() is None
