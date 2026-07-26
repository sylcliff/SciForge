"""Config I/O for sf-init.

Handles the three things the wizard must never get wrong:

1. **Path resolution** — same order as `sf-download` and `sf-lit`
   (env / project / user-global / defaults). We duplicate the small
   resolver rather than importing from a sibling skill, matching the
   convention set by ``skills/download/scripts/config.py``.

2. **Merge** — read the existing TOML with ``tomlkit`` so comments and
   un-recognised keys survive; overlay the wizard's answers into the
   right sections; write out again.

3. **Atomic write + backup** — copy the current file to
   ``.bak-<UTC>`` first, then write ``.tmp`` and ``os.replace`` it into
   place so no partial write can ever leave a broken TOML on disk.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import tomlkit
from tomlkit import TOMLDocument, comment, nl, table
from tomlkit.items import Table


# --------------------------------------------------------------------------- #
# Sections the wizard touches. Every write goes through _ensure_section() so
# missing tables get created without erasing existing sibling tables.
# --------------------------------------------------------------------------- #

SECTION_DOWNLOAD = "download"
SECTION_LIBRARY = "library"
SECTION_CONVERTER = "converter"
SECTION_SOURCES = "sources"
SECTION_INIT = "init"

INIT_SCHEMA_VERSION = "1"


# --------------------------------------------------------------------------- #
# Location resolution
# --------------------------------------------------------------------------- #


def user_global_config_path() -> Path:
    """Where the *user-global* config file lives, whether it exists yet or not.

    Honors ``$XDG_CONFIG_HOME`` for non-Windows layouts.
    """
    xdg = os.environ.get("XDG_CONFIG_HOME")
    root = Path(xdg) if xdg else Path.home() / ".config"
    return root / "sciforge" / "config.toml"


def project_local_config_path(start: Path | None = None) -> Path | None:
    """The nearest ``.sciforge.toml`` walking up from ``start`` (default cwd).

    Stops at a git root — same rule as the download / literature skills'
    lookup. Returns ``None`` when nothing is found.
    """
    cwd = (start or Path.cwd()).resolve()
    for candidate in [cwd] + list(cwd.parents):
        f = candidate / ".sciforge.toml"
        if f.is_file():
            return f
        if (candidate / ".git").is_dir():
            break
    return None


def find_active_config_path() -> Path | None:
    """Return the config file *currently in effect*, matching
    ``skills/download/scripts/config.py:_find_config_file``.

    Resolution order:
      1. ``$SCIFORGE_CONFIG``
      2. project-local ``.sciforge.toml``
      3. user-global ``~/.config/sciforge/config.toml``
    """
    env_path = os.environ.get("SCIFORGE_CONFIG")
    if env_path:
        p = Path(env_path).expanduser().resolve()
        if p.is_file():
            return p
    project = project_local_config_path()
    if project is not None:
        return project
    ug = user_global_config_path()
    return ug if ug.is_file() else None


# --------------------------------------------------------------------------- #
# Read / merge
# --------------------------------------------------------------------------- #


def load_document(path: Path) -> TOMLDocument:
    """Load ``path`` as a ``tomlkit`` document, or return an empty one when the
    file does not exist / is malformed.

    Malformed files are logged to stderr by the caller (wizard) so the user
    isn't silently stripped of their config. Here we surface a fresh document
    to keep the merge logic simple.
    """
    if not path.is_file():
        return tomlkit.document()
    try:
        return tomlkit.parse(path.read_text(encoding="utf-8"))
    except Exception:
        # tomlkit raises many subclasses; treat any as "start fresh in
        # memory, keep the on-disk file untouched (it'll be moved aside by
        # the backup step before we write)".
        return tomlkit.document()


def _ensure_section(doc: TOMLDocument, name: str) -> Table:
    """Return doc[name] as a Table, creating it if missing."""
    if name in doc:
        val = doc[name]
        if isinstance(val, Table):
            return val
    tbl = table()
    doc.add(name, tbl)
    return tbl


def _ensure_nested_section(doc: TOMLDocument, parts: list[str]) -> Table:
    """Return doc[parts[0]][parts[1]]... — creates any missing intermediate."""
    cur: Any = doc
    for i, part in enumerate(parts):
        if part in cur:
            nxt = cur[part]
            if not isinstance(nxt, Table):
                # Value collision — replace with an empty table. Should be rare;
                # documented in troubleshooting.md.
                nxt = table()
                cur[part] = nxt  # type: ignore[index]
        else:
            nxt = table()
            cur.add(part, nxt) if i == 0 else cur.append(part, nxt)  # type: ignore[union-attr]
        cur = nxt
    return cur  # type: ignore[return-value]


def get_value(doc: TOMLDocument, dotted: str) -> Any:
    """Return doc[a][b][c] for ``a.b.c``, or ``None`` if any hop is missing."""
    cur: Any = doc
    for part in dotted.split("."):
        if isinstance(cur, (TOMLDocument, Table)) and part in cur:
            cur = cur[part]
        else:
            return None
    return cur


def set_value(doc: TOMLDocument, dotted: str, value: Any, *, secret: bool = False) -> None:
    """Set ``doc.a.b.c = value`` for a dotted key.

    When ``secret`` is true, a `# WARNING: secret — do not commit` comment is
    written above the key. Idempotent: re-setting an existing secret key does
    not stack duplicate comments (tomlkit merges adjacent trivia).
    """
    parts = dotted.split(".")
    if len(parts) == 1:
        # Top-level scalar. All 7 wizard keys are section-scoped, so this
        # branch is defensive — kept so an ad-hoc caller can still set
        # e.g. ``polite_email`` at the top if the schema ever changes.
        doc[parts[0]] = value
        return
    parent = _ensure_nested_section(doc, parts[:-1])
    key = parts[-1]
    if secret:
        # Emit comment on its own line just before the key. tomlkit does not
        # attach comments to items via a public API in a clean way, so we
        # write it into the parent table's body directly. Duplicate suppression:
        # only add if the previous line isn't already the same warning.
        body = parent.value.body  # type: ignore[union-attr]
        warning = "# WARNING: secret — do not commit"
        already = any(
            hasattr(item, "as_string") and warning in item.as_string()
            for _, item in body
            if item is not None
        )
        if not already:
            parent.add(comment("WARNING: secret — do not commit"))
    parent[key] = value


def record_init_meta(doc: TOMLDocument, *, skipped: list[str]) -> None:
    """Write / update the ``[init]`` metadata section (Q11)."""
    init = _ensure_section(doc, SECTION_INIT)
    init["version"] = INIT_SCHEMA_VERSION
    init["last_run_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    # Preserve prior skipped_keys across runs — union with whatever the caller
    # passes in. A key that was skipped once stays skipped until an actual
    # value overwrites it.
    prior = init.get("skipped_keys", [])
    merged = sorted(set(list(prior) + list(skipped)))
    init["skipped_keys"] = merged


def prior_skipped_keys(doc: TOMLDocument) -> set[str]:
    init = doc.get(SECTION_INIT, None)
    if not isinstance(init, Table):
        return set()
    raw = init.get("skipped_keys", [])
    try:
        return set(str(x) for x in raw)
    except TypeError:
        return set()


# --------------------------------------------------------------------------- #
# [init.env] — Python-environment record (Q8)
# --------------------------------------------------------------------------- #


def record_env(
    doc: TOMLDocument,
    *,
    kind: str,
    name: str,
    python: str,
    extras: list[str] | None = None,
    created_at: str | None = None,
) -> None:
    """Write / update the ``[init.env]`` sub-section.

    Fields:
      * ``kind``       — ``"conda"`` | ``"venv"`` | ``"system"``
      * ``name``       — conda env name, or ``".venv"``-style relative path
      * ``python``     — absolute path to the interpreter
      * ``extras``     — extras groups enabled (``["converters"]`` etc.)
      * ``created_at`` — ISO-8601 UTC; when omitted, uses now(). Not reset on
        subsequent updates so we can tell "sf-init env created this" apart
        from "sf-init env attached to an existing env".
    """
    init = _ensure_section(doc, SECTION_INIT)
    env_tbl = init.get("env")
    if not isinstance(env_tbl, Table):
        env_tbl = table()
        init.append("env", env_tbl)
    env_tbl["kind"] = kind
    env_tbl["name"] = name
    env_tbl["python"] = python
    if extras is not None:
        env_tbl["extras"] = list(extras)
    if "created_at" not in env_tbl:
        env_tbl["created_at"] = created_at or datetime.now(timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )


def read_env(doc: TOMLDocument) -> dict[str, Any] | None:
    """Return the ``[init.env]`` table as a plain dict, or ``None`` if absent."""
    init = doc.get(SECTION_INIT, None)
    if not isinstance(init, Table):
        return None
    env_tbl = init.get("env", None)
    if not isinstance(env_tbl, Table):
        return None
    return {
        "kind": str(env_tbl.get("kind", "")),
        "name": str(env_tbl.get("name", "")),
        "python": str(env_tbl.get("python", "")),
        "extras": list(env_tbl.get("extras", [])),
        "created_at": str(env_tbl.get("created_at", "")),
    }


def forget_env(doc: TOMLDocument) -> None:
    """Remove the ``[init.env]`` section entirely. Used by env.py's rollback
    when a fresh record turns out to have been written for a failed env
    creation (Q10)."""
    init = doc.get(SECTION_INIT, None)
    if not isinstance(init, Table):
        return
    if "env" in init:
        del init["env"]


# --------------------------------------------------------------------------- #
# Backup + atomic write
# --------------------------------------------------------------------------- #


def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")


def backup(path: Path) -> Path | None:
    """Copy ``path`` to ``<path>.bak-<UTC timestamp>``. Returns backup path,
    or ``None`` when there was nothing to back up."""
    if not path.is_file():
        return None
    dst = path.with_name(f"{path.name}.bak-{_timestamp()}")
    shutil.copy2(path, dst)
    return dst


def atomic_write(path: Path, doc: TOMLDocument) -> None:
    """Serialise ``doc`` and replace ``path`` atomically.

    Uses a temp file in the same directory (``os.replace`` needs same
    filesystem) so the swap is truly atomic on POSIX. On Windows,
    ``os.replace`` is atomic within a volume too.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    text = tomlkit.dumps(doc)
    fd, tmp_str = tempfile.mkstemp(
        prefix=path.name + ".", suffix=".tmp", dir=str(path.parent)
    )
    tmp = Path(tmp_str)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
            f.flush()
            try:
                os.fsync(f.fileno())
            except OSError:
                # fsync fails on some FUSE / network mounts; not fatal.
                pass
        os.replace(tmp, path)
    except Exception:
        # Best-effort cleanup so we don't leave .tmp turds in the config dir.
        try:
            tmp.unlink()
        except OSError:
            pass
        raise


# --------------------------------------------------------------------------- #
# Debug / --print-config
# --------------------------------------------------------------------------- #


def redact_secrets(doc: TOMLDocument) -> TOMLDocument:
    """Return a shallow-cloned document with secret values replaced by
    ``"<redacted>"``. Used by ``sf-init --print-config``."""
    clone = tomlkit.parse(tomlkit.dumps(doc))
    for dotted in SECRET_KEYS:
        parts = dotted.split(".")
        cur: Any = clone
        for part in parts[:-1]:
            if isinstance(cur, (TOMLDocument, Table)) and part in cur:
                cur = cur[part]
            else:
                cur = None
                break
        if cur is None:
            continue
        key = parts[-1]
        if key in cur:
            v = cur[key]
            if isinstance(v, str) and v:
                cur[key] = "<redacted>"
    return clone


# The dotted paths of all keys we treat as secret.
SECRET_KEYS: list[str] = [
    "download.semanticscholar_api_key",
    "sources.github.token",
    "sources.pubmed.api_key",
]


__all__ = [
    "INIT_SCHEMA_VERSION",
    "SECRET_KEYS",
    "atomic_write",
    "backup",
    "find_active_config_path",
    "forget_env",
    "get_value",
    "load_document",
    "prior_skipped_keys",
    "project_local_config_path",
    "read_env",
    "record_env",
    "record_init_meta",
    "redact_secrets",
    "set_value",
    "user_global_config_path",
]
