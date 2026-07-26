"""Secret handling for sf-init (Q9).

Two responsibilities:

1. **Detect** existing env-var overrides for each secret key so the wizard
   can display ``[from env]`` and skip re-asking.

2. **Append** ``.sciforge.toml`` / ``sciforge/`` to the nearest
   ``.gitignore`` on first run inside a git repo. Idempotent — running
   twice does not stack duplicate lines.

Never writes secrets to disk itself; that's ``config_io.set_value(..., secret=True)``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


# --------------------------------------------------------------------------- #
# The 3 secret keys the wizard knows about (Q9).
# Each maps a dotted config path to the env var that overrides it.
# The env var names mirror the ones the downstream skills already read.
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class SecretKey:
    dotted: str           # dotted path in the TOML doc
    env_var: str          # environment variable name
    label: str            # human label shown in the wizard / doctor
    reason: str           # one-liner: what breaks without it


SECRET_KEYS: list[SecretKey] = [
    SecretKey(
        dotted="download.semanticscholar_api_key",
        env_var="SCIFORGE_S2_API_KEY",
        label="Semantic Scholar API key",
        reason="S2 rate-limits anonymous callers to ~100 requests / 5 min shared globally.",
    ),
    SecretKey(
        dotted="sources.github.token",
        env_var="GITHUB_TOKEN",
        label="GitHub token",
        reason="sf-search's GitHub source is disabled without a token.",
    ),
    SecretKey(
        dotted="sources.pubmed.api_key",
        env_var="NCBI_API_KEY",
        label="NCBI / PubMed API key",
        reason="Raises PubMed rate limit from 3 to 10 req/s.",
    ),
]


def env_value(key: SecretKey) -> str | None:
    """Return the env var value if present and non-empty, else ``None``."""
    v = os.environ.get(key.env_var)
    return v if v else None


def by_dotted(dotted: str) -> SecretKey | None:
    for k in SECRET_KEYS:
        if k.dotted == dotted:
            return k
    return None


# --------------------------------------------------------------------------- #
# gitignore append
# --------------------------------------------------------------------------- #


# Two entries — both cover shapes we recommend elsewhere.
GITIGNORE_ENTRIES = [".sciforge.toml", "sciforge/"]
GITIGNORE_MARKER = "# SciForge"


def _find_git_root(start: Path | None = None) -> Path | None:
    """Return the nearest ancestor of ``start`` (default cwd) containing
    ``.git/``, or ``None`` if we're not in a repo."""
    cwd = (start or Path.cwd()).resolve()
    for candidate in [cwd] + list(cwd.parents):
        if (candidate / ".git").exists():
            return candidate
    return None


def append_gitignore(start: Path | None = None) -> tuple[Path | None, list[str]]:
    """Append the two SciForge entries to the git root's ``.gitignore``.

    Returns ``(gitignore_path, added_entries)``. When we're not inside a
    git repo, returns ``(None, [])``. When every entry is already present,
    returns ``(gitignore_path, [])``.
    """
    root = _find_git_root(start)
    if root is None:
        return None, []

    gi = root / ".gitignore"
    existing_lines: list[str] = []
    if gi.is_file():
        existing_lines = gi.read_text(encoding="utf-8").splitlines()

    # A line "matches" an entry when, ignoring surrounding whitespace, it
    # equals the entry exactly. gitignore is otherwise permissive (patterns
    # can look many ways) — we do the strict-equality check on purpose so
    # we don't hallucinate a match on something more specific.
    existing_set = {line.strip() for line in existing_lines}
    to_add = [e for e in GITIGNORE_ENTRIES if e not in existing_set]
    if not to_add:
        return gi, []

    # Compose new block. Add a leading blank line + marker header when the
    # existing file doesn't already end cleanly.
    new_lines = list(existing_lines)
    if new_lines and new_lines[-1].strip() != "":
        new_lines.append("")
    new_lines.append(GITIGNORE_MARKER)
    new_lines.extend(to_add)
    text = "\n".join(new_lines).rstrip("\n") + "\n"
    gi.write_text(text, encoding="utf-8")
    return gi, to_add


def is_git_repo(start: Path | None = None) -> bool:
    return _find_git_root(start) is not None


# --------------------------------------------------------------------------- #
# Helpers for the wizard
# --------------------------------------------------------------------------- #


def format_export_line(env_var: str, value: str) -> str:
    """Shell-safe export line for the user to paste into their rc.

    We deliberately don't quote inside the value — API keys are ASCII-safe.
    If they aren't, the user should quote it themselves.
    """
    return f'export {env_var}="{value}"'


def prompt_choices() -> Iterable[str]:
    """The 3 storage options offered per secret. Order matters — first is
    the default when the user hits Enter."""
    return ("env", "file", "skip")


__all__ = [
    "GITIGNORE_ENTRIES",
    "SECRET_KEYS",
    "SecretKey",
    "append_gitignore",
    "by_dotted",
    "env_value",
    "format_export_line",
    "is_git_repo",
    "prompt_choices",
]
