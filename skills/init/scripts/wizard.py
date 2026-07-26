"""Interactive question flow for sf-init (Q5).

Owns the *ordering* and *phrasing* of every question. Owns nothing else —
each answer is handed to ``config_io.set_value`` for durable storage and
to ``secrets`` for env-var routing.

Two entry points:

* :func:`run` — talk to a real TTY.
* :func:`apply_non_interactive` — apply a dict of answers gathered
  externally (Claude via AskUserQuestion, or a CI flag set).
"""

from __future__ import annotations

import os
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable

from tomlkit import TOMLDocument

import config_io as cio
import secrets as secmod


# --------------------------------------------------------------------------- #
# Question set (Q5)
# --------------------------------------------------------------------------- #


@dataclass
class Question:
    id: str                   # short slug — used for --non-interactive flags
    dotted: str               # dotted config path (target for set_value)
    prompt: str               # one-line question shown to the user
    help: str                 # 1-2 sentences explaining why this key exists
    required: bool = False
    is_secret: bool = False
    default: str | None = None
    # Optional validator returning a cleaned value or raising ValueError.
    validator: Callable[[str], str] | None = None
    # Optional generator for a smart default computed at ask-time
    # (e.g. project-local library path).
    default_factory: Callable[[], str | None] | None = None
    # Constrained choices — when set, an empty answer means "keep default"
    # not "skip".
    choices: tuple[str, ...] | None = None


def _validate_email(value: str) -> str:
    v = value.strip()
    if not v:
        raise ValueError("email cannot be empty")
    if "@" not in v or "." not in v.split("@", 1)[1]:
        raise ValueError(f"{v!r} does not look like an email address")
    return v


def _validate_path(value: str) -> str:
    v = value.strip()
    if not v:
        raise ValueError("path cannot be empty")
    return str(Path(v).expanduser())


def _default_library_path() -> str:
    """If we're inside a git repo, default to ``<root>/library``. Otherwise
    ``~/.sciforge/library``. Matches the phrasing in README.md."""
    cwd = Path.cwd().resolve()
    for candidate in [cwd] + list(cwd.parents):
        if (candidate / ".git").exists():
            return str(candidate / "library")
    return str(Path.home() / ".sciforge" / "library")


QUESTIONS: list[Question] = [
    Question(
        id="email",
        dotted="download.polite_email",
        prompt="Contact email for API polite pools",
        help=(
            "Unpaywall REQUIRES this to return open-access PDF URLs. "
            "Crossref and OpenAlex use it to rank you above anonymous callers. "
            "Without it, sf-download skips Unpaywall entirely and most OA "
            "PDFs become unreachable."
        ),
        required=True,
        validator=_validate_email,
    ),
    Question(
        id="library",
        dotted="library.path",
        prompt="Where should sf-lit store the library (papers/, index.db)?",
        help="Absolute or ~-expanded path. Will be created if it doesn't exist.",
        required=True,
        validator=_validate_path,
        default_factory=_default_library_path,
    ),
    Question(
        id="s2_key",
        dotted="download.semanticscholar_api_key",
        prompt="Semantic Scholar API key (press Enter to skip)",
        help=(
            "Free key at https://api.semanticscholar.org/api-key/. Without it, "
            "batches larger than ~10 requests hit 429 within seconds."
        ),
        is_secret=True,
    ),
    Question(
        id="converter",
        dotted="converter.default",
        prompt="Default PDF→Markdown converter [mineru / docling]",
        help=(
            "Which converter sf-lit picks when --converter is omitted. "
            "Install the corresponding binary separately (`pipx install mineru` "
            "or `pipx install docling`)."
        ),
        default="mineru",
        choices=("mineru", "docling"),
    ),
    Question(
        id="download_dir",
        dotted="download.download_dir",
        prompt="Temp landing dir for downloaded PDFs (Enter to keep default)",
        help="Defaults to ~/.sciforge/inbox. Only set this if you want a shared / large-disk location.",
    ),
    Question(
        id="gh_token",
        dotted="sources.github.token",
        prompt="GitHub personal-access token (press Enter to skip)",
        help="Enables sf-search's GitHub source. A read-only fine-grained PAT is enough.",
        is_secret=True,
    ),
    Question(
        id="ncbi_key",
        dotted="sources.pubmed.api_key",
        prompt="NCBI / PubMed API key (press Enter to skip)",
        help=(
            "Free key at https://www.ncbi.nlm.nih.gov/account/settings/. "
            "Raises PubMed rate limit from 3 to 10 req/s."
        ),
        is_secret=True,
    ),
]


def question_by_id(qid: str) -> Question | None:
    for q in QUESTIONS:
        if q.id == qid:
            return q
    return None


# --------------------------------------------------------------------------- #
# Result of a wizard run
# --------------------------------------------------------------------------- #


@dataclass
class WizardResult:
    answers: dict[str, str] = field(default_factory=dict)
    skipped: list[str] = field(default_factory=list)
    # Secrets the user asked to route via env (list of shell export lines).
    env_export_lines: list[str] = field(default_factory=list)
    # File the wizard decided to write to.
    target_path: Path | None = None
    # Whether the user picked "project local" for placement (Q3 branch).
    project_local: bool = False


# --------------------------------------------------------------------------- #
# Prompt primitives
# --------------------------------------------------------------------------- #


def _print_wrapped(text: str, indent: str = "  ") -> None:
    """Cheap wrap at terminal width. No dependency on `textwrap` niceties."""
    width = max(40, shutil.get_terminal_size((80, 20)).columns - len(indent))
    words = text.split()
    line = indent
    for w in words:
        if len(line) + len(w) + 1 > width and line.strip():
            print(line.rstrip())
            line = indent
        line += w + " "
    if line.strip():
        print(line.rstrip())


def _ask(prompt: str, default: str | None = None) -> str:
    tail = f" [{default}]" if default else ""
    try:
        return input(f"{prompt}{tail}: ").strip()
    except EOFError:
        # ``EOFError`` here means stdin closed; treat as "user gave up".
        return ""


def _ask_choice(prompt: str, choices: Iterable[str], default: str | None = None) -> str:
    choices = list(choices)
    label = "/".join(f"[{c}]" if c == default else c for c in choices)
    while True:
        raw = _ask(f"{prompt} ({label})", default=None)
        if not raw and default:
            return default
        if raw in choices:
            return raw
        print(f"  → please answer one of: {', '.join(choices)}")


# --------------------------------------------------------------------------- #
# Placement branch (Q3)
# --------------------------------------------------------------------------- #


def choose_target(preferred: str | None = None) -> tuple[Path, bool]:
    """Return ``(config_path, is_project_local)``.

    ``preferred`` may be one of {"global", "project"} to skip the interactive
    prompt (Claude's --non-interactive passes this).
    """
    project = cio.project_local_config_path()
    global_ = cio.user_global_config_path()

    if preferred == "global":
        return global_, False
    if preferred == "project":
        # If none exists yet, put it at the git root (or cwd if not in one).
        root = Path.cwd().resolve()
        for c in [root] + list(root.parents):
            if (c / ".git").exists():
                root = c
                break
        return root / ".sciforge.toml", True

    print()
    print("Where should this config live?")
    _print_wrapped(
        "global — recommended. Applies to every SciForge run on this machine. "
        f"Path: {global_}"
    )
    if secmod.is_git_repo():
        _print_wrapped(
            "project — a .sciforge.toml at the git root of this repo. Only "
            "affects work in this project. Secrets are NEVER written here."
        )
        pick = _ask_choice("Location", ["global", "project"], default="global")
    else:
        pick = "global"

    if pick == "project":
        root = Path.cwd().resolve()
        for c in [root] + list(root.parents):
            if (c / ".git").exists():
                root = c
                break
        return root / ".sciforge.toml", True
    return global_, False


# --------------------------------------------------------------------------- #
# Question dispatch — one answer at a time
# --------------------------------------------------------------------------- #


def _ask_optional_key(q: Question, current: str | None) -> tuple[str | None, bool]:
    """Ask an optional (non-secret, non-required) question.

    Returns ``(value, skipped)``. ``value=None, skipped=True`` means "leave key
    unset". A choice-typed question with no answer returns its default.
    """
    default = current
    if q.default_factory and not default:
        default = q.default_factory()
    if q.default and not default:
        default = q.default
    if q.choices:
        picked = _ask_choice(q.prompt, q.choices, default=default)
        return picked, False

    raw = _ask(q.prompt, default=default)
    if not raw:
        # Empty answer + no default → skip. Empty answer + default → keep the default.
        if default:
            return default, False
        return None, True
    if q.validator:
        try:
            raw = q.validator(raw)
        except ValueError as e:
            print(f"  → {e}; skipping")
            return None, True
    return raw, False


def _ask_required(q: Question, current: str | None) -> str:
    """Ask a required question, looping until we get a valid answer."""
    default = current
    if q.default_factory and not default:
        default = q.default_factory()
    while True:
        raw = _ask(q.prompt, default=default)
        if not raw and default:
            raw = default
        if not raw:
            print("  → this key is required; please provide a value.")
            continue
        if q.validator:
            try:
                raw = q.validator(raw)
            except ValueError as e:
                print(f"  → {e}")
                continue
        return raw


def _ask_secret(q: Question, current: str | None) -> tuple[str | None, str | None, bool]:
    """Ask a secret question.

    Returns ``(value_to_write, export_line, skipped)``:

    * ``value_to_write`` — the value to hand to config_io.set_value(secret=True),
      or ``None`` when the user picked env / skip.
    * ``export_line`` — an ``export FOO=bar`` string when the user picked
      env, else ``None``.
    * ``skipped`` — True iff the user explicitly said skip (recorded in
      ``[init].skipped_keys``).
    """
    sk = secmod.by_dotted(q.dotted)
    assert sk is not None, f"secret question {q.dotted!r} not in secrets module"

    env_val = secmod.env_value(sk)
    if env_val:
        print(f"  → [from env] ${sk.env_var} is already set; not writing to file.")
        return None, None, False

    if current:
        print(f"  → current value already stored in file; press Enter to keep it.")

    raw = _ask(q.prompt, default=None)
    if not raw:
        return current, None, current is None  # skipped only if nothing was there

    # Ask storage preference
    choice = _ask_choice(
        f"Store {sk.label} how?",
        secmod.prompt_choices(),
        default="env",
    )
    if choice == "env":
        export = secmod.format_export_line(sk.env_var, raw)
        return None, export, False
    if choice == "file":
        return raw, None, False
    # skip
    return None, None, True


def _print_intro_for_question(q: Question) -> None:
    print()
    tag = "REQUIRED" if q.required else "optional"
    print(f"— {q.id}  ({tag})")
    _print_wrapped(q.help)


# --------------------------------------------------------------------------- #
# Public entry: interactive
# --------------------------------------------------------------------------- #


def run(
    doc: TOMLDocument,
    *,
    reset: bool = False,
    prefer_target: str | None = None,
) -> WizardResult:
    """Run the wizard against ``doc`` (mutated in place). Returns a
    :class:`WizardResult` summarising what changed.

    Merge mode (Q4 default): existing values are shown as defaults, unset
    keys get asked, previously-skipped optional keys are remembered and
    skipped again silently.

    Reset mode (Q4 --reset): every question is asked from scratch.
    """
    result = WizardResult()
    prior_skipped = set() if reset else cio.prior_skipped_keys(doc)

    # 1. Placement
    target, project_local = choose_target(prefer_target)
    result.target_path = target
    result.project_local = project_local

    # 2. Ask each question in order
    for q in QUESTIONS:
        current = cio.get_value(doc, q.dotted) if not reset else None
        if isinstance(current, str) and current == "":
            current = None

        # Silent-skip already-declined optional keys
        if not reset and q.dotted in prior_skipped and current is None:
            continue

        _print_intro_for_question(q)

        if q.is_secret:
            # Never write a secret to a project-local file (Q9 hard rule)
            if project_local and current is None:
                print(
                    "  → project-local file; secrets are not written here. "
                    "Use env var or run --reset with global placement."
                )
                # Still ask so the user can pick env → export line
                value, export, skipped = _ask_secret(q, current)
                if export:
                    result.env_export_lines.append(export)
                if skipped:
                    result.skipped.append(q.dotted)
                continue

            value, export, skipped = _ask_secret(q, current if isinstance(current, str) else None)
            if value is not None:
                cio.set_value(doc, q.dotted, value, secret=True)
                result.answers[q.dotted] = value
            if export:
                result.env_export_lines.append(export)
            if skipped:
                result.skipped.append(q.dotted)
            continue

        if q.required:
            value = _ask_required(q, current if isinstance(current, str) else None)
            cio.set_value(doc, q.dotted, value)
            result.answers[q.dotted] = value
        else:
            value, skipped = _ask_optional_key(q, current if isinstance(current, str) else None)
            if value is not None:
                cio.set_value(doc, q.dotted, value)
                result.answers[q.dotted] = value
            if skipped:
                result.skipped.append(q.dotted)

    return result


# --------------------------------------------------------------------------- #
# Public entry: non-interactive
# --------------------------------------------------------------------------- #


def apply_non_interactive(
    doc: TOMLDocument,
    *,
    answers: dict[str, str | None],
    prefer_target: str | None = None,
) -> WizardResult:
    """Apply pre-collected answers (Q8 --non-interactive path).

    ``answers`` keys are question ``id``s (``email``, ``library``, ``s2_key``,
    ...); missing / None values mean "leave unset". This path never prompts,
    never queries env vars for secret substitution, and never asks the
    user where to put the file — the caller must decide.

    Required keys must be present; missing required keys raise ``ValueError``.
    """
    result = WizardResult()
    target, project_local = choose_target(prefer_target or "global")
    result.target_path = target
    result.project_local = project_local

    for q in QUESTIONS:
        v = answers.get(q.id)
        if v is None or v == "":
            if q.required:
                raise ValueError(
                    f"Missing required answer for {q.id!r} "
                    f"(use --{q.id.replace('_', '-')} or set it interactively)"
                )
            # Also silently record as skipped so [init].skipped_keys grows.
            result.skipped.append(q.dotted)
            continue

        if q.is_secret and project_local:
            # Hard rule: no secrets in project-local file. Emit an export line
            # for the caller to surface.
            sk = secmod.by_dotted(q.dotted)
            assert sk is not None
            result.env_export_lines.append(secmod.format_export_line(sk.env_var, v))
            continue

        clean = v
        if q.validator:
            clean = q.validator(v)
        cio.set_value(doc, q.dotted, clean, secret=q.is_secret)
        result.answers[q.dotted] = clean

    return result


__all__ = [
    "QUESTIONS",
    "Question",
    "WizardResult",
    "apply_non_interactive",
    "choose_target",
    "question_by_id",
    "run",
]
