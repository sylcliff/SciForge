"""Tests for `sf-me init`."""

from __future__ import annotations


def test_init_creates_skeleton(run, me_file):
    r = run("init")
    assert r.returncode == 0, r.stderr
    assert me_file.is_file()
    text = me_file.read_text(encoding="utf-8")
    # skeleton must contain the +++ fences and commented examples for
    # all five sections
    assert text.startswith("+++\n")
    assert "\n+++\n" in text
    for section in ("skill", "equipment", "compute", "preference", "history"):
        assert f"[[{section}]]" in text, f"missing example for {section}"


def test_init_existing_file_refuses(run, me_file):
    r1 = run("init")
    assert r1.returncode == 0
    r2 = run("init")
    assert r2.returncode == 4, (r2.stdout, r2.stderr)
    assert "refusing to overwrite" in r2.stderr
    # file must NOT be touched
    assert me_file.read_text(encoding="utf-8").startswith("+++\n")


def test_init_force_overwrites(run, me_file):
    r1 = run("init")
    assert r1.returncode == 0
    # dirty the file so we can see it get replaced
    me_file.write_text("garbage\n", encoding="utf-8")
    r2 = run("init", "--force")
    assert r2.returncode == 0, r2.stderr
    assert me_file.read_text(encoding="utf-8").startswith("+++\n")
