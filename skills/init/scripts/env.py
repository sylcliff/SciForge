"""Python-environment management for sf-init (Q1-Q6, Q8, Q10).

Owns three responsibilities:

1. **Detect** which Python env SciForge should use, in priority order:
   TOML record → current conda → new conda → project-local ``.venv``.

2. **Provision** — either attach to an existing env (install into it) or
   create a fresh env, then install the SciForge core deps and any
   requested ``--with`` extras.

3. **Verify** — run ``python -c 'import httpx'`` etc. against the
   recorded interpreter without depending on the user having
   ``activate``d it.

Transactional guarantee (Q10): if we created a brand-new env in this
run and the install fails partway, we remove it. If we attached to an
existing env, we never delete it — only the TOML record is rolled back.
"""

from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Sequence

from tomlkit import TOMLDocument

import config_io as cio


# --------------------------------------------------------------------------- #
# Package sets — Q6 extras (C)
# --------------------------------------------------------------------------- #

CORE_PACKAGES: list[str] = [
    "httpx>=0.27",
    "pydantic>=2.5",
    "tomlkit>=0.13",
]

# Extras groups. Values are pip-installable specifiers passed straight to
# ``pip install``. Kept intentionally short — the wizard is not a package
# manager, it just wires the well-known ones so `sf-lit convert` works.
EXTRAS: dict[str, list[str]] = {
    "converters": ["mineru", "docling"],
    # Reserved for later; documented but empty for now.
    "pubmed": [],
    "all": [],  # resolved lazily to the union of every other extra
}


def resolve_extras(names: Iterable[str]) -> list[str]:
    """Return a flat, de-duplicated list of pip specifiers for the given
    extras group names. Unknown names raise ``KeyError`` so the CLI can
    surface it clearly."""
    seen: dict[str, None] = {}
    for name in names:
        if name == "all":
            for other in EXTRAS:
                if other == "all":
                    continue
                for spec in EXTRAS[other]:
                    seen.setdefault(spec, None)
            continue
        if name not in EXTRAS:
            raise KeyError(name)
        for spec in EXTRAS[name]:
            seen.setdefault(spec, None)
    return list(seen)


# --------------------------------------------------------------------------- #
# Detection (Q2 / Q3)
# --------------------------------------------------------------------------- #


@dataclass
class EnvDetection:
    kind: str        # "conda-current" | "conda-available" | "venv-fallback" | "recorded"
    name: str        # env name (conda) or path (venv) or "" for recorded
    python: str      # absolute path to python.exe (may be "" for conda-available)
    note: str = ""   # human-readable "why we picked this"
    can_create: bool = False   # True if this option involves creating a new env
    can_attach: bool = False   # True if this option attaches to an existing env


def _which(binary: str) -> str | None:
    p = shutil.which(binary)
    return p


def _conda_available() -> str | None:
    """Return the path to ``conda`` or ``mamba`` if either is on PATH."""
    for name in ("mamba", "conda"):
        p = _which(name)
        if p:
            return p
    return None


def _current_conda_python() -> tuple[str, str] | None:
    """If ``$CONDA_DEFAULT_ENV`` is set, return ``(env_name, python_path)``.

    We locate the interpreter via ``sys.executable`` first, then fall back
    to ``$CONDA_PREFIX/{python.exe|bin/python}``. Never fabricate a path we
    haven't verified — return ``None`` when we can't confirm.
    """
    name = os.environ.get("CONDA_DEFAULT_ENV")
    if not name or name == "base":
        # "base" here isn't a real user env; treat as "no env active".
        return None
    py = None
    prefix = os.environ.get("CONDA_PREFIX")
    if prefix:
        candidate = Path(prefix) / ("python.exe" if platform.system() == "Windows" else "bin/python")
        if candidate.is_file():
            py = str(candidate)
    if not py and sys.executable and Path(sys.executable).is_file():
        py = sys.executable
    if not py:
        return None
    return name, py


def _existing_recorded(doc: TOMLDocument) -> EnvDetection | None:
    """Return a detection describing the [init.env] record, if it exists and
    its python path is still on disk."""
    rec = cio.read_env(doc)
    if not rec:
        return None
    py = rec.get("python", "")
    if py and Path(py).is_file():
        return EnvDetection(
            kind="recorded",
            name=rec.get("name", ""),
            python=py,
            note=f"kind={rec.get('kind')}, from prior sf-init env",
            can_attach=True,
        )
    return None


def detect(doc: TOMLDocument, *, prefer: str | None = None) -> list[EnvDetection]:
    """Return an ordered list of viable env choices, best-first.

    ``prefer`` bypasses discovery entirely:
      * ``"venv"`` — force fallback to project-local .venv
      * ``"conda"`` — force new-conda even if we're already in one
      * ``"existing"`` — use ``[init.env]`` record; fail if none

    When ``prefer`` is None we produce the natural priority order.
    """
    options: list[EnvDetection] = []

    if prefer == "existing":
        rec = _existing_recorded(doc)
        return [rec] if rec else []

    # 1. Recorded env (if still valid). Highest confidence.
    if prefer is None:
        rec = _existing_recorded(doc)
        if rec:
            options.append(rec)

    # 2. Current conda env.
    if prefer in (None, "conda-current"):
        cur = _current_conda_python()
        if cur:
            name, py = cur
            options.append(
                EnvDetection(
                    kind="conda-current",
                    name=name,
                    python=py,
                    note=f"active $CONDA_DEFAULT_ENV={name}",
                    can_attach=True,
                )
            )

    # 3. conda / mamba on PATH → propose new conda env.
    if prefer in (None, "conda"):
        conda = _conda_available()
        if conda:
            options.append(
                EnvDetection(
                    kind="conda-available",
                    name="sciforge",  # default name; overridable at creation
                    python="",         # not known until created
                    note=f"conda/mamba on PATH ({conda})",
                    can_create=True,
                )
            )

    # 4. venv fallback.
    if prefer in (None, "venv"):
        # Prefer git-root/.venv so multiple SciForge checkouts don't share.
        root = _git_root() or Path.cwd().resolve()
        venv_dir = root / ".venv"
        py = _venv_python_path(venv_dir)
        options.append(
            EnvDetection(
                kind="venv-fallback",
                name=str(venv_dir),
                python=str(py),
                note=f"project-local .venv at {venv_dir}",
                can_create=not venv_dir.is_dir(),
                can_attach=venv_dir.is_dir(),
            )
        )

    return options


def _git_root(start: Path | None = None) -> Path | None:
    cwd = (start or Path.cwd()).resolve()
    for c in [cwd] + list(cwd.parents):
        if (c / ".git").exists():
            return c
    return None


def _venv_python_path(venv_dir: Path) -> Path:
    if platform.system() == "Windows":
        return venv_dir / "Scripts" / "python.exe"
    return venv_dir / "bin" / "python"


# --------------------------------------------------------------------------- #
# Provisioning (Q6, Q10)
# --------------------------------------------------------------------------- #


@dataclass
class ProvisionResult:
    kind: str
    name: str
    python: str
    extras: list[str] = field(default_factory=list)
    created: bool = False            # True when we made a fresh env this run
    installed: list[str] = field(default_factory=list)   # packages we installed
    stderr_tail: str = ""            # captured error tail on failure
    ok: bool = True


class ProvisionError(RuntimeError):
    """Raised when env creation / install fails. Carries a ProvisionResult
    so the caller can perform the transactional rollback (Q10)."""

    def __init__(self, msg: str, result: ProvisionResult):
        super().__init__(msg)
        self.result = result


def _run(cmd: Sequence[str], *, capture: bool = True) -> subprocess.CompletedProcess[str]:
    """Wrapper around subprocess.run. Never raises for non-zero exit — the
    caller decides."""
    return subprocess.run(
        list(cmd),
        capture_output=capture,
        text=True,
        check=False,
        # We deliberately don't set shell=True; conda / pip take an argv.
    )


def _tail(text: str, n_lines: int = 20) -> str:
    return "\n".join(text.splitlines()[-n_lines:])


def create_venv(venv_dir: Path) -> Path:
    """Create a plain ``python -m venv`` at ``venv_dir``.

    Uses the currently running interpreter (``sys.executable``) as the source.
    Returns the path to the venv's python.
    """
    venv_dir = Path(venv_dir)
    if venv_dir.exists():
        raise FileExistsError(f"{venv_dir} already exists; refusing to overwrite")
    result = _run([sys.executable, "-m", "venv", str(venv_dir)])
    if result.returncode != 0:
        raise ProvisionError(
            f"python -m venv failed: {_tail(result.stderr or result.stdout)}",
            ProvisionResult(
                kind="venv",
                name=str(venv_dir),
                python="",
                stderr_tail=_tail(result.stderr or result.stdout),
                ok=False,
            ),
        )
    return _venv_python_path(venv_dir)


def create_conda_env(name: str, python_version: str = "3.11") -> str:
    """Create a fresh conda env named ``name`` with ``python=<version>``.

    Returns the absolute path to the env's python interpreter.
    """
    conda = _conda_available()
    if not conda:
        raise ProvisionError(
            "conda not on PATH",
            ProvisionResult(kind="conda", name=name, python="", ok=False),
        )
    result = _run([conda, "create", "-n", name, f"python={python_version}", "-y"])
    if result.returncode != 0:
        raise ProvisionError(
            f"conda create failed: {_tail(result.stderr or result.stdout)}",
            ProvisionResult(
                kind="conda",
                name=name,
                python="",
                stderr_tail=_tail(result.stderr or result.stdout),
                ok=False,
            ),
        )
    # Locate the interpreter via `conda run` info — cheap and portable.
    py = _resolve_conda_python(conda, name)
    if not py:
        raise ProvisionError(
            f"created conda env {name!r} but cannot locate its python",
            ProvisionResult(kind="conda", name=name, python="", ok=False),
        )
    return py


def _resolve_conda_python(conda: str, name: str) -> str | None:
    """Ask conda for the env prefix and derive the python path."""
    result = _run([conda, "env", "list", "--json"])
    if result.returncode != 0:
        return None
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        return None
    for prefix in data.get("envs", []):
        p = Path(prefix)
        if p.name == name:
            py = p / ("python.exe" if platform.system() == "Windows" else "bin/python")
            if py.is_file():
                return str(py)
    return None


def pip_install(python: str, packages: Sequence[str]) -> None:
    """Run ``<python> -m pip install`` against the given packages.

    Raises ProvisionError with the stderr tail on failure so the caller can
    decide whether to roll back.
    """
    if not packages:
        return
    result = _run([python, "-m", "pip", "install", "--upgrade", *packages])
    if result.returncode != 0:
        raise ProvisionError(
            f"pip install failed: {_tail(result.stderr or result.stdout)}",
            ProvisionResult(
                kind="",
                name="",
                python=python,
                stderr_tail=_tail(result.stderr or result.stdout),
                ok=False,
            ),
        )


# --------------------------------------------------------------------------- #
# Verification
# --------------------------------------------------------------------------- #


def check_packages_installed(python: str, packages: Sequence[str]) -> dict[str, bool]:
    """Return ``{package_spec: is_importable}``.

    We import the top-level module name derived from each spec:
    ``"httpx>=0.27"`` → ``"httpx"``. This is deliberately fuzzy — pip
    normalisation quirks (``pytorch`` vs ``torch``) are out of scope; we
    just need doctor to say "core deps present" or "one is missing".
    """
    if not packages:
        return {}

    def _module_name(spec: str) -> str:
        base = spec.split("[", 1)[0]
        for sep in ("==", ">=", "<=", "~=", ">", "<", "!="):
            base = base.split(sep, 1)[0]
        return base.strip().replace("-", "_")

    modules = [_module_name(s) for s in packages]
    probe = (
        "import importlib.util, sys\n"
        "mods = " + repr(modules) + "\n"
        "for m in mods:\n"
        "    sys.stdout.write(m + ':' + ('1' if importlib.util.find_spec(m) else '0') + '\\n')\n"
    )
    result = _run([python, "-c", probe])
    lookup = {m: False for m in modules}
    for line in (result.stdout or "").splitlines():
        if ":" in line:
            m, flag = line.split(":", 1)
            lookup[m.strip()] = flag.strip() == "1"
    return {spec: lookup[_module_name(spec)] for spec in packages}


def python_version(python: str) -> str:
    """Return the Python version string (``3.11.9``), or ``""`` on failure."""
    result = _run([python, "-c", "import sys; print('.'.join(map(str, sys.version_info[:3])))"])
    if result.returncode != 0:
        return ""
    return (result.stdout or "").strip()


# --------------------------------------------------------------------------- #
# Provision orchestration (Q6 + Q10)
# --------------------------------------------------------------------------- #


def provision(
    doc: TOMLDocument,
    *,
    choice: EnvDetection,
    extras: Sequence[str] = (),
    conda_env_name: str | None = None,
) -> ProvisionResult:
    """Materialise ``choice`` on disk (creating an env when needed) and
    install core packages plus requested extras.

    Writes ``[init.env]`` on success. On failure, raises ProvisionError; the
    caller (main.py) is responsible for calling :func:`rollback` — this keeps
    the rollback point *outside* the operation that failed.
    """
    extras = list(extras)
    packages = CORE_PACKAGES + resolve_extras(extras)
    result = ProvisionResult(
        kind=choice.kind,
        name=choice.name,
        python=choice.python,
        extras=list(extras),
    )

    if choice.can_create and choice.kind == "conda-available":
        name = conda_env_name or choice.name
        result.name = name
        result.python = create_conda_env(name)
        result.created = True
    elif choice.can_create and choice.kind == "venv-fallback":
        venv_dir = Path(choice.name)
        result.python = str(create_venv(venv_dir))
        result.created = True
    elif choice.can_attach and choice.python:
        # Attach to existing (conda-current, venv-fallback that already exists,
        # or recorded). Nothing to create.
        result.python = choice.python
    else:
        raise ProvisionError(
            "no viable env action for the given detection",
            ProvisionResult(kind=choice.kind, name=choice.name, python=choice.python, ok=False),
        )

    # Install packages
    try:
        pip_install(result.python, packages)
        result.installed = list(packages)
    except ProvisionError as e:
        e.result.kind = result.kind
        e.result.name = result.name
        e.result.python = result.python
        e.result.extras = result.extras
        e.result.created = result.created
        raise

    # Record in TOML
    normalised_kind = {
        "conda-current": "conda",
        "conda-available": "conda",
        "venv-fallback": "venv",
        "recorded": "unknown",
    }.get(result.kind, result.kind)
    cio.record_env(
        doc,
        kind=normalised_kind,
        name=result.name,
        python=result.python,
        extras=result.extras,
    )
    return result


# --------------------------------------------------------------------------- #
# Rollback (Q10)
# --------------------------------------------------------------------------- #


def rollback(doc: TOMLDocument, result: ProvisionResult, *, ask: bool = False) -> str:
    """Undo whatever ``provision`` did.

    Returns a human-readable summary line describing what was undone.

    Rules:
      * If ``result.created`` is False (we attached to an existing env), only
        the ``[init.env]`` record — if freshly written this run — is removed.
        The env stays.
      * If ``result.created`` is True and ``ask`` is False, remove the env
        unconditionally. The caller confirms with the user *before* calling.
      * The TOML record is always removed.
    """
    if not result.created:
        cio.forget_env(doc)
        return f"rolled back TOML record; env {result.name!r} left untouched"

    if result.kind in ("conda-current", "conda-available"):
        conda = _conda_available()
        if conda and result.name:
            _run([conda, "env", "remove", "-n", result.name, "-y"], capture=True)
        cio.forget_env(doc)
        return f"removed conda env {result.name!r}"

    if result.kind == "venv-fallback":
        p = Path(result.name)
        if p.is_dir():
            shutil.rmtree(p, ignore_errors=True)
        cio.forget_env(doc)
        return f"removed venv at {p}"

    cio.forget_env(doc)
    return "rolled back TOML record"


# --------------------------------------------------------------------------- #
# Activate hints (Q8 / C)
# --------------------------------------------------------------------------- #


def activate_hint(result_or_record: dict) -> str:
    r"""Return the shell line the user should copy-paste to enter the env.

    Accepts either a ``ProvisionResult.__dict__`` or the value returned by
    :func:`config_io.read_env`. Windows PowerShell / cmd is not disambiguated;
    the same ``conda activate`` line works for both, and ``source .venv/...``
    is unix-only (we print an ``.\.venv\Scripts\activate`` variant on
    Windows).
    """
    kind = result_or_record.get("kind", "")
    name = result_or_record.get("name", "")
    if kind == "conda" or kind.startswith("conda-"):
        return f"conda activate {name}"
    if kind == "venv" or kind == "venv-fallback":
        if platform.system() == "Windows":
            return f".\\{Path(name).name}\\Scripts\\activate"
        return f"source {name}/bin/activate"
    return "# no activation needed (system python)"


__all__ = [
    "CORE_PACKAGES",
    "EXTRAS",
    "EnvDetection",
    "ProvisionError",
    "ProvisionResult",
    "activate_hint",
    "check_packages_installed",
    "create_conda_env",
    "create_venv",
    "detect",
    "pip_install",
    "provision",
    "python_version",
    "resolve_extras",
    "rollback",
]
