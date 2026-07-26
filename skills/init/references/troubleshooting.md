# Troubleshooting sf-init

The diagnostic mapping doctor uses is small on purpose — the goal is
**every ⚠ / ✗ row carries a copy-pasteable fix**, not exhaustive
theory. This file documents each mapping and every known corner case.

## Doctor row status

| Mark | Meaning | When it appears |
|---|---|---|
| `✓` | Fine, no action needed. | Config value set, path writable, endpoint responds 2xx (or a documented "reachable" non-2xx like Unpaywall's 400/404). |
| `⚠` | Non-fatal, tools may degrade. | Optional key unset, secret unset with named consequence, endpoint slow / rate-limited. |
| `✗` | Blocking. Something downstream will fail. | Required key unset, converter binary missing when it's the default, network probes all timeout. |

## Config-value mappings

| Row | Trigger | Fix line printed |
|---|---|---|
| `polite_email  unset` | Key empty in file and `$SCIFORGE_POLITE_EMAIL` empty. | "Unpaywall will be skipped. Run `sf-init`..." |
| `library.path  unset` | Key empty in file. `sf-lit` cannot run. | "Run `sf-init` and choose a library location." |
| `library.path  <dir> (missing)` | Path resolves, directory does not exist. | "Directory will be created on first use, or `mkdir -p …`." |
| `library.path  <dir> (not writable)` | `os.access(dir, W_OK)` false. | Permissions hint. |
| `converter.default  unset` | Key absent. | Optional; degrades to `--converter` required each call. |
| `converter.mineru  not found` | `shutil.which("mineru")` fails AND `LITLIB_MINERU_BIN` unset. | `pipx install mineru`. Same shape for docling. |
| `<secret>  unset` | Not in env, not in file. | Prints `secrets.SecretKey.reason` and the correct `export` line. |

## Network mappings (Q10)

Each probe returns either `(status_code, latency, None)` or
`(None, latency, exception_name)`. Doctor maps them like this:

| Raw | Row status | Fix |
|---|---|---|
| `2xx` | `✓` | none |
| `400` on Unpaywall | `✓` (400 without email is the reachability signal) | none |
| `404` on Unpaywall | `✓` (unindexed DOI is fine, source is reachable) | none |
| `429` | `⚠` | "rate-limited — register S2 API key" (or general "slow down") |
| `403` | `⚠` | "publisher blocks anonymous API access — sf-download will report `pdf_link_broken`" |
| Other `4xx` / `5xx` | `⚠` | "HTTP <status>" |
| `ReadTimeout` on one probe | `⚠` | "timeout — set HTTP_PROXY / HTTPS_PROXY" |
| `ReadTimeout` on every probe | Extra `✗ network` header row | "Set HTTP_PROXY / HTTPS_PROXY, or verify DNS. `--skip-network` bypasses this check." |
| Any other exception | `⚠` | class name + reachability hint |

The "every probe timed out" summary row is what tells you "your machine
can't talk to the internet" without you having to read 5 individual
timeout lines.

## Merge / write corner cases (Q4)

- **Existing config is malformed TOML**. `load_document` returns an
  empty document rather than crashing. The bad file is moved aside as
  `.bak-<ts>` before the fresh write. Look at the backup to recover any
  keys you'd hand-added.
- **Existing config has a value where the wizard wants a table** (e.g.
  someone wrote `library = "…"` at the top level instead of
  `[library].path`). The wizard silently overwrites that node with the
  expected table. Documented but rare.
- **A key was previously in `skipped_keys` and you now set a value**.
  `set_value` overwrites the key, but the string stays in
  `skipped_keys`. That's fine — `skipped_keys` only suppresses re-asking
  when the value is *still* unset. Merged behavior:
  `key in skipped_keys AND value is None → skip`. Anything else → ask
  (in reset) or trust (in merge).

## Placement (Q3) corner cases

- **Inside a git repo, user picks `project-local`**. The wizard writes
  `<git-root>/.sciforge.toml` and appends `.sciforge.toml` +
  `sciforge/` to `<git-root>/.gitignore` (Q9 / D).
- **`.sciforge.toml` exists in both project and global**. The
  *active* config for downstream skills is the project one (per the
  path resolution order). `sf-init` respects that: it edits whichever
  file was picked by `find_active_config_path`, unless overridden by
  `--target` or `--config-path`.
- **Secrets and project-local**: hard rule — no secret is ever written
  into a `.sciforge.toml`. Answering a secret question in project mode
  routes to an `export` line printed at the end of the run.

## Common failure signatures across skills

Mostly informational — this is how downstream errors look, so doctor
can point users back here.

| Downstream error | Root cause | Fix |
|---|---|---|
| `sf-download` warning `Unpaywall will be skipped (no polite_email)` | `download.polite_email` unset | `sf-init` and answer Q1. |
| `sf-download` returns `rate_limited` in batch | `download.semanticscholar_api_key` unset | `sf-init` and answer Q3, or `export SCIFORGE_S2_API_KEY=...`. |
| `sf-lit convert` error `mineru: not found` | Converter binary missing | `pipx install mineru`, or set `LITLIB_MINERU_BIN`. |
| `sf-lit` error `library.path not set` | Required key missing | `sf-init` and answer Q2. |
| `sf-search` pubmed queries fail intermittently | NCBI rate limit hit | `sf-init` and answer Q7. |
| `sf-search` github source disabled | Missing GitHub token | `sf-init` and answer Q6. |

## Recovery

- **You clobbered your config by running an old sf-init**. The wizard
  always writes `.bak-<UTC-timestamp>` next to the file. Copy it back:
  `cp config.toml.bak-<ts> config.toml`.
- **You want a completely clean re-configure**. `sf-init --reset` — but
  a `.bak-<ts>` is still made.
- **You want to test a different config without touching the real
  one**. `SCIFORGE_CONFIG=/tmp/test.toml sf-init` and every
  downstream skill will read `/tmp/test.toml` too.
