"""Shared fixtures for the literature skill test suite.

The fixtures set up a temporary library rooted under ``tmp_path``, wire
``SCIFORGE_CONFIG`` at a config.toml that points at it, and install
"fake" MinerU / Docling launchers so the ingest→convert→search chain
can be exercised without any real converter binaries.

Fake converters live in ``tests/fixtures/`` as Python scripts. They
mimic each converter's actual output shape:

- ``fake_mineru.py`` — writes ``<stem>.md`` + ``<stem>_content_list.json``
  under ``<out>/<stem>/auto/`` with heading blocks (text_level=2/3),
  paragraphs, a table block, and page_idx.
- ``fake_docling.py`` — writes a single ``<stem>.md`` at the top of
  ``<out>/``.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SKILL_ROOT = Path(__file__).parent.parent
LITLIB = SKILL_ROOT / "scripts" / "sf-lit"
FIXTURES = Path(__file__).parent / "fixtures"


# ---- fake converters ---------------------------------------------------


@pytest.fixture(scope="session")
def fake_mineru() -> Path:
    return FIXTURES / "fake_mineru.py"


@pytest.fixture(scope="session")
def fake_docling() -> Path:
    return FIXTURES / "fake_docling.py"


# ---- library / config --------------------------------------------------


@pytest.fixture
def libenv(tmp_path: Path, fake_mineru: Path, fake_docling: Path):
    """Fresh library + config pointing at it + fake converters wired in.

    Yields ``(lib_path, env_dict)`` — ``env_dict`` is what should be
    passed to subprocess.run(env=...).
    """
    lib = tmp_path / "lib"
    cfg = tmp_path / "config.toml"
    py = sys.executable
    # TOML strings need proper escaping for Windows paths; json.dumps is
    # the simplest way to get a compliant quoted string.
    cfg.write_text(
        "\n".join([
            "[library]",
            f"path = {json.dumps(str(lib))}",
            "",
            "[converter]",
            'default = "mineru"',
            "",
            "[converter.mineru]",
            f"command = {json.dumps(f'{py} {fake_mineru}')}",
            'env = "LITLIB_MINERU_BIN"',
            "",
            "[converter.docling]",
            f"command = {json.dumps(f'{py} {fake_docling}')}",
            'env = "LITLIB_DOCLING_BIN"',
            "",
        ]),
        encoding="utf-8",
    )
    env = os.environ.copy()
    env["SCIFORGE_CONFIG"] = str(cfg)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    # Bootstrap the library.
    r = _run(["init"], env=env)
    assert r.returncode == 0, r.stderr
    yield lib, env


# ---- helpers -----------------------------------------------------------


def _run(argv, env=None, stdin=None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(LITLIB), *argv],
        env=env,
        capture_output=True,
        text=True,
        input=stdin,
    )


def parse_kv(out: str) -> dict[str, str]:
    return dict(
        line.split("=", 1)
        for line in out.strip().splitlines()
        if "=" in line
    )


def make_pdf(path: Path, content: bytes = b"%PDF-1.4 fake") -> Path:
    path.write_bytes(content)
    return path


@pytest.fixture
def run(libenv):
    """Return a ``run(*argv, stdin=None)`` helper bound to the fixture env."""
    _, env = libenv

    def _do(*argv, stdin=None) -> subprocess.CompletedProcess:
        return _run(list(argv), env=env, stdin=stdin)

    return _do
