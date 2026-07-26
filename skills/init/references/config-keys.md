# Config keys sf-init writes

The wizard writes 7 keys, in 4 sections, into a single TOML file. Every
key is optional to leave blank *except* the two `REQUIRED` rows.

| Dotted path | Question `id` | Required? | Secret? | Default | What breaks without it |
|---|---|---|---|---|---|
| `download.polite_email` | `email` | ✅ | no | *(none)* | Unpaywall is skipped entirely (source will return 403 without email). Crossref / OpenAlex de-rank you. Most OA PDFs become unreachable via sf-download. |
| `library.path` | `library` | ✅ | no | `<repo>/library` if in a git repo, else `~/.sciforge/library` | `sf-lit` has nowhere to put papers, index.db, collections. Every literature-skill command fails immediately. |
| `download.semanticscholar_api_key` | `s2_key` | no | ✅ | *(none)* | Semantic Scholar starts returning `429 rate_limited` within a few requests during batch runs. |
| `converter.default` | `converter` | no | no | `mineru` (when unset in file) | `sf-lit convert` requires `--converter <name>` on every call. |
| `download.download_dir` | `download_dir` | no | no | `~/.sciforge/inbox` | Downloads land in the default temp dir; not a hard failure. |
| `sources.github.token` | `gh_token` | no | ✅ | *(none)* | `sf-search`'s GitHub source is disabled (project-code discovery). |
| `sources.pubmed.api_key` | `ncbi_key` | no | ✅ | *(none)* | PubMed rate limit stays at 3 req/s instead of 10 req/s. |

## Meta section

Every write produces / updates:

```toml
[init]
version = "1"
last_run_at = "2026-07-26T15:32:08Z"
skipped_keys = ["download.semanticscholar_api_key", "sources.github.token"]
```

- `version` is the schema version. Do **not** hand-edit it; future
  `sf-init` versions read it to migrate old configs safely.
- `last_run_at` is UTC ISO-8601. Displayed in `doctor` output as a
  soft reminder to re-run when it's been a while.
- `skipped_keys` records the dotted paths the user pressed Enter past
  in a prior run. Those keys are *not* re-asked on the next merge
  (Q11) — so if you're now ready to fill one in, run `sf-init --reset`
  or delete the string from `skipped_keys` and re-run.

## Env var overrides

For every non-secret key, downstream skills honor an env var; for
secrets, env vars are the *preferred* home:

| Env var | Overrides |
|---|---|
| `SCIFORGE_CONFIG` | The whole config file path |
| `SCIFORGE_POLITE_EMAIL` | `download.polite_email` |
| `SCIFORGE_S2_API_KEY` | `download.semanticscholar_api_key` |
| `SCIFORGE_HTTP_TIMEOUT` | `download.http_timeout_seconds` (not asked; defaults to 30) |
| `SCIFORGE_DOWNLOAD_DIR` | `download.download_dir` |
| `LITLIB_MINERU_BIN` | Path to the mineru binary |
| `LITLIB_DOCLING_BIN` | Path to the docling binary |
| `GITHUB_TOKEN` | `sources.github.token` |
| `NCBI_API_KEY` | `sources.pubmed.api_key` |

The wizard checks these before asking; a set env var displays as
`[from env]` and is never rewritten into the file.

## Not owned by sf-init

The other SciForge skills accept many more keys (per-source throttle,
citekey style, converter extra_args, source enable flags, etc.). These
have sensible defaults and are meant to be hand-edited in the TOML when
you actually need to change them; the wizard does *not* ask about them
to keep the first-run experience under 5 questions.

See:
- `../../literature/references/config.md`
- `../../download/references/config.md`
- `../../search/references/config.md`
