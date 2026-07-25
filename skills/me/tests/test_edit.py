"""Tests for `sf-me edit`."""

from __future__ import annotations

import sys


def test_edit_missing_file_exits_3(run):
    r = run("edit")
    assert r.returncode == 3
    assert "not found" in r.stderr


def test_edit_launches_editor_and_reports_exit_code(run, meenv):
    _, env = meenv
    # init so me.md exists
    r_init = run("init")
    assert r_init.returncode == 0

    # a no-op editor: python -c pass. use quotes so shlex keeps -c and pass together.
    env["EDITOR"] = f'"{sys.executable}" -c pass'
    r = run("edit")
    assert r.returncode == 0, (r.stdout, r.stderr)


def test_edit_editor_nonzero_bubbles_up(run, meenv):
    _, env = meenv
    r_init = run("init")
    assert r_init.returncode == 0

    # editor exits 7 → CLI should surface exit 1
    env["EDITOR"] = f'"{sys.executable}" -c "import sys; sys.exit(7)"'
    r = run("edit")
    assert r.returncode == 1, (r.stdout, r.stderr)
    assert "editor exited" in r.stderr


def test_edit_no_editor_available_exits_3(run, meenv):
    _, env = meenv
    r_init = run("init")
    assert r_init.returncode == 0
    # blank out every editor-y env var so the resolver has nothing.
    # We also blank PATH so no fallback like `nano` / `notepad` resolves.
    env.pop("EDITOR", None)
    env.pop("VISUAL", None)
    env["PATH"] = ""
    r = run("edit")
    assert r.returncode == 3, (r.stdout, r.stderr)
    assert "no editor" in r.stderr
