---
name: init
description: Use when the user asks to set up, initialize, or configure SciForge — writing the shared TOML at `~/.config/sciforge/config.toml` (or a project-local `.sciforge.toml`) that `sf-download`, `sf-lit`, and `sf-search` all read. Also use when any of those commands report a config gap — `polite_email` unset, `library.path` missing, `converter mineru not found`, `rate_limited` due to unset `s2_api_key`, `unpaywall skipped`. Runs an interactive wizard that merges into any existing config, backs it up, routes secrets safely, and finishes with a ✓/⚠/✗ doctor table.
---

# init — SciForge setup wizard

Interactive first-run / re-configuration guide for the whole SciForge
stack. Writes **one** TOML file that every other skill reads.

- **Scope**: config only. Does not install MinerU / Docling, does not
  create the researcher `me` profile (points at `sf-me edit` at the
  end), does not fetch papers.
- Python + [`tomlkit`](https://tomlkit.readthedocs.io/) via PEP-723
  inline deps — preserves comments and un-recognised keys when merging.
- Doctor invokes the existing `sf-download doctor` and `sf-lit doctor`
  paths internally; it does not duplicate their probing logic.

## When to invoke

**Trigger on any of these signals:**

- User says "set up / init / configure SciForge", "how do I configure
  polite_email / S2 key / the library path"
- A SciForge command has just failed with a config-related error:
  - `sf-download` warns `Unpaywall skipped (no polite_email)`
  - `sf-download` returns `rate_limited` due to unset `s2_api_key`
  - `sf-lit` fails with `converter mineru not found` or `library.path
    not writable`
  - `sf-search` reports missing NCBI key while querying PubMed
- User's `~/.config/sciforge/config.toml` does not exist yet

**Do not** trigger when a command is already working; init exists to
fill config gaps, not to interrogate a healthy setup.

## First-run check

```bash
scripts/sf-init doctor
```

Prints the ✓/⚠/✗ table without asking any questions. Safe to run any
time.

## Routing

| User asks for | Command |
|---|---|
| "Set up SciForge" (first time or fill gaps) | `scripts/sf-init` |
| "Reconfigure from scratch" | `scripts/sf-init --reset` |
| "Just check my config" | `scripts/sf-init doctor` |
| "Print what my config looks like right now" | `scripts/sf-init --print-config` |
| Non-interactive (Claude passes answers as flags) | `scripts/sf-init --non-interactive --email <e> --library <p> [--converter mineru|docling] [--download-dir <p>] [--s2-key <k>] [--gh-token <t>] [--ncbi-key <k>]` |
| Skip network probes (offline / airgapped) | Add `--skip-network` to any of the above |

## The 7 questions the wizard asks

Two are **required** — anything below breaks fast:

1. `polite_email` — Unpaywall requires it; Crossref/OpenAlex polite pool
   ranks you higher with it. Without it, `sf-download` skips Unpaywall
   entirely and most OA PDFs become unreachable.
2. `library.path` — where `sf-lit` puts `papers/`, `index.db`,
   `collections/`. Defaults to `./library` under the current git repo
   if present, else `~/.sciforge/library`.

Five are **optional** — press Enter to skip. Skipped keys are recorded
in `[init].skipped_keys` so the wizard doesn't re-nag next time.

3. `semanticscholar_api_key` — free key at
   <https://api.semanticscholar.org/api-key/>. Without it, batch
   downloads hit 429 within a few requests.
4. `converter.default` — `mineru` or `docling`. Picks the default when
   `sf-lit convert` is called without `--converter`.
5. `download_dir` — override the temp landing dir for downloaded PDFs.
6. `github_token` — enables the GitHub source in `sf-search` (project
   discovery); optional.
7. `ncbi_api_key` — raises the PubMed rate limit for `sf-search`.

## Secret handling

Secrets (`s2_api_key`, `github_token`, `ncbi_api_key`) are **not**
written to a project-local `.sciforge.toml` under any circumstance.

For each secret the wizard asks, in order:

1. **Is it already in the environment?** (`$SCIFORGE_S2_API_KEY`,
   `$GITHUB_TOKEN`, `$NCBI_API_KEY`) — if yes, print `[from env]` and
   move on. Never rewrite to the file.
2. **Otherwise ask where to put it**:
   - `env` (default) — the wizard prints an `export ...` line to paste
     into your shell rc; the file is **not** touched.
   - `file` — writes into the **global** `~/.config/sciforge/config.toml`
     only, with a `# WARNING: secret — do not commit` comment above the
     key.
   - `skip` — leave unset; you'll hit the natural rate-limit or
     permission error later.

When the wizard runs inside a git repo, it also appends `.sciforge.toml`
and `sciforge/` to the nearest `.gitignore` on first run (idempotent).

## Merge semantics

Running `sf-init` twice does not clobber your file:

- Existing keys keep their values unless you overwrite them at a prompt.
- Existing **comments and un-recognised keys are preserved**
  (`tomlkit`, not the stdlib `tomllib`).
- Before writing, the wizard copies your current file to
  `config.toml.bak-<UTC-timestamp>`.
- The write itself is atomic: `.tmp` → `rename`, never a half-written
  TOML on disk.

`--reset` bypasses merge and asks every question from scratch, but still
writes a `.bak-<ts>` first.

## Exit doctor: the ✓/⚠/✗ table

```
✓ polite_email                user@example.com
✓ library                     D:\...\library  (writable)
⚠ semanticscholar_api_key     unset  → S2 rate-limits after ~10 requests
                                       fix: sf-init and answer question 3
                                            or export SCIFORGE_S2_API_KEY=...
✓ converter.default           mineru  (found: /usr/local/bin/mineru)
✗ converter.docling           not found
                              fix: pipx install docling
reachability arxiv            152ms  ✓
reachability crossref         238ms  ✓
reachability unpaywall        141ms  ✓
reachability openalex         189ms  ✓
reachability semanticscholar  429    ⚠   fix: register S2 API key (see above)
```

Every `⚠` / `✗` row **must** carry a copy-pasteable fix command.

## Reference files (read on demand)

- **`references/config-keys.md`** — every key the wizard writes, its
  default, whether it's a secret, and which downstream skill breaks
  when it's missing.
- **`references/troubleshooting.md`** — the diagnostic mapping used by
  doctor (network timeout → proxy, 429 → API key, 403 → paywall) and
  every non-obvious failure the setup path can produce.

## Hard rules

- **Don't** invoke `sf-init` unattended without `--non-interactive` and
  explicit flags — a wizard hanging on stdin has stalled many CI runs.
- **Don't** write secrets to a project-local `.sciforge.toml`. When the
  wizard is asked to persist a secret, it only ever writes to the
  global config file.
- **Don't** run `sf-init doctor` as the "does the paper download work"
  test — it only verifies config and endpoint reachability; use
  `sf-download <known-DOI>` for the true smoke test.
- **Don't** replace the config file wholesale. If a merge conflict
  arises, ask the user via `--reset` rather than clobbering silently.
