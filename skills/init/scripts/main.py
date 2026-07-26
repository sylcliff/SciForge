"""sf-init CLI dispatch (extended for env + mcp verbs).

Modes:

  sf-init                        # interactive wizard (merge mode)
  sf-init --reset                # ignore existing config; ask everything
  sf-init doctor                 # ✓/⚠/✗ health check only
  sf-init env                    # detect / create / verify SciForge Python env
  sf-init env --check            # verify recorded env without touching anything
  sf-init mcp                    # detect Claude MCP registrations; print gaps
  sf-init --print-config         # dump current TOML with secrets redacted
  sf-init --non-interactive \\
          --email <e> --library <p> [--converter mineru|docling] ...

Global flags:

  --skip-network / --skip-env / --skip-mcp   # bypass those doctor sections
  --target global|project                     # override placement
  --config-path PATH                          # explicit target file

`env` extra flags:

  --use-conda / --use-venv / --use-existing   # override detection priority
  --conda-env NAME                            # name for a new conda env
  --with GROUP[,GROUP]                        # extras (converters, all, ...)

`mcp` extra flags:

  --claude-settings PATH                       # override ~/.claude/settings.json
  --list                                       # print recommended clip and exit
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import tomlkit

import config_io as cio
import doctor as doctor_mod
import env as env_mod
import mcp as mcp_mod
import secrets as secmod
import wizard


# --------------------------------------------------------------------------- #
# Parser
# --------------------------------------------------------------------------- #


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="sf-init",
        description="Interactive setup wizard for the SciForge stack.",
    )
    p.add_argument(
        "verb",
        nargs="?",
        default=None,
        choices=[None, "doctor", "env", "mcp"],
        help="Sub-verb. `doctor` health-check; `env` manage Python env; `mcp` MCP status.",
    )

    # Global-ish
    p.add_argument("--reset", action="store_true", help="Ignore existing config; ask every question.")
    p.add_argument("--print-config", action="store_true", help="Print current TOML (secrets redacted) and exit.")
    p.add_argument("--non-interactive", action="store_true", help="Do not prompt; consume answers from flags below.")
    p.add_argument("--skip-network", action="store_true", help="Skip endpoint probes in doctor output.")
    p.add_argument("--skip-env", action="store_true", help="Skip Python-environment section in doctor.")
    p.add_argument("--skip-mcp", action="store_true", help="Skip MCP section in doctor.")
    p.add_argument("--target", choices=["global", "project"], default=None, help="Where to write the config.")
    p.add_argument("--config-path", type=Path, default=None, help="Explicit target file; overrides --target.")

    # Wizard answer flags
    for q in wizard.QUESTIONS:
        p.add_argument(
            f"--{q.id.replace('_', '-')}",
            dest=f"ans_{q.id}",
            default=None,
            metavar={"email": "E", "library": "PATH"}.get(q.id, "VAL"),
            help=q.prompt,
        )

    # `env` verb flags
    p.add_argument("--use-conda", action="store_true", help="Force new conda env.")
    p.add_argument("--use-venv", action="store_true", help="Force project-local .venv fallback.")
    p.add_argument("--use-existing", action="store_true", help="Reuse recorded [init.env]; fail if none.")
    p.add_argument("--conda-env", default=None, metavar="NAME", help="Name for a new conda env (default: sciforge).")
    p.add_argument("--with", dest="with_extras", default=None, metavar="G,H", help="Comma-separated extras groups.")
    p.add_argument("--check", action="store_true", help="`sf-init env`: verify only, do not create / install.")
    p.add_argument("--yes", action="store_true", help="Auto-confirm all prompts (env attach / rollback).")

    # `mcp` verb flags
    p.add_argument("--claude-settings", type=Path, default=None, help="Path override for ~/.claude/settings.json.")
    p.add_argument("--list", action="store_true", help="`sf-init mcp`: print the recommended list and exit.")

    return p


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _resolve_target(args: argparse.Namespace) -> tuple[Path, bool]:
    if args.config_path:
        p = args.config_path.expanduser().resolve()
        return p, p.name == ".sciforge.toml"
    # For --non-interactive we must NOT open the placement prompt (fixes the
    # bug spotted after Q6): default to global unless --target says otherwise.
    if args.non_interactive:
        return wizard.choose_target(args.target or "global")
    if args.target:
        return wizard.choose_target(args.target)
    return wizard.choose_target(None)


def _load_doc_at(path: Path | None) -> tuple[TOMLDocument, Path | None]:  # type: ignore[name-defined]
    """Load a document from ``path``, or the active config, or return an empty
    one. Returns ``(doc, actual_path)``. ``actual_path`` may be None."""
    from tomlkit import TOMLDocument
    resolved = path or cio.find_active_config_path()
    if resolved and resolved.is_file():
        return cio.load_document(resolved), resolved
    return TOMLDocument(), resolved


# --------------------------------------------------------------------------- #
# Verbs
# --------------------------------------------------------------------------- #


def _do_doctor(args: argparse.Namespace) -> int:
    doc, path = _load_doc_at(args.config_path)
    if path and path.is_file():
        print(f"Reading: {path}\n")
    else:
        print("No config file found; showing defaults.\n")

    local = doctor_mod.local_checks(doc)
    env_rows = None if args.skip_env else doctor_mod.env_checks(doc)
    mcp_rows = None if args.skip_mcp else doctor_mod.mcp_checks(settings_path=args.claude_settings)
    network = None if args.skip_network else doctor_mod.network_checks()
    doctor_mod.render_all(local, network, env=env_rows, mcp=mcp_rows)
    ok, warn, fail = doctor_mod.summarize(local, network, env=env_rows, mcp=mcp_rows)
    print(f"Summary: ok={ok}  warn={warn}  fail={fail}")
    print()
    print("Optional: describe yourself as a researcher for topic-fit checks.")
    print("Run  `sf-me edit`  when you're ready.")
    return 0 if fail == 0 else 2


def _do_print_config(args: argparse.Namespace) -> int:
    path = args.config_path or cio.find_active_config_path()
    if not path or not path.is_file():
        print("No config file found.")
        return 1
    doc = cio.load_document(path)
    print(f"# Effective config from: {path}")
    print(tomlkit.dumps(cio.redact_secrets(doc)))
    return 0


def _do_env(args: argparse.Namespace) -> int:
    """`sf-init env` — detect / create / verify SciForge Python environment.

    Layered behaviour:

    * ``--check`` — verify only. Loads doc, runs ``env_checks``, exits.
    * default — pick top detection candidate (subject to --use-*), attach or
      create, install core + ``--with`` extras, record in [init.env],
      atomic-write.
    """
    doc, path = _load_doc_at(args.config_path)
    write_target = path or cio.user_global_config_path()

    if args.check:
        rows = doctor_mod.env_checks(doc)
        doctor_mod.render_all([], None, env=rows)
        ok, warn, fail = doctor_mod.summarize([], None, env=rows)
        print(f"Summary: ok={ok}  warn={warn}  fail={fail}")
        return 0 if fail == 0 else 2

    prefer = None
    if args.use_existing:
        prefer = "existing"
    elif args.use_conda:
        prefer = "conda"
    elif args.use_venv:
        prefer = "venv"

    detections = env_mod.detect(doc, prefer=prefer)
    if not detections:
        print("error: no viable Python env detected. Try --use-venv.", file=sys.stderr)
        return 4

    # Pick top. In non-interactive mode we don't ask.
    choice = detections[0]
    if not args.non_interactive and not args.yes:
        if choice.can_attach and not choice.can_create:
            print(f"Attach to existing env  →  kind={choice.kind}  name={choice.name}")
            print(f"  {choice.note}")
            print("  This will `pip install` the SciForge core deps (~5 MB) into that env.")
            reply = input("Proceed? [Y/n]: ").strip().lower()
            if reply and reply not in ("y", "yes"):
                # Try next candidate
                if len(detections) > 1:
                    choice = detections[1]
                    print(f"→ falling back to: kind={choice.kind}  name={choice.name}")
                else:
                    print("aborted.")
                    return 1

    extras_list: list[str] = []
    if args.with_extras:
        extras_list = [x.strip() for x in args.with_extras.split(",") if x.strip()]
        # Validate now for a clean error
        try:
            env_mod.resolve_extras(extras_list)
        except KeyError as e:
            print(f"error: unknown extras group {e.args[0]!r}. Known: {', '.join(env_mod.EXTRAS)}", file=sys.stderr)
            return 5

    try:
        result = env_mod.provision(
            doc,
            choice=choice,
            extras=extras_list,
            conda_env_name=args.conda_env,
        )
    except env_mod.ProvisionError as e:
        r = e.result
        print(f"error: {e}", file=sys.stderr)
        if r.stderr_tail:
            print(f"--- stderr tail ---\n{r.stderr_tail}", file=sys.stderr)
        # Q10 rollback
        summary = env_mod.rollback(doc, r)
        print(f"→ {summary}", file=sys.stderr)
        # Persist the rolled-back doc so [init.env] doesn't linger
        cio.backup(write_target)
        cio.atomic_write(write_target, doc)
        return 6

    # Persist success
    cio.backup(write_target)
    cio.atomic_write(write_target, doc)

    print()
    print(f"Provisioned SciForge env: kind={result.kind}  name={result.name}")
    print(f"  python:  {result.python}")
    print(f"  packages: {', '.join(result.installed) or '(none)'}")
    if result.extras:
        print(f"  extras:   {', '.join(result.extras)}")
    print()
    hint = env_mod.activate_hint({"kind": result.kind if result.kind.startswith(("conda", "venv")) else ("conda" if "conda" in result.kind else "venv"), "name": result.name})
    print("Activate in this shell:")
    print(f"  {hint}")
    return 0


def _do_mcp(args: argparse.Namespace) -> int:
    """`sf-init mcp` — status only (Q4 C)."""
    if args.list:
        print("SciForge recommended MCP servers:\n")
        for r in mcp_mod.RECOMMENDED:
            print(f"  - {r.name}  (needed-by: {', '.join(r.needed_by)})")
            print(f"      why:     {r.why}")
            print(f"      install: {r.install_cmd}")
            print()
        return 0

    settings = args.claude_settings or mcp_mod.default_settings_path()
    print(f"Reading MCP registry: {settings}\n")
    rows = doctor_mod.mcp_checks(settings_path=args.claude_settings)
    doctor_mod.render_all([], None, mcp=rows)
    ok, warn, fail = doctor_mod.summarize([], None, mcp=rows)
    print(f"Summary: ok={ok}  warn={warn}  fail={fail}")
    return 0 if fail == 0 else 2


def _do_wizard(args: argparse.Namespace) -> int:
    target, project_local = _resolve_target(args)
    doc = cio.load_document(target)

    if args.non_interactive:
        answers = {q.id: getattr(args, f"ans_{q.id}", None) for q in wizard.QUESTIONS}
        try:
            result = wizard.apply_non_interactive(
                doc,
                answers=answers,
                prefer_target="project" if project_local else "global",
            )
            result.target_path = target
            result.project_local = project_local
        except ValueError as e:
            print(f"error: {e}", file=sys.stderr)
            return 3
    else:
        print(f"Writing to: {target}")
        result = wizard.run(doc, reset=args.reset, prefer_target="project" if project_local else "global")
        result.target_path = target

    cio.record_init_meta(doc, skipped=result.skipped)
    backup_path = cio.backup(target)
    cio.atomic_write(target, doc)
    gi_path, gi_added = secmod.append_gitignore()

    print()
    print(f"Setup complete. Config written to {target}")
    if backup_path:
        print(f"Backup:                          {backup_path}")
    if gi_added:
        print(f"gitignore:                       {gi_path}  (+{', '.join(gi_added)})")
    if result.env_export_lines:
        print()
        print("Add these to your shell rc (never commit):")
        for line in result.env_export_lines:
            print(f"  {line}")
    print()
    local = doctor_mod.local_checks(doc)
    env_rows = None if args.skip_env else doctor_mod.env_checks(doc)
    mcp_rows = None if args.skip_mcp else doctor_mod.mcp_checks(settings_path=args.claude_settings)
    network = None if args.skip_network else doctor_mod.network_checks()
    doctor_mod.render_all(local, network, env=env_rows, mcp=mcp_rows)
    ok, warn, fail = doctor_mod.summarize(local, network, env=env_rows, mcp=mcp_rows)
    print(f"Summary: ok={ok}  warn={warn}  fail={fail}")
    print()
    if env_rows and any(r.status != "ok" for r in env_rows):
        print("Next: set up the SciForge Python env  →  `sf-init env`")
    if mcp_rows and any(r.status != "ok" for r in mcp_rows):
        print("Optional: register recommended MCP servers  →  `sf-init mcp`")
    print("Optional: describe yourself as a researcher   →  `sf-me edit`")
    return 0 if fail == 0 else 2


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    if args.print_config:
        return _do_print_config(args)
    if args.verb == "doctor":
        return _do_doctor(args)
    if args.verb == "env":
        return _do_env(args)
    if args.verb == "mcp":
        return _do_mcp(args)
    return _do_wizard(args)


if __name__ == "__main__":
    sys.exit(main())
