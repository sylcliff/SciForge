# Configuration

`sf-download` reads the same TOML file as `sf-lit` (see
`../literature/references/config.md` §resolution order). Its keys live
in a new top-level `[download]` section.

## Resolution order

1. `$SCIFORGE_CONFIG` env var (explicit file path)
2. `.sciforge.toml` in the current working directory, then walking up
   toward a git root
3. `$XDG_CONFIG_HOME/sciforge/config.toml`, or
   `~/.config/sciforge/config.toml` if `XDG_CONFIG_HOME` is unset
4. Built-in defaults (in `scripts/config.py`)

Partial config is fine; every key inherits its default.

## Keys

```toml
[download]
# Optional but recommended. Used as the `email=` query parameter for
# Unpaywall (Unpaywall requires it — no email → source is skipped
# entirely) and as the `mailto=` polite-pool contact for Crossref and
# OpenAlex (they still work without it but throttle harder).
polite_email = ""

# Optional. Semantic Scholar works anonymously but rate-limits are
# shared globally at ~100 requests / 5 minutes, so batch runs hit 429
# quickly. Set a key from https://api.semanticscholar.org/api-key/
# to raise the ceiling.
semanticscholar_api_key = ""

# Per-request HTTP timeout in seconds. Applies to every source
# individually. Metadata queries are always fast (<1s); the PDF fetch
# is the one call that can plausibly need the full budget.
http_timeout_seconds = 30

# Concurrency for --from-file / --ids batches. 4 is chosen to stay
# below every source's polite-pool rate limit even under sustained
# load; increase only if you understand the source's limits.
max_concurrency = 4

# Where to put downloaded PDFs when neither --out nor
# SCIFORGE_DOWNLOAD_DIR is set. "~" is expanded.
download_dir = "~/.sciforge/inbox"
```

## Environment overrides

Environment variables take precedence over the config file (env > config > default):

| Variable | Overrides |
|---|---|
| `SCIFORGE_POLITE_EMAIL` | `download.polite_email` |
| `SCIFORGE_S2_API_KEY` | `download.semanticscholar_api_key` |
| `SCIFORGE_HTTP_TIMEOUT` | `download.http_timeout_seconds` |
| `SCIFORGE_DOWNLOAD_DIR` | `download.download_dir` |

`--out DIR` on the CLI beats both env and config for that one invocation.

## Graceful degradation

Missing credentials never fail the whole run:

- **No `polite_email`** → Unpaywall is **skipped entirely** (its API
  requires a valid email). Crossref and OpenAlex still work but
  without polite-pool priority. `doctor` reports this as a warning,
  not an error. Interactive wizards are **not** used (batch/pipe/agent
  contexts have no TTY).

- **No `semanticscholar_api_key`** → S2 runs anonymously. On 429, that
  specific request is marked `rate_limited` for the paper it happened
  on, but the batch continues. If 429s are observed at all during a
  batch, the batch summary line includes
  `"warnings": ["s2_no_key_seen_429"]` so the user knows what to fix.

- **Config file missing entirely** → all keys fall back to defaults;
  no error.

## Inspecting the effective config

Same commands as `sf-lit` (they share the same file):

```bash
../literature/scripts/sf-lit config path   # print the file being read
../literature/scripts/sf-lit config show   # dump the effective config
```

`sf-download doctor` prints the resolved `[download]` values.

## Suggested global config

Add this section to your existing `~/.config/sciforge/config.toml`:

```toml
[download]
polite_email = "you@example.com"
semanticscholar_api_key = "YOUR-S2-KEY"   # from api.semanticscholar.org/api-key/
```

The email is by far the higher-value entry — it unlocks Unpaywall and
substantially raises the effective rate limits at Crossref / OpenAlex.
