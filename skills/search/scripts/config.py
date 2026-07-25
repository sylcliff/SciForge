"""Config + shared HTTP helper for sf-search.

Reads the same TOML config as `sf-download` (searches for `.sciforge.toml`
walking up from CWD, or `$XDG_CONFIG_HOME/sciforge/config.toml`), pulls
out a `[search]` section, then layers environment-variable overrides.

Env vars deliberately shared with `sf-download`:
- SCIFORGE_POLITE_EMAIL  — polite mailto for Crossref / OpenAlex / PubMed UA
- SCIFORGE_S2_API_KEY    — Semantic Scholar x-api-key header
- SCIFORGE_HTTP_TIMEOUT  — HTTP timeout seconds (default 10)

Stdlib only. urllib.request + xml.etree.ElementTree cover every source.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from threading import Lock
from typing import Any

# --------------------------------------------------------------------------- #
# Version
# --------------------------------------------------------------------------- #

VERSION = "0.1.0"
UA_BASE = f"sf-search/{VERSION}"

# --------------------------------------------------------------------------- #
# Defaults
# --------------------------------------------------------------------------- #

_DEFAULTS: dict[str, Any] = {
    "polite_email": "",
    "semanticscholar_api_key": "",
    "http_timeout_seconds": 10,
}

_ENV_MAP: dict[str, str] = {
    "SCIFORGE_POLITE_EMAIL": "polite_email",
    "SCIFORGE_S2_API_KEY": "semanticscholar_api_key",
    "SCIFORGE_HTTP_TIMEOUT": "http_timeout_seconds",
}


@dataclass
class SearchConfig:
    polite_email: str = ""
    semanticscholar_api_key: str = ""
    http_timeout_seconds: int = 10

    _source_path: str = field(default="(defaults)", repr=False)

    def as_dict(self) -> dict[str, Any]:
        return {
            "polite_email": self.polite_email,
            "semanticscholar_api_key": self.semanticscholar_api_key,
            "http_timeout_seconds": self.http_timeout_seconds,
        }

    def user_agent(self) -> str:
        """UA string honoring polite email if set."""
        if self.polite_email:
            return f"{UA_BASE} (mailto:{self.polite_email})"
        return UA_BASE


# --------------------------------------------------------------------------- #
# Config file lookup (same as sf-download)
# --------------------------------------------------------------------------- #


def _find_config_file() -> Path | None:
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
            break

    xdg = os.environ.get("XDG_CONFIG_HOME")
    root = Path(xdg) if xdg else Path.home() / ".config"
    p = root / "sciforge" / "config.toml"
    return p if p.is_file() else None


def _read_toml_search_section(path: Path) -> dict[str, Any]:
    try:
        import tomllib  # 3.11+
    except ImportError:  # pragma: no cover
        import tomli as tomllib  # type: ignore[no-redef]

    with open(path, "rb") as f:
        data = tomllib.load(f)
    section = data.get("search", {})
    if not isinstance(section, dict):
        return {}
    return section


def _apply_env_overrides(values: dict[str, Any]) -> None:
    for env_key, cfg_key in _ENV_MAP.items():
        env_val = os.environ.get(env_key)
        if env_val is None or env_val == "":
            continue
        if cfg_key == "http_timeout_seconds":
            try:
                values[cfg_key] = int(env_val)
            except ValueError:
                pass
        else:
            values[cfg_key] = env_val


def load_config() -> SearchConfig:
    """Assemble effective config: defaults → TOML → env vars."""
    merged: dict[str, Any] = dict(_DEFAULTS)
    source = "(defaults)"

    path = _find_config_file()
    if path is not None:
        try:
            section = _read_toml_search_section(path)
        except Exception:  # noqa: BLE001
            section = {}
        else:
            source = str(path)
        for k, v in section.items():
            if k in merged:
                merged[k] = v

    _apply_env_overrides(merged)

    cfg = SearchConfig(**{k: merged[k] for k in _DEFAULTS})
    cfg._source_path = source
    return cfg


# --------------------------------------------------------------------------- #
# HTTP helper
# --------------------------------------------------------------------------- #


class HTTPError(Exception):
    """Raised when an HTTP call to a source fails."""

    def __init__(self, source: str, reason: str):
        super().__init__(f"[{source}] {reason}")
        self.source = source
        self.reason = reason


# Per-source token buckets. Configured lazily on first use.
# Only matters in batch mode; single-query mode fires each source once
# and never touches the bucket.
_BUCKETS: dict[str, "_TokenBucket"] = {}
_BUCKETS_LOCK = Lock()


class _TokenBucket:
    """Simple min-interval enforcer per source (batch mode only)."""

    def __init__(self, min_interval_s: float):
        self.min_interval_s = min_interval_s
        self.last_request_ts = 0.0
        self.lock = Lock()

    def acquire(self) -> None:
        with self.lock:
            now = time.monotonic()
            wait = self.min_interval_s - (now - self.last_request_ts)
            if wait > 0:
                time.sleep(wait)
            self.last_request_ts = time.monotonic()


# Per-source min intervals (seconds). Only used in batch mode.
_MIN_INTERVALS: dict[str, float] = {
    "arxiv": 3.0,        # arXiv docs require ≥3 s between requests
    "pubmed": 0.34,      # 3 req/s without API key
    "crossref": 0.02,    # 50 req/s
    "openalex": 0.10,    # 10 req/s
    "s2_anonymous": 1.0, # 1 req/s without key
    "s2_with_key": 0.01, # 100 req/s with key
}


def enforce_rate_limit(source: str, has_s2_key: bool = False) -> None:
    """Called from within an adapter when batch mode is active."""
    key = source
    if source == "s2":
        key = "s2_with_key" if has_s2_key else "s2_anonymous"
    interval = _MIN_INTERVALS.get(key, 0.0)
    if interval <= 0:
        return
    with _BUCKETS_LOCK:
        if key not in _BUCKETS:
            _BUCKETS[key] = _TokenBucket(interval)
        bucket = _BUCKETS[key]
    bucket.acquire()


def http_get(
    url: str,
    *,
    source: str,
    cfg: SearchConfig,
    headers: dict[str, str] | None = None,
    respect_rate_limit: bool = False,
) -> bytes:
    """HTTPS GET with polite UA. Raises HTTPError on any failure.

    Args:
        url: fully-formed URL (adapter is responsible for building it)
        source: source name for error attribution and rate limiting
        cfg: SearchConfig for timeout + polite email + S2 key
        headers: extra headers to merge in
        respect_rate_limit: enforce per-source token bucket (batch mode)
    """
    if respect_rate_limit:
        has_key = bool(cfg.semanticscholar_api_key) if source == "s2" else False
        enforce_rate_limit(source, has_s2_key=has_key)

    merged_headers: dict[str, str] = {"User-Agent": cfg.user_agent()}
    if source == "s2" and cfg.semanticscholar_api_key:
        merged_headers["x-api-key"] = cfg.semanticscholar_api_key
    if headers:
        merged_headers.update(headers)

    req = urllib.request.Request(url, headers=merged_headers)
    try:
        with urllib.request.urlopen(req, timeout=cfg.http_timeout_seconds) as r:
            return r.read()
    except urllib.error.HTTPError as e:
        raise HTTPError(source, f"HTTP {e.code}: {e.reason}") from e
    except urllib.error.URLError as e:
        raise HTTPError(source, f"URL error: {e.reason}") from e
    except TimeoutError as e:
        raise HTTPError(source, f"timeout after {cfg.http_timeout_seconds}s") from e
    except Exception as e:  # noqa: BLE001 — final safety net
        raise HTTPError(source, f"{type(e).__name__}: {e}") from e


def http_get_json(url: str, **kwargs: Any) -> Any:
    """http_get + json.loads. Wraps decode errors as HTTPError."""
    body = http_get(url, **kwargs)
    try:
        return json.loads(body)
    except json.JSONDecodeError as e:
        raise HTTPError(kwargs.get("source", "?"), f"invalid JSON: {e}") from e


def build_url(base: str, params: dict[str, Any]) -> str:
    """Build a URL, dropping None/empty params, URL-encoding the rest."""
    clean = {k: str(v) for k, v in params.items() if v is not None and v != ""}
    return f"{base}?{urllib.parse.urlencode(clean, quote_via=urllib.parse.quote)}"


__all__ = [
    "VERSION",
    "SearchConfig",
    "HTTPError",
    "load_config",
    "http_get",
    "http_get_json",
    "build_url",
    "enforce_rate_limit",
]
