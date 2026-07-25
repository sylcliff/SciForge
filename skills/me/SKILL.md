---
name: me
description: Use when the user wants to record, inspect, or edit their own research profile — personal skills, lab equipment, compute resources, research preferences, and past project history — as a single Markdown file. Consumed by future companion skills for topic-fit checks and self-aware planning ("know thyself" for research).
---

# me — the researcher's self-profile

Stores **one** local Markdown file describing you as a researcher:
skills, equipment, compute, preferences, and history. Other SciForge
skills read it (via `sf-me show --json`) to make topic-fit and
feasibility decisions. **Scope: local data management only.** The
skill is a passive profile store; no advice or scoring lives here.

## Design in one paragraph

`sf-me` owns exactly one entity, `sciforge://me/self`, backed by one
file: `me.md`. The file has a **TOML front-matter** (structured
sections) and a **Markdown body** (free notes). The five sections
are `skill`, `equipment`, `compute`, `preference`, `history` — chosen
to answer the four questions every topic-fit decision needs:

- **Can I do it?** → `skill`
- **Can I physically pull it off?** → `equipment`, `compute`
- **Do I want to do it?** → `preference`
- **What have I done?** → `history`

Entries within each section are objects with two required keys —
`name` (slug) and `short` (one-liner) — plus any optional keys the
user wants (`level`, `updated`, `evidence`, `tags`, …). Optional
keys are **advisory**: `sf-me` does not validate them, but companion
consumers may.

## Storage

`me.md` lives at `~/.sciforge/me/me.md` by default (user-level,
cross-project). Unlike `sf-lit` — whose default library path is
project-level (`./library`) — `sf-me`'s default is deliberately
user-level: your skills and hardware do not change per repository.
Override with the `[me]` section in your SciForge config file.

The file is authoritative on disk. There is no DB, no cache, no
index. Every `sf-me show` re-reads and re-parses it.

## First-run

```bash
scripts/sf-me init       # creates ~/.sciforge/me/me.md with commented examples
scripts/sf-me show       # prints the current profile as a plain-text table
scripts/sf-me edit       # opens me.md in $EDITOR
```

`init` refuses to overwrite an existing file — exits 4 unless
`--force` is given (destructive per ADR-0004).

## When to invoke

- User wants to **initialize** a fresh `me.md`
- User wants to **inspect** their own profile (human view or `--json`)
- User wants to **edit** their profile in `$EDITOR`
- Another skill wants to **read** the profile to gate a topic or a plan

Do NOT invoke this skill to add or remove individual entries — v1
has no `add` / `remove` / `list` / `summary` subcommand. The user
edits the Markdown by hand (or an agent uses `edit` to open it).
Programmatic writes are a future concern.

## Routing

| User asks for | Command |
|---|---|
| Set up a fresh profile | `scripts/sf-me init` |
| Force-overwrite existing profile | `scripts/sf-me init --force` |
| See the profile (human) | `scripts/sf-me show` |
| See the profile (machine) | `scripts/sf-me show --json` |
| Open the file in `$EDITOR` | `scripts/sf-me edit` |

`show` without an id is equivalent to `show self`. Any other id
exits 3 (unknown resource).

## File format

`me.md` uses **TOML front-matter** (Hugo-style `+++` fences), then a
free-form Markdown body:

```markdown
+++
[[skill]]
name = "pytorch-distributed"
short = "Used DDP to train a 7B model"

[[equipment]]
name = "nmr-400"
short = "Shared 400MHz NMR, booking required"

[[compute]]
name = "gpu-a100-cluster"
short = "8x A100 80GB, group-shared, queue-based"

[[preference]]
name = "high-risk-methods"
short = "OK with 3-6 month research bets"

[[history]]
name = "gnn-drug-discovery"
short = "2023-2025, GNNs for compound screening"
+++

# Notes

Free-form Markdown here. Cross-entry reflection, career-stage
thoughts, methodological taste — anything that does not fit a single
structured entry. This section is **not** part of `show --json`.
```

### Required per entry

- `name` — slug (lowercase, hyphenated). Assigned by the user on
  first write; **never renamed** thereafter (same policy as
  `sf-lit` citekeys — CONTEXT.md).
- `short` — one-sentence description. Consumed by both human view
  and downstream `--json` readers.

### Optional per entry (advisory, not validated)

Users are encouraged but not required to add:

- `level` — `novice` / `working` / `proficient` / `expert` (skill)
- `access` — `owned` / `shared` / `external` (equipment, compute)
- `kind` — `local-gpu` / `cluster` / `cloud` (compute)
- `scale` — free string (compute)
- `stance` — `prefer` / `avoid` / `neutral` (preference)
- `period` — free string, e.g. `2023-2025` (history)
- `updated` — ISO-8601 date
- `evidence` — supporting facts (papers, projects, roles)
- `tags` — free list of strings

Nothing bad happens if these are missing; downstream consumers
should treat absence as "unknown," not "false."

### Uniqueness

Within a section, `name` must be unique. `sf-me` does not enforce
this at write time (there is no `add`), but `show --json` will exit 2
with a clear stderr if a duplicate slug is found in the same section.

## Output

### `sf-me show` (default, human)

```
# Me  (~/.sciforge/me/me.md)

## Skills (2)
  pytorch-distributed    Used DDP to train a 7B model
  nmr-analysis           Reads 1D and 2D NMR spectra

## Equipment (1)
  nmr-400                Shared 400MHz NMR, booking required

## Compute (2)
  gpu-a100-cluster       8x A100 80GB, group-shared, queue-based
  laptop-4090            Local RTX 4090 24GB

## Preferences (0)  (empty)

## History (1)
  gnn-drug-discovery     2023-2025, GNNs for compound screening
```

- Sections always print in fixed order: skill, equipment, compute,
  preference, history.
- Empty sections show as `(empty)` — a nudge to fill them in.
- Pure text, no ANSI escapes, pipe-friendly.
- The Markdown body is **not** rendered by `show`. Use `edit` to see it.

### `sf-me show --json`

Per [ADR-0006](../../docs/adr/0006-minimum-output-contract.md), the
JSON output separates the protocol layer (`id` / `type` / `uri`) from
the content layer (`data`):

```json
{
  "id": "self",
  "type": "me",
  "uri": "sciforge://me/self",
  "data": {
    "skill":      [{"name": "pytorch-distributed", "short": "..."}],
    "equipment":  [{"name": "nmr-400",             "short": "..."}],
    "compute":    [{"name": "gpu-a100-cluster",    "short": "..."}],
    "preference": [],
    "history":    [{"name": "gnn-drug-discovery",  "short": "..."}]
  }
}
```

Guarantees:

- Top-level keys are exactly `{"id", "type", "uri", "data"}`.
- `data` keys are exactly the five section names, always present,
  always arrays (possibly empty).
- Each entry is a TOML table verbatim from the file — arbitrary
  optional keys pass through unchanged.
- The Markdown body is **not** included. A future flag (out of
  scope for v1) may expose it.

## SciForge URI namespace

This skill owns `sciforge://me/self` — a single entity, no other
ids. `sf-me show self --json` is the resolver, and `sf-me show
--json` is a shorthand for the same call.

Cross-skill references resolve lazily per
[ADR-0003](../../docs/adr/0003-uri-cross-skill-refs-lazy.md):

```bash
# a future sf-fit-check might do:
sf-me show --json | sf-fit-check "$TOPIC_DESCRIPTION"
```

## Exit codes

Every subcommand follows the SciForge minimum contract
([ADR-0006](../../docs/adr/0006-minimum-output-contract.md)):

| Code | Meaning | Examples |
|---|---|---|
| `0` | Success | `init`, `show`, `edit` completed |
| `2` | Invalid user input | `show <unknown-flag>`, malformed TOML front-matter, duplicate `name` in a section |
| `3` | Resource not found | `show <other-id>`, `edit` when no `me.md` exists yet (run `init` first), `$EDITOR` unset for `edit` |
| `4` | Destructive refused | `init` when `me.md` already exists (add `--force` to bypass) |
| `1`, `≥64` | Runtime error | I/O failure, uncaught exception |

## Config

Config resolution mirrors `sf-lit` (see
[references/config.md](references/config.md)):

1. `$SCIFORGE_CONFIG` env var — explicit path to a TOML file
2. `./.sciforge.toml` — walked up to the git root
3. `$XDG_CONFIG_HOME/sciforge/config.toml` or
   `~/.config/sciforge/config.toml`
4. Built-in defaults

The `[me]` section keys are:

```toml
[me]
dir = "~/.sciforge/me"     # directory that will contain me.md
```

## Verification

```bash
export SCIFORGE_CONFIG=/tmp/sciforge-me-test.toml
mkdir -p /tmp/me-test
printf '[me]\ndir = "/tmp/me-test"\n' > "$SCIFORGE_CONFIG"

scripts/sf-me init
scripts/sf-me show
scripts/sf-me show --json | python -c 'import json,sys; d=json.load(sys.stdin); assert set(d)=={"id","type","uri","data"}; assert set(d["data"])=={"skill","equipment","compute","preference","history"}; print("OK")'
scripts/sf-me init                 # → exit 4
scripts/sf-me init --force         # → exit 0, overwrites
```

## Reference documents

- [references/config.md](references/config.md) — config resolution and `[me]` keys
- [references/schema.md](references/schema.md) — TOML front-matter shape, required and optional keys

## Modules

- Entry point: [scripts/sf-me](scripts/sf-me)
- Config resolution: [scripts/config.py](scripts/config.py) — copied from `sf-lit` and trimmed
- Subcommands: [scripts/init_me.py](scripts/init_me.py), [scripts/show.py](scripts/show.py), [scripts/edit.py](scripts/edit.py)
- Shared helpers: [scripts/profile.py](scripts/profile.py) — parse/dump the `me.md` file

## Design boundaries (what this skill does not do)

- **No advice.** `sf-me` does not score topics, recommend paths, or
  match capabilities to opportunities. That lives in future
  companion skills (`sf-fit-check`, etc.).
- **No programmatic writes** in v1. To add or remove entries the
  user edits `me.md` by hand (or via `sf-me edit`).
- **No validation of optional fields.** The schema is intentionally
  permissive: only `name` + `short` are required.
- **No history / audit log.** If you want a diff, put `me.md` under
  git yourself.
- **No sync.** One machine, one file. Multi-machine sync is a
  dotfiles concern.
