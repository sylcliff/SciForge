"""Tests for `sf-me show` — human view + --json contract + failure paths."""

from __future__ import annotations

import json

POPULATED = """\
+++
[[skill]]
name  = "pytorch-distributed"
short = "Used DDP to train a 7B model"
level = "proficient"

[[skill]]
name  = "nmr-analysis"
short = "Reads 1D and 2D NMR spectra"

[[compute]]
name  = "gpu-a100-cluster"
short = "8x A100 80GB, group-shared"

[[history]]
name  = "gnn-drug-discovery"
short = "2023-2025, GNNs for compound screening"
period = "2023-2025"
+++

# Notes

Some free-form thoughts here.
"""


# ---- happy paths -----------------------------------------------------


def test_show_default_equals_self(run):
    run("init")
    r1 = run("show")
    r2 = run("show", "self")
    assert r1.returncode == 0
    assert r2.returncode == 0
    assert r1.stdout == r2.stdout


def test_show_human_shows_empty_sections(run):
    run("init")
    r = run("show")
    assert r.returncode == 0, r.stderr
    # every section is present, all empty on the fresh skeleton
    for label in ("Skills", "Equipment", "Compute", "Preferences", "History"):
        assert f"## {label} (0)  (empty)" in r.stdout


def test_show_human_populated(run, me_file):
    run("init")
    me_file.write_text(POPULATED, encoding="utf-8")
    r = run("show")
    assert r.returncode == 0, r.stderr
    assert "## Skills (2)" in r.stdout
    assert "pytorch-distributed" in r.stdout
    assert "## Equipment (0)  (empty)" in r.stdout
    assert "## Compute (1)" in r.stdout
    assert "gpu-a100-cluster" in r.stdout
    assert "## History (1)" in r.stdout
    # body must NOT be rendered
    assert "Some free-form thoughts" not in r.stdout


# ---- --json contract -------------------------------------------------


def test_show_json_contract_keys(run):
    run("init")
    r = run("show", "--json")
    assert r.returncode == 0, r.stderr
    payload = json.loads(r.stdout)
    # top-level keys are exactly the ADR-0006 envelope + data
    assert set(payload.keys()) == {"id", "type", "uri", "data"}
    assert payload["id"] == "self"
    assert payload["type"] == "me"
    assert payload["uri"] == "sciforge://me/self"


def test_show_json_data_has_all_five_sections(run):
    run("init")
    r = run("show", "--json")
    payload = json.loads(r.stdout)
    assert set(payload["data"].keys()) == {
        "skill",
        "equipment",
        "compute",
        "preference",
        "history",
    }
    # all arrays, all empty on the fresh skeleton
    for v in payload["data"].values():
        assert isinstance(v, list)
        assert v == []


def test_show_json_populated_passthrough(run, me_file):
    run("init")
    me_file.write_text(POPULATED, encoding="utf-8")
    r = run("show", "--json")
    assert r.returncode == 0, r.stderr
    payload = json.loads(r.stdout)
    skill_names = [e["name"] for e in payload["data"]["skill"]]
    assert skill_names == ["pytorch-distributed", "nmr-analysis"]
    # optional field survives round-trip verbatim
    assert payload["data"]["skill"][0]["level"] == "proficient"
    assert payload["data"]["history"][0]["period"] == "2023-2025"


# ---- failure paths ---------------------------------------------------


def test_show_unknown_id_exits_3(run):
    run("init")
    r = run("show", "foo")
    assert r.returncode == 3
    assert "unknown id" in r.stderr


def test_show_missing_file_exits_3(run):
    # no init → me.md does not exist
    r = run("show")
    assert r.returncode == 3
    assert "not found" in r.stderr


def test_show_broken_toml_exits_2(run, me_file):
    run("init")
    # write malformed TOML front-matter (missing closing quote)
    me_file.write_text(
        '+++\n[[skill]]\nname = "unterminated\nshort = "x"\n+++\n',
        encoding="utf-8",
    )
    r = run("show", "--json")
    assert r.returncode == 2, (r.stdout, r.stderr)
    assert "invalid TOML" in r.stderr or "TOML" in r.stderr


def test_show_duplicate_name_in_section_exits_2(run, me_file):
    run("init")
    me_file.write_text(
        "+++\n"
        "[[skill]]\nname = \"dup\"\nshort = \"first\"\n"
        "[[skill]]\nname = \"dup\"\nshort = \"second\"\n"
        "+++\n",
        encoding="utf-8",
    )
    r = run("show", "--json")
    assert r.returncode == 2
    assert "duplicate name" in r.stderr


def test_show_missing_required_field_exits_2(run, me_file):
    run("init")
    # `short` missing on the entry
    me_file.write_text(
        "+++\n[[skill]]\nname = \"x\"\n+++\n",
        encoding="utf-8",
    )
    r = run("show", "--json")
    assert r.returncode == 2
    assert "short" in r.stderr
