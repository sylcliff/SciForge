"""Health-check for sf-init (Q6 + Q10).

Prints a structured ``✓ / ⚠ / ✗`` table. Each non-``✓`` row carries a
one-line fix command so the user can act without re-reading docs.

Two probe families:

1. **Local** — resolved config values, path writability, converter binary
   presence, secrets-vs-env parity.

2. **Network** (``sf-download``-style probes) — one cheap GET per source
   with a short timeout. Skipped when ``--skip-network`` was passed.

Diagnostic mapping (Q10) converts raw failures into intent:

* ``ReadTimeout`` on every probe → suggest ``HTTP_PROXY``.
* ``ReadTimeout`` on one probe → note the service is slow, not fatal.
* ``429`` → mention the missing S2 API key.
* ``403`` → the endpoint publisher is blocking the API-only path.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from tomlkit import TOMLDocument

import config_io as cio
import env as env_mod
import mcp as mcp_mod
import secrets as secmod


# --------------------------------------------------------------------------- #
# Row type
# --------------------------------------------------------------------------- #


@dataclass
class Row:
    status: str    # "ok" | "warn" | "fail"
    key: str
    value: str
    fix: str | None = None    # printed as follow-up line when non-ok


def _mark(status: str) -> str:
    return {"ok": "✓", "warn": "⚠", "fail": "✗"}.get(status, "?")


# --------------------------------------------------------------------------- #
# Config-value checks
# --------------------------------------------------------------------------- #


def _check_polite_email(doc: TOMLDocument) -> Row:
    v = cio.get_value(doc, "download.polite_email")
    if isinstance(v, str) and v.strip():
        return Row("ok", "polite_email", v)
    return Row(
        "warn",
        "polite_email",
        "unset",
        fix="Unpaywall will be skipped. Run `sf-init` and set your contact email.",
    )


def _check_library_path(doc: TOMLDocument) -> Row:
    v = cio.get_value(doc, "library.path")
    if not (isinstance(v, str) and v.strip()):
        return Row(
            "fail",
            "library.path",
            "unset",
            fix="sf-lit cannot store papers. Run `sf-init` and choose a library location.",
        )
    p = Path(str(v)).expanduser()
    if not p.exists():
        return Row(
            "warn",
            "library.path",
            f"{p} (missing)",
            fix=f"Directory will be created on first use, or `mkdir -p {p}` yourself.",
        )
    if not os.access(p, os.W_OK):
        return Row("fail", "library.path", f"{p} (not writable)", fix=f"chmod / permissions on {p}")
    return Row("ok", "library.path", str(p) + "  (writable)")


def _check_converter(doc: TOMLDocument) -> list[Row]:
    rows: list[Row] = []
    default = cio.get_value(doc, "converter.default")
    if isinstance(default, str) and default.strip():
        rows.append(Row("ok", "converter.default", default))
    else:
        rows.append(
            Row(
                "warn",
                "converter.default",
                "unset",
                fix="sf-lit convert will require --converter each time. Run `sf-init` to pick a default.",
            )
        )
    for name, install_hint in [
        ("mineru", "pipx install mineru"),
        ("docling", "pipx install docling"),
    ]:
        # Users can override via env var (LITLIB_MINERU_BIN / LITLIB_DOCLING_BIN)
        env_name = f"LITLIB_{name.upper()}_BIN"
        env_val = os.environ.get(env_name)
        if env_val:
            # Env override wins per literature/references/config.md; we don't
            # try to exec-probe the override — the user knows their setup.
            rows.append(Row("ok", f"converter.{name}", f"{env_val}  [from ${env_name}]"))
            continue
        found = shutil.which(name)
        if found:
            rows.append(Row("ok", f"converter.{name}", f"found: {found}"))
        else:
            rows.append(
                Row(
                    "warn" if default != name else "fail",
                    f"converter.{name}",
                    "not found on PATH",
                    fix=install_hint,
                )
            )
    return rows


def _check_download_dir(doc: TOMLDocument) -> Row:
    v = cio.get_value(doc, "download.download_dir")
    label = str(v) if v else "~/.sciforge/inbox (default)"
    p = Path(str(v) if v else "~/.sciforge/inbox").expanduser()
    if not p.exists():
        return Row("ok", "download.download_dir", f"{label}  (will be created)")
    if not os.access(p, os.W_OK):
        return Row("warn", "download.download_dir", f"{p} (not writable)", fix=f"chmod / permissions on {p}")
    return Row("ok", "download.download_dir", str(p) + "  (writable)")


def _check_secret(dotted: str, doc: TOMLDocument) -> Row:
    sk = secmod.by_dotted(dotted)
    assert sk is not None
    env_val = secmod.env_value(sk)
    if env_val:
        # Show only a hash-like suffix to reassure it's set without leaking.
        return Row("ok", dotted, f"[from ${sk.env_var}]  (last-4: {env_val[-4:]})")
    file_val = cio.get_value(doc, dotted)
    if isinstance(file_val, str) and file_val.strip():
        return Row("ok", dotted, f"[from file]  (last-4: {file_val[-4:]})")
    return Row(
        "warn",
        dotted,
        "unset",
        fix=f"{sk.reason}  export {sk.env_var}=...  or run `sf-init`",
    )


def local_checks(doc: TOMLDocument) -> list[Row]:
    rows: list[Row] = []
    rows.append(_check_polite_email(doc))
    rows.append(_check_library_path(doc))
    rows.extend(_check_converter(doc))
    rows.append(_check_download_dir(doc))
    for sk in secmod.SECRET_KEYS:
        rows.append(_check_secret(sk.dotted, doc))
    return rows


# --------------------------------------------------------------------------- #
# Network probes (mirrors sf-download/scripts/doctor.py)
# --------------------------------------------------------------------------- #


PROBES: list[tuple[str, str]] = [
    ("arxiv", "https://export.arxiv.org/api/query?search_query=id:1706.03762&max_results=1"),
    ("crossref", "https://api.crossref.org/works?rows=0"),
    ("unpaywall", "https://api.unpaywall.org/v2/10.1038/nature12373"),
    ("openalex", "https://api.openalex.org/works?per_page=1"),
    ("semanticscholar", "https://api.semanticscholar.org/graph/v1/paper/search?query=test&limit=1"),
    ("pubmed", "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/einfo.fcgi"),
]


def _diagnose(name: str, status: int | None, err: str | None) -> tuple[str, str | None]:
    """Map raw probe outcome to (Row.status, fix hint). Q10 heuristics."""
    if status is None:
        # Network error: timeout / DNS / TLS
        if err and "Timeout" in err:
            return "warn", f"timeout — check network, or set HTTP_PROXY / HTTPS_PROXY."
        return "warn", f"{err} — check network reachability."
    if 200 <= status < 300:
        return "ok", None
    if status == 429:
        if name == "semanticscholar":
            return "warn", "rate-limited without an API key. Register at https://api.semanticscholar.org/api-key/."
        return "warn", "rate-limited. Slow down or set an API key."
    if status == 400 and name == "unpaywall":
        # unpaywall returns 400 when you hit its DOI endpoint without email;
        # we still call it reachable — the source is fine.
        return "ok", None
    if status == 422 and name == "unpaywall":
        # 422 means "unprocessable entity"; without an ?email= parameter
        # unpaywall returns this. The source itself is reachable.
        return "ok", None
    if status == 403:
        return "warn", f"HTTP 403 — publisher blocks anonymous API access. sf-download will report `pdf_link_broken`."
    if status == 404:
        # unpaywall returns 404 on non-indexed DOIs; also fine as a
        # reachability signal.
        return "ok", None
    return "warn", f"HTTP {status}"


async def _one_probe(client, url: str) -> tuple[int | None, float, str | None]:
    t0 = time.monotonic()
    try:
        r = await client.get(url)
        return r.status_code, (time.monotonic() - t0) * 1000, None
    except Exception as exc:
        return None, (time.monotonic() - t0) * 1000, type(exc).__name__


async def _probe_all(timeout: float) -> list[Row]:
    try:
        import httpx
    except ImportError:  # pragma: no cover — httpx is a hard dep of the skill
        return [Row("fail", "reachability", "httpx not installed", fix="pip install httpx>=0.27")]

    rows: list[Row] = []
    all_timeout = True
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as c:
        tasks = [_one_probe(c, url) for _, url in PROBES]
        results = await asyncio.gather(*tasks)
    for (name, _url), (status, latency, err) in zip(PROBES, results):
        st, fix = _diagnose(name, status, err)
        value = f"{int(latency)}ms" if status is not None else f"{err or 'error'}"
        if st == "ok":
            all_timeout = False
        rows.append(Row(st, f"reachability {name}", value, fix=fix))
    if all_timeout:
        # Prepend a summary row that highlights the pattern.
        rows.insert(
            0,
            Row(
                "fail",
                "network",
                "every probe timed out",
                fix="Set HTTP_PROXY / HTTPS_PROXY, or verify DNS. Re-run with `--skip-network` to bypass this check.",
            ),
        )
    return rows


def network_checks(*, timeout_seconds: float = 5.0) -> list[Row]:
    return asyncio.run(_probe_all(timeout_seconds))


# --------------------------------------------------------------------------- #
# Python-environment checks (Q9 new section)
# --------------------------------------------------------------------------- #


def env_checks(doc: TOMLDocument) -> list[Row]:
    """Verify the recorded ``[init.env]`` env: interpreter path exists,
    core deps importable, extras importable (if declared)."""
    record = cio.read_env(doc)
    if record is None:
        return [
            Row(
                "warn",
                "env.record",
                "not set",
                fix="Run `sf-init env` to detect / create a SciForge Python env.",
            )
        ]

    rows: list[Row] = [
        Row("ok", "env.kind", record.get("kind", "unknown")),
        Row("ok", "env.name", record.get("name", "")),
    ]

    python = record.get("python", "")
    if not python or not Path(python).is_file():
        rows.append(
            Row(
                "fail",
                "env.python",
                f"{python or '(none)'} — not on disk",
                fix="Run `sf-init env` to recreate or attach to a valid env.",
            )
        )
        return rows

    ver = env_mod.python_version(python)
    rows.append(Row("ok", "env.python", f"{python}  ({ver or 'unknown version'})"))

    # Core deps
    core_status = env_mod.check_packages_installed(python, env_mod.CORE_PACKAGES)
    missing_core = [p for p, ok in core_status.items() if not ok]
    if missing_core:
        rows.append(
            Row(
                "fail",
                "env.dependencies (core)",
                f"missing: {', '.join(missing_core)}",
                fix=f"Run `sf-init env --check` to reinstall, or `{python} -m pip install {' '.join(missing_core)}`.",
            )
        )
    else:
        rows.append(
            Row(
                "ok",
                "env.dependencies (core)",
                f"{len(core_status)} present",
            )
        )

    # Extras
    extras = list(record.get("extras", []) or [])
    if extras:
        try:
            packages = env_mod.resolve_extras(extras)
        except KeyError as e:
            rows.append(
                Row(
                    "warn",
                    "env.dependencies (extras)",
                    f"unknown extras group: {e.args[0]}",
                    fix="Remove it from [init.env].extras and re-run `sf-init env`.",
                )
            )
        else:
            status = env_mod.check_packages_installed(python, packages)
            missing = [p for p, ok in status.items() if not ok]
            label = f"env.dependencies ({', '.join(extras)})"
            if missing:
                rows.append(
                    Row(
                        "warn",
                        label,
                        f"missing: {', '.join(missing)}",
                        fix=f"`sf-init env --with {','.join(extras)}` re-installs the group.",
                    )
                )
            else:
                rows.append(Row("ok", label, f"{len(packages)} present"))

    return rows


# --------------------------------------------------------------------------- #
# MCP checks (Q9 new section)
# --------------------------------------------------------------------------- #


def mcp_checks(*, settings_path: Path | None = None) -> list[Row]:
    """Row per MCP server: registered ✓, missing-recommended ⚠, extras ✓."""
    rows: list[Row] = []
    for st in mcp_mod.status(settings_path):
        if st.registered and st.recommended:
            rows.append(Row("ok", f"mcp {st.name}", "registered"))
        elif st.registered and not st.recommended:
            rows.append(Row("ok", f"mcp {st.name}", "registered (extra)"))
        elif not st.registered and st.recommended:
            rows.append(
                Row(
                    "warn",
                    f"mcp {st.name}",
                    "not registered",
                    fix=f"{st.reason}\n                              {st.install_cmd}",
                )
            )
    if not rows:
        rows.append(
            Row(
                "warn",
                "mcp",
                "no recommended MCP servers detected",
                fix="See references/recommended-mcps.md for the SciForge shortlist.",
            )
        )
    return rows


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #


def _render(rows: Iterable[Row], header: str) -> None:
    rows = list(rows)
    if not rows:
        return
    print(header)
    print("─" * max(45, min(80, shutil.get_terminal_size((80, 20)).columns)))
    key_w = max(len(r.key) for r in rows)
    for r in rows:
        mark = _mark(r.status)
        print(f"{mark} {r.key.ljust(key_w)}  {r.value}")
        if r.status != "ok" and r.fix:
            indent = " " * (2 + key_w + 2)
            print(f"{indent}→ {r.fix}")
    print()


def render_all(
    local: Iterable[Row],
    network: Iterable[Row] | None,
    *,
    env: Iterable[Row] | None = None,
    mcp: Iterable[Row] | None = None,
) -> None:
    _render(local, "Config values")
    if env is not None:
        _render(env, "Python environment")
    if mcp is not None:
        _render(mcp, "MCP servers")
    if network is not None:
        _render(network, "Reachability (use --skip-network to bypass)")


def summarize(
    local: Iterable[Row],
    network: Iterable[Row] | None,
    *,
    env: Iterable[Row] | None = None,
    mcp: Iterable[Row] | None = None,
) -> tuple[int, int, int]:
    """Return ``(ok, warn, fail)`` counts across every row given."""
    ok = warn = fail = 0
    everything: list[Row] = list(local)
    if env:
        everything.extend(list(env))
    if mcp:
        everything.extend(list(mcp))
    if network:
        everything.extend(list(network))
    for r in everything:
        if r.status == "ok":
            ok += 1
        elif r.status == "warn":
            warn += 1
        elif r.status == "fail":
            fail += 1
    return ok, warn, fail


__all__ = [
    "Row",
    "PROBES",
    "env_checks",
    "local_checks",
    "mcp_checks",
    "network_checks",
    "render_all",
    "summarize",
]
