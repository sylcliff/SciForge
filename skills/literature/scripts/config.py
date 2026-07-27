#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///

"""Config resolution for SciForge literature skill.

Resolution order (first match wins):
  1. $SCIFORGE_CONFIG env var
  2. ./.sciforge.toml (walk up to git root)
  3. $XDG_CONFIG_HOME/sciforge/config.toml (or ~/.config/sciforge/config.toml)
  4. Built-in defaults

Usage:
  python3 config.py           # print effective config path
  python3 config.py get <key> # get one value, e.g. "library.path"
  python3 config.py show      # print effective config as TOML
"""

import os
import sys
from functools import lru_cache
from pathlib import Path

DEFAULT_CONFIG = {
    "library": {"path": "./library"},
    "citekey": {"style": "authoryearword", "on_collision": "suffix"},
    # PDF → Markdown converter binaries. `default` picks which one runs
    # when `convert` / `add --and-convert` aren't given `--converter`.
    # Commands can be overridden via env vars for conda/docker setups.
    "converter": {
        "default": "mineru",
        "mineru": {
            "command": "mineru",
            "env": "LITLIB_MINERU_BIN",
            "extra_args": [],
        },
        "docling": {
            "command": "docling",
            "env": "LITLIB_DOCLING_BIN",
            "extra_args": [],
        },
    },
    "sources": {
        # Each source is an object with `enabled` + source-specific options.
        # (Flat bools would collide with sub-dicts in TOML.)
        "arxiv": {"enabled": True, "throttle_seconds": 3},
        "crossref": {"enabled": True},
        "semantic_scholar": {"enabled": True, "api_key_env": "S2_API_KEY"},
        "github": {
            "enabled": True,
            "token_env": "GITHUB_TOKEN",
            "readme_summary_chars": 800,
        },
        "news": {"enabled": True, "max_results": 5, "recency_days": 365},
    },
    "pdf": {"pdftotext_bin": "pdftotext", "use_pdfinfo": True},
    "cache": {
        "arxiv": 168,
        "crossref": 168,
        "s2": 168,
        "github": 24,
        "news": 72,
    },
    "export": {
        "bibtex": {
            "fields": ["title", "author", "year", "journal", "booktitle", "doi", "url", "eprint"],
            "include_file_field": True,
        }
    },
    "ui": {"rich": True},
}


def _find_git_root(path: Path) -> Path | None:
    """Walk up from path looking for a .git directory."""
    for parent in [path] + list(path.parents):
        if (parent / ".git").is_dir():
            return parent
    return None


def _find_config() -> Path | None:
    """Find the first existing config file."""
    # 1. env var
    env_path = os.environ.get("SCIFORGE_CONFIG")
    if env_path:
        p = Path(env_path).expanduser().resolve()
        if p.is_file():
            return p
        return p  # return the path even if it doesn't exist yet

    # 2. cwd / git root
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
    if p.is_file():
        return p
    return p  # even if not yet created, return the path


def _merge_toml_into_defaults(overrides: dict) -> dict:
    """Deep-merge user TOML overrides into DEFAULT_CONFIG."""
    result = {}
    for key, default_val in DEFAULT_CONFIG.items():
        if key in overrides:
            if isinstance(default_val, dict):
                merged = dict(default_val)
                for k, v in overrides[key].items():
                    merged[k] = v
                result[key] = merged
            else:
                result[key] = overrides[key]
        else:
            result[key] = default_val
    for key in overrides:
        if key not in result:
            result[key] = overrides[key]
    return result


@lru_cache(maxsize=1)
def load_config() -> dict:
    """Load and merge config from file + defaults. Returns dict."""
    config_path = _find_config()
    if config_path and config_path.is_file():
        try:
            import tomllib  # Python 3.11+
        except ImportError:
            import tomli as tomllib  # type: ignore[no-redef]
        with open(config_path, "rb") as f:
            user_config = tomllib.load(f)
        merged = _merge_toml_into_defaults(user_config)
    else:
        merged = dict(DEFAULT_CONFIG)

    # resolve library path
    lib_path = merged.get("library", {}).get("path", "./library")
    merged["_library_path"] = str(Path(lib_path).expanduser().resolve())
    merged["_config_path"] = str(config_path) if config_path else "(defaults)"
    return merged


def load_config_path() -> str:
    """Return the config file path or '(defaults)'."""
    c = _find_config()
    if c and c.is_file():
        return str(c)
    return str(c) if c else "(defaults)"


def get_config_value(key: str) -> str | None:
    """Get a dot-separated config value, e.g. 'library.path'."""
    config = load_config()
    parts = key.replace("_library_path", "library.path").split(".")
    val = config
    for p in parts:
        if isinstance(val, dict):
            val = val.get(p)
        else:
            return None
    if val is None:
        return None
    return str(val)


def get_converter_command(converter: str) -> list[str] | None:
    """Resolve the CLI command for a converter (``mineru`` / ``docling``).

    Returns a list suitable for passing as the head of ``subprocess.run``'s
    argv, or ``None`` if the converter is not configured. Env var override
    (``LITLIB_MINERU_BIN`` / ``LITLIB_DOCLING_BIN``) wins over the config
    file's ``command`` value.
    """
    cfg = load_config()
    section = cfg.get("converter", {}).get(converter)
    if not isinstance(section, dict):
        return None
    env_var = section.get("env")
    cmd_str: str | None = None
    if env_var:
        cmd_str = os.environ.get(env_var)
    if not cmd_str:
        cmd_str = section.get("command")
    if not cmd_str:
        return None
    # Split env-var strings so a user can set LITLIB_MINERU_BIN="python -m
    # magic_pdf" for a venv-embedded launch. Use POSIX splitting on POSIX
    # and cmd-style on Windows so backslashes in path literals survive.
    import shlex
    argv = shlex.split(cmd_str, posix=(os.name != "nt"))
    # On Windows, shlex.split with posix=False keeps surrounding quotes on
    # tokens; strip a single leading/trailing quote pair to make the argv
    # directly usable by subprocess.run.
    if os.name == "nt":
        argv = [a[1:-1] if len(a) >= 2 and a[0] == a[-1] == '"' else a for a in argv]
    extra = section.get("extra_args") or []
    if isinstance(extra, list):
        argv.extend(str(x) for x in extra)
    return argv


def default_converter() -> str:
    """Return the configured default converter name."""
    cfg = load_config()
    return str(cfg.get("converter", {}).get("default") or "mineru")


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args:
        print(load_config_path())
    elif args[0] == "get" and len(args) >= 2:
        val = get_config_value(args[1])
        if val is None:
            sys.exit(3)
        print(val)
    elif args[0] == "show":
        import tomllib as tml
        cfg = load_config()
        # Remove internal keys
        show = {k: v for k, v in cfg.items() if not k.startswith("_")}
        # crude TOML print
        for section, vals in show.items():
            print(f"[{section}]")
            if isinstance(vals, dict):
                for k, v in vals.items():
                    if isinstance(v, dict):
                        print(f"[{section}.{k}]")
                        for sk, sv in v.items():
                            print(f"{sk} = {repr(sv)}")
                    else:
                        print(f"{k} = {repr(v)}")
            else:
                print(f"    {vals}")
            print()
    else:
        print(load_config_path())
