"""End-to-end tests for sf-init in non-interactive mode.

Exercises the full CLI: parse args → wizard.apply_non_interactive →
config_io.atomic_write → doctor rendering. Uses ``--skip-network`` so
tests don't touch the internet.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
import tomlkit


HERE = Path(__file__).resolve().parents[1]
SF_INIT = HERE / "scripts" / "sf-init"


def _run(args: list[str], **env_over) -> subprocess.CompletedProcess[str]:
    """Run sf-init with a clean environment (no user config leakage)."""
    import os
    env = os.environ.copy()
    # Strip anything that would interfere
    for k in list(env):
        if k.startswith("SCIFORGE_") or k in ("GITHUB_TOKEN", "NCBI_API_KEY", "XDG_CONFIG_HOME"):
            env.pop(k, None)
    env.update(env_over)
    return subprocess.run(
        [sys.executable, str(SF_INIT), *args],
        capture_output=True,
        text=True,
        env=env,
    )


# ---- --non-interactive happy path ----


def test_non_interactive_writes_valid_toml(tmp_path: Path) -> None:
    target = tmp_path / "cfg.toml"
    r = _run(
        [
            "--non-interactive",
            "--skip-network",
            "--config-path",
            str(target),
            "--email",
            "user@example.com",
            "--library",
            str(tmp_path / "lib"),
        ]
    )
    assert r.returncode in (0, 2), r.stderr  # 2 allowed: warns present, no fails
    assert target.is_file(), f"config not written; stdout={r.stdout}\nstderr={r.stderr}"
    doc = tomlkit.parse(target.read_text(encoding="utf-8"))
    assert doc["download"]["polite_email"] == "user@example.com"
    assert doc["library"]["path"].endswith("lib")


def test_non_interactive_writes_init_section(tmp_path: Path) -> None:
    target = tmp_path / "cfg.toml"
    _run(
        [
            "--non-interactive",
            "--skip-network",
            "--config-path",
            str(target),
            "--email",
            "user@example.com",
            "--library",
            str(tmp_path / "lib"),
        ]
    )
    doc = tomlkit.parse(target.read_text(encoding="utf-8"))
    assert "init" in doc
    assert str(doc["init"]["version"]) == "1"
    # Skipped optional keys land here
    assert "download.semanticscholar_api_key" in doc["init"]["skipped_keys"]


def test_non_interactive_missing_email_fails(tmp_path: Path) -> None:
    target = tmp_path / "cfg.toml"
    r = _run(
        [
            "--non-interactive",
            "--skip-network",
            "--config-path",
            str(target),
            "--library",
            str(tmp_path / "lib"),
        ]
    )
    assert r.returncode != 0
    assert "email" in r.stderr.lower()


def test_backup_created_on_second_run(tmp_path: Path) -> None:
    target = tmp_path / "cfg.toml"
    args = [
        "--non-interactive",
        "--skip-network",
        "--config-path",
        str(target),
        "--email",
        "u@e.com",
        "--library",
        str(tmp_path / "lib"),
    ]
    _run(args)
    _run(args)  # second write creates a backup of the first
    backups = list(tmp_path.glob("cfg.toml.bak-*"))
    assert len(backups) == 1


# ---- --print-config ----


def test_print_config_redacts_secrets(tmp_path: Path) -> None:
    target = tmp_path / "cfg.toml"
    # Seed a config with a secret in it
    _run(
        [
            "--non-interactive",
            "--skip-network",
            "--config-path",
            str(target),
            "--email",
            "u@e.com",
            "--library",
            str(tmp_path / "lib"),
            "--s2-key",
            "top_secret_value",
        ]
    )
    r = _run(["--print-config", "--config-path", str(target)])
    assert r.returncode == 0
    assert "top_secret_value" not in r.stdout
    assert "<redacted>" in r.stdout


def test_print_config_missing_file(tmp_path: Path) -> None:
    r = _run(["--print-config", "--config-path", str(tmp_path / "missing.toml")])
    assert r.returncode == 1
    assert "No config file found" in r.stdout


# ---- doctor sub-verb ----


def test_doctor_runs_on_seeded_config(tmp_path: Path) -> None:
    target = tmp_path / "cfg.toml"
    _run(
        [
            "--non-interactive",
            "--skip-network",
            "--config-path",
            str(target),
            "--email",
            "u@e.com",
            "--library",
            str(tmp_path / "lib"),
        ]
    )
    r = _run(["doctor", "--skip-network", "--config-path", str(target)])
    assert r.returncode in (0, 2)
    assert "polite_email" in r.stdout
    assert "library.path" in r.stdout
    # No network probes when --skip-network
    assert "Reachability" not in r.stdout


def test_doctor_missing_file_uses_defaults(tmp_path: Path) -> None:
    r = _run(["doctor", "--skip-network", "--config-path", str(tmp_path / "no.toml")])
    # We report failures because polite_email + library.path are unset
    assert "unset" in r.stdout
    assert r.returncode in (0, 2)
