"""Parse and describe the me.md file.

The file is a Hugo-style TOML front-matter document:

    +++
    [[skill]]
    name  = "..."
    short = "..."
    ...
    +++

    <free-form Markdown body>

This module owns all knowledge of that shape — the fence marker, the
five valid section names, the required per-entry fields, and the
error taxonomy. Callers get either a validated ``Profile`` dict or a
``ProfileError`` with an exit code baked in.

Stdlib-only; parsing goes through ``tomllib``.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

FENCE = "+++"

SECTIONS: tuple[str, ...] = (
    "skill",
    "equipment",
    "compute",
    "preference",
    "history",
)

REQUIRED_FIELDS: tuple[str, ...] = ("name", "short")


@dataclass
class ProfileError(Exception):
    """A parse or schema error with an ADR-0006 exit code attached."""

    message: str
    exit_code: int  # 2 = bad input, 3 = not found

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.message


def split_front_matter(text: str) -> tuple[str, str]:
    """Split ``me.md`` text into (front_matter_toml, body).

    Rules:
      - The file MUST start with a ``+++`` line (leading whitespace
        allowed but discouraged).
      - A second ``+++`` line closes the front matter.
      - Everything after the second fence is body (may be empty).

    Raises ProfileError (exit 2) if the fences are missing or unbalanced.
    """
    lines = text.splitlines(keepends=True)
    # find the opening fence
    open_idx: int | None = None
    for i, ln in enumerate(lines):
        stripped = ln.strip()
        if stripped == "":
            continue
        if stripped == FENCE:
            open_idx = i
            break
        # first non-blank line is not a fence → malformed
        raise ProfileError(
            "me.md must begin with a '+++' TOML front-matter fence",
            exit_code=2,
        )
    if open_idx is None:
        raise ProfileError(
            "me.md must begin with a '+++' TOML front-matter fence",
            exit_code=2,
        )

    # find the closing fence
    close_idx: int | None = None
    for j in range(open_idx + 1, len(lines)):
        if lines[j].strip() == FENCE:
            close_idx = j
            break
    if close_idx is None:
        raise ProfileError(
            "me.md front-matter is not closed by a matching '+++' fence",
            exit_code=2,
        )

    front = "".join(lines[open_idx + 1 : close_idx])
    body = "".join(lines[close_idx + 1 :])
    return front, body


def parse_front_matter(front: str) -> dict:
    """Parse TOML front-matter into a raw dict.

    Raises ProfileError (exit 2) on TOML syntax errors.
    """
    try:
        return tomllib.loads(front)
    except tomllib.TOMLDecodeError as e:
        raise ProfileError(
            f"invalid TOML in me.md front-matter: {e}",
            exit_code=2,
        ) from e


def normalise_sections(raw: dict) -> dict[str, list[dict]]:
    """Return a dict with all five sections present as lists.

    - Missing sections become ``[]``.
    - Every entry must be a table (TOML dict) — non-dict entries
      raise ProfileError (exit 2).
    - Every entry must have ``name`` and ``short`` (strings).
    - Duplicate ``name`` within a section is an error (exit 2).
    - Unknown top-level keys are ignored, not rejected — the front
      matter may carry non-section metadata in the future.
    """
    out: dict[str, list[dict]] = {s: [] for s in SECTIONS}
    for section in SECTIONS:
        val = raw.get(section)
        if val is None:
            continue
        if not isinstance(val, list):
            raise ProfileError(
                f"section '{section}' must be a TOML array of tables",
                exit_code=2,
            )
        seen: set[str] = set()
        for idx, entry in enumerate(val):
            if not isinstance(entry, dict):
                raise ProfileError(
                    f"section '{section}'[{idx}] must be a table",
                    exit_code=2,
                )
            for req in REQUIRED_FIELDS:
                if req not in entry:
                    raise ProfileError(
                        f"section '{section}'[{idx}] is missing required "
                        f"field '{req}'",
                        exit_code=2,
                    )
                if not isinstance(entry[req], str) or not entry[req].strip():
                    raise ProfileError(
                        f"section '{section}'[{idx}].{req} must be a "
                        f"non-empty string",
                        exit_code=2,
                    )
            name = entry["name"]
            if name in seen:
                raise ProfileError(
                    f"duplicate name in section '{section}': {name}",
                    exit_code=2,
                )
            seen.add(name)
            out[section].append(entry)
    return out


def load_profile(path: Path) -> dict[str, list[dict]]:
    """Read me.md from disk, return normalised sections.

    Raises ProfileError(exit 3) if the file is missing, and
    ProfileError(exit 2) for any schema or TOML problem.
    """
    if not path.is_file():
        raise ProfileError(
            f"me.md not found at {path}. Run 'sf-me init' to create it.",
            exit_code=3,
        )
    text = path.read_text(encoding="utf-8")
    front, _body = split_front_matter(text)
    raw = parse_front_matter(front)
    return normalise_sections(raw)


# ---- skeleton generation ---------------------------------------------


SKELETON = """\
+++
# Every entry needs `name` (a lowercase slug) and `short` (a one-line
# description). Anything else is optional — sf-me does not validate
# extra keys. Uncomment the examples below to get started, then run
# `sf-me edit` to keep filling them in.

# --- skill: what you can do ---
# [[skill]]
# name  = "pytorch-distributed"
# short = "Trained a 7B model with DDP"
# # optional: level = "proficient"
# # optional: updated = "2026-07-25"
# # optional: evidence = "paper X, project Y"

# --- equipment: wet-lab / instrument access ---
# [[equipment]]
# name  = "nmr-400"
# short = "Shared 400MHz NMR, booking required"
# # optional: access = "shared"

# --- compute: GPUs, clusters, cloud quotas ---
# [[compute]]
# name  = "gpu-a100-cluster"
# short = "8x A100 80GB, group-shared, queue-based"
# # optional: kind = "cluster"
# # optional: scale = "8x A100 80GB"
# # optional: access = "shared"

# --- preference: taste, risk appetite, hard nos ---
# [[preference]]
# name  = "high-risk-methods"
# short = "OK with 3-6 month research bets"
# # optional: stance = "prefer"

# --- history: past projects, publications, roles ---
# [[history]]
# name  = "gnn-drug-discovery"
# short = "2023-2025, GNNs for compound screening"
# # optional: period = "2023-2025"
+++

# Notes

<!--
Free-form Markdown below. This section is NOT part of `sf-me show
--json`; it is for your own reflection and for LLM-driven readers
that want more context than the one-line `short` fields provide.

Suggested uses:
- Cross-entry reflection ("the last two years I have shifted from X to Y")
- Career-stage thoughts
- Methodological taste, red lines, aspirations
- References to other SciForge entities via `sciforge://` URIs
-->
"""


def write_skeleton(path: Path) -> None:
    """Write the initial me.md at ``path``. Parent dirs are created.

    Caller is responsible for the exists/force check.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(SKELETON, encoding="utf-8")
