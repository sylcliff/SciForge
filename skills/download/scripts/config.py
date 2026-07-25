"""Config loader for sf-download.

Reads the same TOML file `sf-lit` reads (resolution order in
`references/config.md`), pulls out the `[download]` section, and layers
environment-variable overrides on top.

Keeps the loader independent of `skills/literature/scripts/config.py` —
we duplicate the tiny bit of file-resolution logic rather than
depending on a sibling skill's Python module (SciForge's `CONTEXT.md`
frames skills as CLI-first contracts, not Python-imported libraries).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# --------------------------------------------------------------------------- #
# Defaults
# --------------------------------------------------------------------------- #

_DEFAULTS: dict[str, Any] = {
    "polite_email": "",
    "semanticscholar_api_key": "",
    "http_timeout_seconds": 30,
    "max_concurrency": 4,
    "download_dir": "~/.sciforge/inbox",
}

# Environment variable → config key
_ENV_MAP: dict[str, str] = {
    "SCIFORGE_POLITE_EMAIL": "polite_email",
    "SCIFORGE_S2_API_KEY": "semanticscholar_api_key",
    "SCIFORGE_HTTP_TIMEOUT": "http_timeout_seconds",
    "SCIFORGE_DOWNLOAD_DIR": "download_dir",
}


# --------------------------------------------------------------------------- #
# Public config object
# --------------------------------------------------------------------------- #


@dataclass
class DownloadConfig:
    polite_email: str = ""
    semanticscholar_api_key: str = ""
    http_timeout_seconds: int = 30
    max_concurrency: int = 4
    download_dir: str = "~/.sciforge/inbox"

    # Bookkeeping so `doctor` can show where things came from.
    _source_path: str = field(default="(defaults)", repr=False)

    @property
    def resolved_download_dir(self) -> Path:
        """Absolute Path with `~` expanded. Does not create the directory."""
        return Path(self.download_dir).expanduser().resolve()

    def as_dict(self) -> dict[str, Any]:
        """For doctor / diagnostics. Drops internal fields."""
        return {
            "polite_email": self.polite_email,
            "semanticscholar_api_key": self.semanticscholar_api_key,
            "http_timeout_seconds": self.http_timeout_seconds,
            "max_concurrency": self.max_concurrency,
            "download_dir": self.download_dir,
        }


# --------------------------------------------------------------------------- #
# Config file lookup — mirrors literature/scripts/config.py
# --------------------------------------------------------------------------- #


def _find_config_file() -> Path | None:
    """Return the config file to read, or None to use defaults.

    Resolution order (first hit wins):
      1. $SCIFORGE_CONFIG env var (an explicit path)
      2. .sciforge.toml in CWD or any ancestor up to git root
      3. $XDG_CONFIG_HOME/sciforge/config.toml (or ~/.config/sciforge/config.toml)
    """
    env_path = os.environ.get("SCIFORGE_CONFIG")
    if env_path:
        p = Path(env_path).expanduser().resolve()
        return p if p.is_file() else None

    cwd = Path.cwd().resolve()
    for candidate in [cwd] + list(cwd.parents):
        f = candidate / ".sciforge.toml"
        if f.is_file():
            return f
        if (candidate / ".git").is_dir():
            break  # stop walking at repo root, per config.md

    xdg = os.environ.get("XDG_CONFIG_HOME")
    root = Path(xdg) if xdg else Path.home() / ".config"
    p = root / "sciforge" / "config.toml"
    return p if p.is_file() else None


def _read_toml_download_section(path: Path) -> dict[str, Any]:
    """Return the `[download]` table from `path`, or an empty dict."""
    try:
        import tomllib  # 3.11+
    except ImportError:  # pragma: no cover
        import tomli as tomllib  # type: ignore[no-redef]

    with open(path, "rb") as f:
        data = tomllib.load(f)
    section = data.get("download", {})
    if not isinstance(section, dict):
        return {}
    return section


def _apply_env_overrides(values: dict[str, Any]) -> None:
    """Mutate `values` with env-var overrides, coercing types."""
    for env_key, cfg_key in _ENV_MAP.items():
        env_val = os.environ.get(env_key)
        if env_val is None or env_val == "":
            continue
        if cfg_key == "http_timeout_seconds":
            try:
                values[cfg_key] = int(env_val)
            except ValueError:
                pass  # ignore malformed; keep prior value
        else:
            values[cfg_key] = env_val


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #


def load_config() -> DownloadConfig:
    """Assemble effective config: defaults → TOML → env vars."""
    merged: dict[str, Any] = dict(_DEFAULTS)
    source = "(defaults)"

    path = _find_config_file()
    if path is not None:
        try:
            section = _read_toml_download_section(path)
        except Exception:  # noqa: BLE001 — a broken config shouldn't crash the CLI
            section = {}
        else:
            source = str(path)
        for k, v in section.items():
            if k in merged:
                merged[k] = v

    _apply_env_overrides(merged)

    cfg = DownloadConfig(**{k: merged[k] for k in _DEFAULTS})
    cfg._source_path = source
    return cfg


__all__ = ["DownloadConfig", "load_config"]
