"""MCP registration probe for sf-init (Q4, Q7).

`sf-init mcp` reads Claude's ``~/.claude/settings.json`` to find registered
MCP servers, then compares against a hard-coded recommendation list stored
alongside the SciForge init skill.

Never edits ``settings.json`` — Q4 (C): reporting + copy-paste commands only.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

# --------------------------------------------------------------------------- #
# Recommended MCP list (Q7 / a) — hard-coded here so we don't hunt across
# every skill directory yet. When SciForge grows past ~10 skills this should
# move to a per-skill declaration.
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class RecommendedMCP:
    name: str          # server name as Claude's settings.json would register it
    needed_by: list[str]   # SciForge skills that benefit
    why: str           # one-liner
    install_cmd: str   # copy-paste command


RECOMMENDED: list[RecommendedMCP] = [
    RecommendedMCP(
        name="scansci-pdf",
        needed_by=["download"],
        why=(
            "Fallback fetch for paywalled papers via Sci-Hub / LibGen / WebVPN. "
            "sf-download uses only public APIs; scansci-pdf covers what "
            "Unpaywall can't."
        ),
        install_cmd=(
            "pipx install scansci-pdf "
            '&& claude mcp add scansci-pdf -- scansci-pdf serve'
        ),
    ),
    RecommendedMCP(
        name="brave-search",
        needed_by=["search"],
        why=(
            "General web / news search when academic sources miss context "
            "(policy briefs, press releases, forum threads)."
        ),
        install_cmd=(
            "# Get a free key at https://brave.com/search/api/\n"
            "# then:  claude mcp add brave-search -e BRAVE_API_KEY=<key> "
            "-- npx -y @modelcontextprotocol/server-brave-search"
        ),
    ),
]


def recommended_names() -> list[str]:
    return [r.name for r in RECOMMENDED]


def by_name(name: str) -> RecommendedMCP | None:
    for r in RECOMMENDED:
        if r.name == name:
            return r
    return None


# --------------------------------------------------------------------------- #
# Registry read (Q7 / α)
# --------------------------------------------------------------------------- #


def default_settings_path() -> Path:
    """Return the conventional path to Claude Code's MCP registry file.

    Claude Code stores its user-level ``mcpServers`` map in ``~/.claude.json``
    (top-level file, not ``~/.claude/settings.json`` — the latter holds
    permission / env / plugin settings but not MCP registrations).

    Users with an atypical layout can override via ``$CLAUDE_SETTINGS`` env
    or ``--claude-settings`` flag on the CLI.
    """
    override = os.environ.get("CLAUDE_SETTINGS")
    if override:
        return Path(override).expanduser().resolve()
    # Preferred (Claude Code current): ~/.claude.json
    p = Path.home() / ".claude.json"
    if p.is_file():
        return p
    # Legacy fallback (Claude Desktop / older layouts): ~/.claude/settings.json
    return Path.home() / ".claude" / "settings.json"


def _read_json(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        # Doctor renders a ⚠ row; we don't crash the whole run.
        return {}


def registered_servers(settings_path: Path | None = None) -> set[str]:
    """Return the set of MCP server names Claude Code has registered.

    Reads the ``mcpServers`` top-level key (Claude Code format) and merges
    with ``mcp.servers`` (Claude Desktop older format). Missing / malformed
    files return an empty set — never raises.
    """
    settings = _read_json(settings_path or default_settings_path())
    names: set[str] = set()
    for key in ("mcpServers",):
        v = settings.get(key)
        if isinstance(v, dict):
            names.update(v.keys())
    # Nested mcp.servers shape (rare in current builds; kept for parity)
    mcp = settings.get("mcp")
    if isinstance(mcp, dict):
        v = mcp.get("servers")
        if isinstance(v, dict):
            names.update(v.keys())
    return names


# --------------------------------------------------------------------------- #
# Report / status
# --------------------------------------------------------------------------- #


@dataclass
class MCPStatus:
    name: str
    registered: bool
    recommended: bool
    reason: str = ""
    install_cmd: str = ""


def status(settings_path: Path | None = None) -> list[MCPStatus]:
    """Merge the registered set with the recommended list into a sortable
    list of status rows. Order:

      1. Recommended-and-missing (rendered as ``⚠`` — action needed)
      2. Recommended-and-registered (``✓``)
      3. Registered-but-not-recommended (``✓`` with `` extra`` note)
    """
    reg = registered_servers(settings_path)
    rec_names = {r.name for r in RECOMMENDED}

    rows: list[MCPStatus] = []
    # Missing recommended first
    for r in RECOMMENDED:
        if r.name not in reg:
            rows.append(
                MCPStatus(
                    name=r.name,
                    registered=False,
                    recommended=True,
                    reason=r.why,
                    install_cmd=r.install_cmd,
                )
            )
    # Registered recommended
    for r in RECOMMENDED:
        if r.name in reg:
            rows.append(
                MCPStatus(name=r.name, registered=True, recommended=True)
            )
    # Extras — registered but not in our list. Purely informational.
    for name in sorted(reg - rec_names):
        rows.append(MCPStatus(name=name, registered=True, recommended=False))
    return rows


__all__ = [
    "MCPStatus",
    "RECOMMENDED",
    "RecommendedMCP",
    "by_name",
    "default_settings_path",
    "recommended_names",
    "registered_servers",
    "status",
]
