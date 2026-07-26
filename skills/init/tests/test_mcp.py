"""Tests for mcp.py (Q11 unit, Q4/Q7)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import mcp as mcp_mod  # noqa: E402


def _write_settings(path: Path, servers: dict) -> None:
    payload = {"mcpServers": servers}
    path.write_text(json.dumps(payload), encoding="utf-8")


# ---- registered_servers ----


def test_registered_servers_reads_mcpservers_key(tmp_path: Path) -> None:
    f = tmp_path / "settings.json"
    _write_settings(f, {"scansci-pdf": {"command": "x"}, "brave-search": {"command": "y"}})
    assert mcp_mod.registered_servers(f) == {"scansci-pdf", "brave-search"}


def test_registered_servers_returns_empty_when_missing_file(tmp_path: Path) -> None:
    assert mcp_mod.registered_servers(tmp_path / "nope.json") == set()


def test_registered_servers_returns_empty_when_malformed(tmp_path: Path) -> None:
    f = tmp_path / "bad.json"
    f.write_text("{not valid json", encoding="utf-8")
    assert mcp_mod.registered_servers(f) == set()


def test_registered_servers_reads_nested_shape(tmp_path: Path) -> None:
    """Older Claude Desktop shape: `mcp.servers`."""
    f = tmp_path / "settings.json"
    f.write_text(
        json.dumps({"mcp": {"servers": {"legacy-one": {"cmd": "z"}}}}), encoding="utf-8"
    )
    assert "legacy-one" in mcp_mod.registered_servers(f)


# ---- default settings path ----


def test_default_settings_path_uses_env_var(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("CLAUDE_SETTINGS", str(tmp_path / "custom.json"))
    assert mcp_mod.default_settings_path() == (tmp_path / "custom.json").resolve()


def test_default_settings_path_falls_back_to_home(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CLAUDE_SETTINGS", raising=False)
    p = mcp_mod.default_settings_path()
    # Either ~/.claude.json (Claude Code) or ~/.claude/settings.json (legacy).
    assert p.name in ("settings.json", ".claude.json")


# ---- status ----


def test_status_missing_recommended_first(tmp_path: Path) -> None:
    f = tmp_path / "settings.json"
    _write_settings(f, {})  # nothing registered
    rows = mcp_mod.status(f)
    # Every recommended should be there, all with registered=False and recommended=True
    assert all(not r.registered and r.recommended for r in rows)
    # scansci-pdf and brave-search should be present
    names = {r.name for r in rows}
    assert names >= {"scansci-pdf", "brave-search"}


def test_status_all_registered(tmp_path: Path) -> None:
    f = tmp_path / "settings.json"
    _write_settings(f, {r.name: {"command": "x"} for r in mcp_mod.RECOMMENDED})
    rows = mcp_mod.status(f)
    assert all(r.registered and r.recommended for r in rows)


def test_status_marks_extras_as_registered_not_recommended(tmp_path: Path) -> None:
    f = tmp_path / "settings.json"
    _write_settings(f, {"random-extra": {"command": "x"}})
    rows = mcp_mod.status(f)
    extras = [r for r in rows if r.name == "random-extra"]
    assert extras and extras[0].registered is True and extras[0].recommended is False


# ---- recommended list ----


def test_recommended_list_matches_by_name() -> None:
    for r in mcp_mod.RECOMMENDED:
        assert mcp_mod.by_name(r.name) is r


def test_by_name_unknown_returns_none() -> None:
    assert mcp_mod.by_name("does-not-exist") is None


def test_all_recommended_have_install_and_reason() -> None:
    for r in mcp_mod.RECOMMENDED:
        assert r.install_cmd, f"missing install_cmd for {r.name}"
        assert r.why, f"missing rationale for {r.name}"
        assert r.needed_by, f"missing needed_by for {r.name}"
