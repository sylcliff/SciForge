# Python environment management

`sf-init env` manages the single Python environment that every SciForge
skill runs in. This document is the reference for its detection
priority, extras groups, transactional guarantees, and doctor output.

## Detection priority

`sf-init env` picks the highest-ranking candidate below, unless a
`--use-*` flag forces one:

| Rank | Candidate | Condition | Action |
|---|---|---|---|
| 1 | **Recorded env** | `[init.env]` in TOML, `python` on disk | Attach; verify only. |
| 2 | **Current conda env** | `$CONDA_DEFAULT_ENV` set + not `base` | Ask user, then attach. |
| 3 | **New conda env** | `conda` / `mamba` on PATH | Create env `sciforge` (or `--conda-env NAME`) with `python=3.11`. |
| 4 | **`.venv` fallback** | Always available | Create `<git-root>/.venv` via `python -m venv`. |

Override with:

- `--use-existing` — force rank 1 (fail if none recorded).
- `--use-conda` — skip rank 1-2, create fresh conda env (rank 3).
- `--use-venv` — skip conda ranks entirely; go straight to `.venv`.

## Extras groups (Q6 / C)

```
sf-init env                        # core only (~5 MB, seconds)
sf-init env --with converters      # + mineru, docling (~2-4 GB, minutes)
sf-init env --with all             # everything below
```

Groups:

| Group | Packages | Purpose |
|---|---|---|
| *core* (implicit) | `httpx`, `pydantic`, `tomlkit` | Every SciForge skill needs these. |
| `converters` | `mineru`, `docling` | Backends for `sf-lit convert`. |
| `pubmed` | *(reserved, empty for now)* | Future PubMed-specific tools. |
| `all` | Union of every non-reserved group. | Convenience alias. |

Unknown groups on `--with` produce an immediate error, not a partial
install.

## Recorded state (Q8)

Each successful `sf-init env` writes:

```toml
[init.env]
kind       = "conda"                         # "conda" | "venv" | "system"
name       = "paperhound"                    # env name (conda) or path (venv)
python     = "C:\\...\\envs\\paperhound\\python.exe"
created_at = "2026-07-26T15:32:08Z"          # first-recorded UTC
extras     = ["converters"]                  # groups enabled
```

Only `python` is used for doctor's dependency probes — env activation is
the user's job (Q8 / C).

## Activate

`sf-init env` prints the correct activation line on success:

- conda: `conda activate <name>`
- POSIX venv: `source <path>/bin/activate`
- Windows venv: `.\<name>\Scripts\activate`

No shim binary is created; SciForge skills are called via `python
scripts/sf-*.py` or `./scripts/sf-*` after the user activates.

## Transactional rollback (Q10)

The wizard treats env creation + install as a single transaction:

- If we **created** a fresh env (rank 3 / rank 4 with `can_create`) and
  the install fails, the env is deleted (`conda env remove -n <name>` or
  `rm -rf .venv`).
- If we **attached** to an existing env (rank 1 / rank 2 / rank 4
  already-existing) and install fails, the env is **not** touched.
  Only the TOML `[init.env]` record — if just written this run — is
  removed.

Either way, the last 20 lines of stderr from the failing subprocess are
printed so the user can decide what to do next. Backup + atomic write
still apply to the TOML, so a failed env run leaves the config file in
the pre-run state.

## Doctor rows (Q9)

The **Python environment** section of `sf-init doctor` renders:

| Row key | Status logic |
|---|---|
| `env.record` | ⚠ if no `[init.env]` at all. |
| `env.kind`, `env.name` | ✓ echoing recorded values. |
| `env.python` | ✓ path exists + version reported; ✗ path missing. |
| `env.dependencies (core)` | ✓ all import; ✗ any missing (rerun `sf-init env`). |
| `env.dependencies (converters)` | ✓ all import; ⚠ any missing (extras is optional). |

Skip the section entirely with `--skip-env`.

## Interaction with PEP-723 shebangs

Each SciForge skill still declares its dependencies in a PEP-723 block
(`# /// script ... # ///`). `sf-init env` **is not required** — the
skills can `pip install` on first run in whatever env the user is
already in. What `sf-init env` buys you is:

- One deterministic env for every skill (no per-invocation surprises).
- Doctor visibility into whether that env is healthy.
- `--with extras` for the heavy converters that PEP-723 would install
  the *first* time you `sf-lit convert` and then cache — but not report
  on until you actually try.
