"""Shared fixtures for the sf-me test suite.

Every test runs against a fresh temp directory. ``SCIFORGE_CONFIG``
is pointed at a per-test config.toml whose ``[me] dir`` sits under
``tmp_path`` — so tests never touch the real ``~/.sciforge/me/``.

Mirrors the pattern in ``skills/literature/tests/conftest.py``.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SKILL_ROOT = Path(__file__).parent.parent
SFME = SKILL_ROOT / "scripts" / "sf-me"


@pytest.fixture
def meenv(tmp_path: Path):
    """Fresh me dir + config pointing at it.

    Yields ``(me_dir, env_dict)`` — pass ``env_dict`` to subprocess.run.
    """
    me_dir = tmp_path / "me"
    cfg = tmp_path / "config.toml"
    # Use json.dumps to safely quote Windows-style paths in TOML.
    cfg.write_text(
        "[me]\n"
        f"dir = {json.dumps(str(me_dir))}\n",
        encoding="utf-8",
    )
    env = os.environ.copy()
    env["SCIFORGE_CONFIG"] = str(cfg)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    yield me_dir, env


def _run(argv, env=None, stdin=None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SFME), *argv],
        env=env,
        capture_output=True,
        text=True,
        input=stdin,
    )


@pytest.fixture
def run(meenv):
    """Return a ``run(*argv)`` helper bound to the fixture env."""
    _, env = meenv

    def _do(*argv, stdin=None) -> subprocess.CompletedProcess:
        return _run(list(argv), env=env, stdin=stdin)

    return _do


@pytest.fixture
def me_file(meenv) -> Path:
    """The resolved me.md path (may or may not exist yet)."""
    me_dir, _ = meenv
    return me_dir / "me.md"
