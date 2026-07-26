"""Snapshot tests for doctor rendering across the 4 sections.

We capture stdout for a minimal in-memory config + faked network/env/mcp
inputs, then assert on stable substrings rather than exact byte-match —
terminal width differs across CI runners and would break byte-snapshots
without adding real value.
"""

from __future__ import annotations

import io
import sys
from contextlib import redirect_stdout
from pathlib import Path

import pytest
import tomlkit

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import doctor  # noqa: E402


def _render_to_str(local, network=None, *, env=None, mcp=None) -> str:
    buf = io.StringIO()
    with redirect_stdout(buf):
        doctor.render_all(local, network, env=env, mcp=mcp)
    return buf.getvalue()


def _row(status: str, key: str, value: str, fix: str | None = None) -> doctor.Row:
    return doctor.Row(status=status, key=key, value=value, fix=fix)


# ---- section headers ----


def test_render_all_shows_config_section_only_by_default() -> None:
    out = _render_to_str([_row("ok", "polite_email", "a@b.c")])
    assert "Config values" in out
    assert "Python environment" not in out
    assert "MCP servers" not in out
    assert "Reachability" not in out


def test_render_all_shows_env_section_when_provided() -> None:
    out = _render_to_str([], env=[_row("ok", "env.kind", "conda")])
    assert "Python environment" in out
    assert "env.kind" in out


def test_render_all_shows_mcp_section_when_provided() -> None:
    out = _render_to_str([], mcp=[_row("warn", "mcp scansci-pdf", "not registered", fix="install...")])
    assert "MCP servers" in out
    assert "scansci-pdf" in out


def test_render_all_shows_network_section_when_provided() -> None:
    out = _render_to_str([], [_row("ok", "reachability arxiv", "150ms")])
    assert "Reachability" in out


def test_render_all_section_order() -> None:
    """Config → Env → MCP → Reachability."""
    out = _render_to_str(
        [_row("ok", "a", "1")],
        [_row("ok", "reachability x", "1ms")],
        env=[_row("ok", "env.kind", "conda")],
        mcp=[_row("ok", "mcp scansci-pdf", "registered")],
    )
    i_config = out.index("Config values")
    i_env = out.index("Python environment")
    i_mcp = out.index("MCP servers")
    i_net = out.index("Reachability")
    assert i_config < i_env < i_mcp < i_net


# ---- summarize ----


def test_summarize_counts_across_sections() -> None:
    local = [_row("ok", "a", "1"), _row("warn", "b", "2")]
    env = [_row("fail", "env.python", "missing")]
    mcp = [_row("ok", "mcp x", "registered")]
    network = [_row("warn", "reachability arxiv", "timeout")]
    ok, warn, fail = doctor.summarize(local, network, env=env, mcp=mcp)
    assert (ok, warn, fail) == (2, 2, 1)


# ---- fix line indentation ----


def test_fix_line_uses_arrow_and_indent() -> None:
    row = _row("warn", "polite_email", "unset", fix="Run sf-init.")
    out = _render_to_str([row])
    assert "→ Run sf-init." in out
    # Fix line is indented after the key column.
    fix_line = [l for l in out.splitlines() if "Run sf-init." in l][0]
    assert fix_line.startswith(" ")


# ---- env_checks integration ----


def test_env_checks_no_record_warns() -> None:
    doc = tomlkit.document()
    rows = doctor.env_checks(doc)
    kinds = [r.key for r in rows]
    assert "env.record" in kinds
    assert rows[0].status == "warn"


def test_env_checks_missing_python_is_fail(tmp_path: Path) -> None:
    from config_io import record_env
    doc = tomlkit.document()
    record_env(doc, kind="venv", name=str(tmp_path), python=str(tmp_path / "nope.exe"))
    rows = doctor.env_checks(doc)
    fails = [r for r in rows if r.status == "fail"]
    assert fails, "expected env.python row to be ✗ when file missing"


# ---- mcp_checks integration ----


def test_mcp_checks_empty_registry_flags_recommended(tmp_path: Path) -> None:
    settings = tmp_path / "settings.json"
    settings.write_text('{"mcpServers": {}}', encoding="utf-8")
    rows = doctor.mcp_checks(settings_path=settings)
    warns = [r for r in rows if r.status == "warn"]
    assert warns, "expected at least one recommended MCP missing"
