# Authoring a SciForge skill

This is the operational handbook for anyone (including future-you) adding a new skill to SciForge. It codifies the rules the architecture ADRs imply; if you want the *why*, follow the links to `docs/adr/`.

**Prerequisites**: read [`CONTEXT.md`](CONTEXT.md) for the shared vocabulary. If you disagree with a term, do not shadow it — open a PR against `CONTEXT.md` first.

## 1. Decide: domain skill or micro skill?

The judgement is mechanical, not aesthetic ([ADR-0002](docs/adr/0002-cli-first-json-contract.md), [ADR-0003](docs/adr/0003-uri-cross-skill-refs-lazy.md)):

> A skill is a **domain skill** if and only if it maintains persistent state across invocations (an index, a catalog, a project directory). Otherwise it is a **micro skill**.

If the skill would need to remember something between two calls, it is a domain skill. If every call is a pure function of its arguments plus disk artifacts *it did not create*, it is a micro skill.

**Domain skill checklist:**
- [ ] Owns exactly one URI namespace, `sciforge://<yourname>/*`
- [ ] Implements `show <id> --json` returning `{id, type, uri, …}` ([ADR-0006](docs/adr/0006-minimum-output-contract.md))
- [ ] Its on-disk state is authoritative — the DB (if any) is a rebuildable cache
- [ ] Has an ingest contract documented in `references/ingest-interface.md` or equivalent

**Micro skill checklist:**
- [ ] Reads only from flags/stdin/temp files
- [ ] Writes only to stdout, a user-named output file, or a temp file
- [ ] Never touches a domain skill's on-disk state directly (goes through that skill's CLI)
- [ ] Idempotent: same inputs → same outputs

Current placement of the roadmap skills:

| Skill | Kind | URI namespace | Notes |
|---|---|---|---|
| `sf-lit` | domain | `sciforge://literature/*` | Existing, canonical example |
| `sf-analysis` | domain (staged) | `sciforge://analysis/*` (reserved) | Ship the micro skills first (`sf-fit`, `sf-plot-*`, `sf-summarize`); wrap into a domain skill once real experiments expose the state model |
| `sf-writing` | domain (staged) | `sciforge://writing/*` (reserved) | Same staging as `sf-analysis`. **The domain layer never produces text** — text generation stays in micro skills (`sf-polish`, `sf-cite-check`, `sf-draft-section`). The domain layer only registers, tracks, aggregates references, and resolves URIs |
| *figures* | (none) | *(no namespace)* | Deliberately not a domain skill. Figure outputs live under whatever skill invoked them (usually `sf-analysis`) and are referenced as `sciforge://analysis/<exp-id>/<fig-id>`. Style is a shared configuration file under `figures/styles/`, not a database |

## 2. Naming

Every SciForge-authored CLI is named `sf-<something>` ([ADR-0005](docs/adr/0005-sf-cli-naming.md)):

- Domain skills: `sf-<domain>` — `sf-lit`, `sf-analysis`, `sf-writing`
- Micro skills that belong to a domain: `sf-<domain>-<action>` — `sf-plot-timeseries`, `sf-lit-citekey`
- Standalone micro skills: `sf-<action>` — `sf-polish`, `sf-arxiv`, `sf-cite-check`

The prefix is a reserved namespace; companion skills may opt in if they satisfy this document.

## 3. Output contract

Every CLI must satisfy [ADR-0006](docs/adr/0006-minimum-output-contract.md):

- Support a `--json` flag that switches stdout from human-readable text (default) to machine-readable JSON.
- Timestamps are ISO-8601 UTC. IDs are strings.
- Domain skills expose `show <id> --json` returning **at least** `{"id": str, "type": str, "uri": str}`; additional fields are up to the skill but must be documented in its `SKILL.md`.
- Exit codes:

| Code | Meaning |
|---|---|
| `0` | Success |
| `2` | Invalid user input (bad flags, malformed JSON, conflicting options) |
| `3` | Resource not found (unknown citekey, missing file, unknown URI) |
| `4` | Destructive operation refused without `--force` |
| `1`, `≥64` | System / runtime errors — prefer `2/3/4` when applicable |

## 4. The destructive-operation checklist

Every category below must require **explicit confirmation** — either an interactive prompt, or a `--force` flag that fails closed with exit code `4` if the operation would be destructive and the flag is absent ([ADR-0004](docs/adr/0004-layered-trust-model.md)). Agents must not bypass this guard; the CLI layer is the last line of defence.

**Requires confirmation:**

| # | Category | Examples |
|---|---|---|
| 1 | Filesystem overwrite or delete | Overwriting `paper.md`, deleting an experiment directory, `--move-pdf` that moves a user-provided source file, `-o` to a path that already exists |
| 2 | DB delete / merge | `rebuild-db`, `tag --remove`, `--upsert` onto an existing entry (semantics are explicit in the flag itself → confirmation via the flag counts) |
| 3 | Breaking a provenance link | Overwriting a result already referenced by a manuscript, mutating the config of an experiment that has been cited |
| 4 | Outward call | Uploading to arXiv / OSF / Overleaf, `git push`, sending mail, any API call that mutates external state |
| 5 | Bypassing a fuse | `convert --reconvert --force` (skipping the sha256 fuse), any other `--force`-bypassable safety check |
| 9 | Modifying SciForge configuration | `sf config set …`, editing `figures/styles/*.toml` in place, changing the default converter |

**Does not require confirmation:**

| # | Category | Rationale |
|---|---|---|
| 5 | Canonical output regeneration when fuses pass | `convert --reconvert` on unchanged inputs is a no-op; only `--force` needs a confirm |
| 6 | Fetch / download (read-only outward) | Public read-only APIs (arXiv, PubMed) do not mutate external state; treated like `mkdir` |
| 7 | Read-only queries | `search`, `show`, `list`, any `--json` output |
| 8 | Bulk operations under a workflow | The workflow plan itself is confirmed once; individual items inside it do not re-prompt |
| 10 | Ephemeral output | Writing to `/tmp/` or an unnamed cwd temp file, or to stdout |

## 5. Workflow rules (agent-facing)

When an agent chains two or more skills for the user:

1. **Show the plan first.** Print the list of commands, or a short natural-language summary, and wait for one confirmation.
2. **Stream progress but do not re-prompt** for individual steps within the confirmed plan.
3. **Break out and re-confirm** if a step returns exit code `4` (destructive refusal) or exits with an unexpected non-zero code that requires human judgement.

Single-skill invocations skip step 1 entirely: the CLI's own guards are trusted.

## 6. Documenting the skill

Each skill lives under `skills/<name>/` and must contain:

- `SKILL.md` — the contract the host agent reads (invocation matrix, output shapes, exit codes, per-skill flags). Keep it agent-oriented; avoid marketing language.
- `README.md` — the human-facing overview.
- `references/` — versioned schemas and interface documents.
- `scripts/sf-<name>` — the CLI entrypoint. Prefer Python stdlib-only where practical.
- `tests/` — pytest-driven, with fixtures for any external binary the skill shells out to.

If the skill owns a URI namespace, its `SKILL.md` must state so explicitly and describe the `show <id> --json` payload.

## 7. When to add an ADR

Follow the rules in [`docs/adr/`](docs/adr/). All three must be true:

1. Hard to reverse
2. Surprising without context
3. Genuinely traded off against alternatives

If any is missing, do not add an ADR — either put it in this file (if it's a rule) or in the skill's own docs (if it's local).
