"""Tests for config resolution (SCIFORGE_CONFIG plumbing, [me] section)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

SKILL_ROOT = Path(__file__).parent.parent
SFME = SKILL_ROOT / "scripts" / "sf-me"


def _run(argv, env):
    return subprocess.run(
        [sys.executable, str(SFME), *argv],
        env=env,
        capture_output=True,
        text=True,
    )


def test_sciforge_config_env_var_is_respected(tmp_path):
    """Two distinct config paths must map to two distinct me dirs.

    Regression guard: if config resolution ever ignores SCIFORGE_CONFIG,
    both `init` calls would clobber the same file (typically the real
    ~/.sciforge/me/me.md).
    """
    me_a = tmp_path / "a" / "me"
    me_b = tmp_path / "b" / "me"
    cfg_a = tmp_path / "cfg_a.toml"
    cfg_b = tmp_path / "cfg_b.toml"
    cfg_a.write_text(f"[me]\ndir = {json.dumps(str(me_a))}\n", encoding="utf-8")
    cfg_b.write_text(f"[me]\ndir = {json.dumps(str(me_b))}\n", encoding="utf-8")

    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"

    env["SCIFORGE_CONFIG"] = str(cfg_a)
    r_a = _run(["init"], env)
    assert r_a.returncode == 0, r_a.stderr

    env["SCIFORGE_CONFIG"] = str(cfg_b)
    r_b = _run(["init"], env)
    assert r_b.returncode == 0, r_b.stderr

    assert (me_a / "me.md").is_file()
    assert (me_b / "me.md").is_file()
    # they are distinct files at distinct paths
    assert me_a != me_b


def test_missing_me_section_uses_defaults(tmp_path, monkeypatch):
    """If the config file has no [me] section, the default dir is used
    (the default is ~/.sciforge/me — but we do NOT want the test to
    touch the real home). We settle for asserting that the CLI does
    not crash and reports a sensible path.
    """
    cfg = tmp_path / "cfg.toml"
    # a totally unrelated section
    cfg.write_text("[library]\npath = './library'\n", encoding="utf-8")

    env = os.environ.copy()
    env["SCIFORGE_CONFIG"] = str(cfg)
    env["PYTHONDONTWRITEBYTECODE"] = "1"

    r = _run(["show"], env)
    # exit 3 is expected: the default ~/.sciforge/me/me.md does not
    # exist in this test host either (or if it does, that user gets
    # their real profile — irrelevant to what we assert). What we
    # actually verify is that config resolution did not blow up with
    # a Python traceback.
    assert r.returncode in (0, 3), (r.stdout, r.stderr)
    assert "Traceback" not in r.stderr
