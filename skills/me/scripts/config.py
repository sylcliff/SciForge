#!/usr/bin/env python3
"""Config resolution for SciForge me skill.

Mirrors the resolution order used by sf-lit (see
``skills/literature/scripts/config.py``) but only reads the ``[me]``
section. Stdlib-only.

Resolution order (first match wins):
  1. $SCIFORGE_CONFIG env var (explicit file path)
  2. ./.sciforge.toml (walked up to git root)
  3. $XDG_CONFIG_HOME/sciforge/config.toml
     or ~/.config/sciforge/config.toml
  4. Built-in defaults

Usage:
  python3 config.py           # print effective config path
  python3 config.py get <key> # e.g. "me.dir"
  python3 config.py show      # print effective [me] section as TOML
"""

from __future__ import annotations

import os
import sys
import tomllib
from pathlib import Path

# Only the [me] section matters here. Other sections (e.g. [library],
# [converter]) are quietly ignored: they belong to other skills.
DEFAULT_ME = {
    "dir": "~/.sciforge/me",
}


def _find_config() -> Path | None:
    # 1. env var
    env_path = os.environ.get("SCIFORGE_CONFIG")
    if env_path:
        p = Path(env_path).expanduser().resolve()
        return p  # return the path even if it does not exist yet

    # 2. cwd -> git root
    cwd = Path.cwd().resolve()
    for candidate in [cwd] + list(cwd.parents):
        if (candidate / ".sciforge.toml").is_file():
            return candidate / ".sciforge.toml"
        if (candidate / ".git").is_dir():
            break

    # 3. xdg config
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        p = Path(xdg) / "sciforge" / "config.toml"
    else:
        p = Path.home() / ".config" / "sciforge" / "config.toml"
    return p


def load_me_config() -> dict:
    """Return the resolved ``[me]`` section, merged with defaults.

    Also attaches two internal keys:
      _config_path — the resolved config file path, or "(defaults)"
      _me_dir      — the resolved on-disk directory (absolute, ~ expanded)
    """
    cfg_path = _find_config()
    user_me: dict = {}
    if cfg_path and cfg_path.is_file():
        with open(cfg_path, "rb") as f:
            data = tomllib.load(f)
        section = data.get("me")
        if isinstance(section, dict):
            user_me = section

    merged = dict(DEFAULT_ME)
    for k, v in user_me.items():
        merged[k] = v

    merged["_config_path"] = str(cfg_path) if cfg_path and cfg_path.is_file() else "(defaults)"
    merged["_me_dir"] = str(Path(str(merged["dir"])).expanduser().resolve())
    return merged


def me_dir() -> Path:
    """Return the resolved me directory as a Path."""
    return Path(load_me_config()["_me_dir"])


def me_file() -> Path:
    """Return the resolved me.md path as a Path."""
    return me_dir() / "me.md"


def get_config_value(key: str) -> str | None:
    """Get a dotted config value, e.g. 'me.dir'."""
    cfg = load_me_config()
    parts = key.split(".")
    if not parts:
        return None
    if parts[0] == "me":
        node: object = {k: v for k, v in cfg.items() if not k.startswith("_")}
        for p in parts[1:]:
            if isinstance(node, dict):
                node = node.get(p)
            else:
                return None
        return None if node is None else str(node)
    # Everything outside [me] is out of scope for sf-me.
    return None


def _cli(argv: list[str]) -> int:
    if not argv:
        print(load_me_config()["_config_path"])
        return 0
    if argv[0] == "get" and len(argv) >= 2:
        val = get_config_value(argv[1])
        if val is None:
            return 3
        print(val)
        return 0
    if argv[0] == "show":
        cfg = load_me_config()
        print("[me]")
        for k, v in cfg.items():
            if k.startswith("_"):
                continue
            print(f"{k} = {v!r}")
        return 0
    print(load_me_config()["_config_path"])
    return 0


if __name__ == "__main__":
    sys.exit(_cli(sys.argv[1:]))
