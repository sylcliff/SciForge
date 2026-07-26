"""Unit + integration tests for env.py.

Layers (Q11 / C):

* Unit (mocked subprocess): extras resolution, detection ordering, activate
  hints, rollback semantics, TOML record I/O.
* Integration (real subprocess): creates a real ``.venv`` in tmp_path,
  installs a tiny known package, verifies check_packages_installed. Kept
  small (< ~15 s) so CI can run it.
* Conda: gated on ``needs_conda`` mark; run locally with
  ``pytest -m needs_conda`` when you actually want to hit it.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
import tomlkit

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import config_io as cio  # noqa: E402
import env as env_mod  # noqa: E402


# ---- extras resolution ----


def test_resolve_extras_core_group() -> None:
    result = env_mod.resolve_extras(["converters"])
    assert "mineru" in result
    assert "docling" in result


def test_resolve_extras_all_alias() -> None:
    """`all` should union every non-reserved group and never contain 'all'."""
    result = env_mod.resolve_extras(["all"])
    assert "all" not in result
    assert "mineru" in result
    assert "docling" in result


def test_resolve_extras_unknown_key_raises() -> None:
    with pytest.raises(KeyError):
        env_mod.resolve_extras(["nonesuch"])


def test_resolve_extras_dedupes() -> None:
    r = env_mod.resolve_extras(["converters", "converters"])
    assert r.count("mineru") == 1


# ---- detection ordering ----


def test_detect_prefers_recorded_over_current(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    fake_py = tmp_path / "fake-python.exe"
    fake_py.write_text("")
    doc = tomlkit.document()
    cio.record_env(doc, kind="venv", name=str(tmp_path), python=str(fake_py))
    monkeypatch.setenv("CONDA_DEFAULT_ENV", "somewhere")
    monkeypatch.setenv("CONDA_PREFIX", str(tmp_path))
    detections = env_mod.detect(doc)
    assert detections[0].kind == "recorded"
    assert detections[0].python == str(fake_py)


def test_detect_skips_recorded_when_python_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    doc = tomlkit.document()
    cio.record_env(doc, kind="venv", name="/nope", python="/nope/python.exe")
    monkeypatch.delenv("CONDA_DEFAULT_ENV", raising=False)
    detections = env_mod.detect(doc)
    assert not any(d.kind == "recorded" for d in detections)


def test_detect_base_conda_treated_as_no_env(monkeypatch: pytest.MonkeyPatch) -> None:
    doc = tomlkit.document()
    monkeypatch.setenv("CONDA_DEFAULT_ENV", "base")
    monkeypatch.setenv("CONDA_PREFIX", "/opt/miniconda")
    detections = env_mod.detect(doc)
    assert not any(d.kind == "conda-current" for d in detections)


def test_detect_always_includes_venv_fallback(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    doc = tomlkit.document()
    monkeypatch.delenv("CONDA_DEFAULT_ENV", raising=False)
    monkeypatch.chdir(tmp_path)
    detections = env_mod.detect(doc)
    assert any(d.kind == "venv-fallback" for d in detections)


def test_detect_prefer_venv_only(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    doc = tomlkit.document()
    monkeypatch.setenv("CONDA_DEFAULT_ENV", "someenv")
    monkeypatch.chdir(tmp_path)
    detections = env_mod.detect(doc, prefer="venv")
    kinds = [d.kind for d in detections]
    assert kinds == ["venv-fallback"]


def test_detect_prefer_existing_none(monkeypatch: pytest.MonkeyPatch) -> None:
    doc = tomlkit.document()
    detections = env_mod.detect(doc, prefer="existing")
    assert detections == []


# ---- activate hint ----


def test_activate_hint_conda() -> None:
    assert env_mod.activate_hint({"kind": "conda", "name": "myenv"}) == "conda activate myenv"


def test_activate_hint_venv_posix(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(env_mod.platform, "system", lambda: "Linux")
    assert env_mod.activate_hint({"kind": "venv", "name": "/x/.venv"}) == "source /x/.venv/bin/activate"


def test_activate_hint_venv_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(env_mod.platform, "system", lambda: "Windows")
    hint = env_mod.activate_hint({"kind": "venv", "name": "C:\\repo\\.venv"})
    assert "Scripts" in hint and "activate" in hint


# ---- rollback ----


def test_rollback_forgets_toml_when_attached(tmp_path: Path) -> None:
    doc = tomlkit.document()
    cio.record_env(doc, kind="venv", name=str(tmp_path / ".venv"), python=str(tmp_path / "py"))
    result = env_mod.ProvisionResult(kind="conda-current", name="paperhound", python="", created=False)
    env_mod.rollback(doc, result)
    assert cio.read_env(doc) is None


def test_rollback_removes_venv_when_created(tmp_path: Path) -> None:
    venv_dir = tmp_path / ".venv"
    venv_dir.mkdir()
    (venv_dir / "sentinel").write_text("hi", encoding="utf-8")
    doc = tomlkit.document()
    cio.record_env(doc, kind="venv", name=str(venv_dir), python=str(venv_dir / "python"))
    result = env_mod.ProvisionResult(
        kind="venv-fallback", name=str(venv_dir), python="", created=True
    )
    env_mod.rollback(doc, result)
    assert not venv_dir.exists()
    assert cio.read_env(doc) is None


# ---- TOML I/O for [init.env] ----


def test_record_env_and_read_roundtrip(tmp_path: Path) -> None:
    doc = tomlkit.document()
    cio.record_env(
        doc, kind="conda", name="paperhound",
        python="/x/python", extras=["converters"],
    )
    got = cio.read_env(doc)
    assert got == {
        "kind": "conda",
        "name": "paperhound",
        "python": "/x/python",
        "extras": ["converters"],
        "created_at": got["created_at"],
    }
    assert got["created_at"].endswith("Z")


def test_record_env_preserves_created_at(tmp_path: Path) -> None:
    doc = tomlkit.document()
    cio.record_env(doc, kind="venv", name="/a", python="/a/py")
    first = cio.read_env(doc)["created_at"]  # type: ignore[index]
    cio.record_env(doc, kind="venv", name="/a", python="/a/py")
    second = cio.read_env(doc)["created_at"]  # type: ignore[index]
    assert first == second, "created_at must be pinned on first write"


def test_forget_env_removes_section() -> None:
    doc = tomlkit.document()
    cio.record_env(doc, kind="venv", name="/a", python="/a/py")
    cio.forget_env(doc)
    assert cio.read_env(doc) is None


# ---- integration: real .venv ----


def test_create_venv_and_check_dependencies(tmp_path: Path) -> None:
    """Real .venv creation. Fast because we don't install anything heavy."""
    venv = tmp_path / ".venv"
    python = env_mod.create_venv(venv)
    assert python.is_file()
    # A brand-new venv has no httpx; probe should say False for it.
    status = env_mod.check_packages_installed(str(python), ["httpx>=0.27"])
    assert status == {"httpx>=0.27": False}
    # But it has 'sys' as a stdlib module — sanity check the probe direction.
    assert env_mod.check_packages_installed(str(python), ["sys"]) == {"sys": True}


def test_python_version_returns_something(tmp_path: Path) -> None:
    venv = tmp_path / ".venv"
    python = env_mod.create_venv(venv)
    v = env_mod.python_version(str(python))
    assert v.count(".") == 2  # e.g. 3.11.9


def test_create_venv_refuses_to_overwrite(tmp_path: Path) -> None:
    venv = tmp_path / ".venv"
    venv.mkdir()
    with pytest.raises(FileExistsError):
        env_mod.create_venv(venv)


# ---- conda: gated ----


@pytest.mark.needs_conda
def test_conda_env_creation_and_teardown(tmp_path: Path) -> None:  # pragma: no cover
    """Only runs when explicitly opted in with `pytest -m needs_conda`."""
    conda = env_mod._conda_available()
    if not conda:
        pytest.skip("conda not on PATH")
    name = f"sciforge-test-{os.getpid()}"
    py = env_mod.create_conda_env(name)
    try:
        assert Path(py).is_file()
        v = env_mod.python_version(py)
        assert v.startswith("3.")
    finally:
        env_mod._run([conda, "env", "remove", "-n", name, "-y"])
