# Configuration

The literature skill reads `.sciforge.toml`, resolved in this order
(first match wins):

1. `$SCIFORGE_CONFIG` env var (explicit file path)
2. `.sciforge.toml` in the current working directory, then walking up
   toward a git root
3. `$XDG_CONFIG_HOME/sciforge/config.toml`, or
   `~/.config/sciforge/config.toml` if `XDG_CONFIG_HOME` is unset
4. Built-in defaults (in `scripts/config.py`)

Unset keys inherit their default; a partial config is fine.

## Keys

```toml
[library]
path = "./library"                  # cwd-relative or absolute; "~" expanded

[citekey]
style = "authoryearword"            # authoryearword | authoryear | arxiv | doi-slug
on_collision = "suffix"             # suffix (_a, _b, ...) | error

[sources.arxiv]
enabled = true
throttle_seconds = 3

[sources.crossref]
enabled = true

[sources.semantic_scholar]
enabled = true
api_key_env = "S2_API_KEY"          # env var to read the key from

[sources.github]
enabled = true
token_env = "GITHUB_TOKEN"
readme_summary_chars = 800

[sources.news]
enabled = true
max_results = 5
recency_days = 365

[pdf]
pdftotext_bin = "pdftotext"
use_pdfinfo = true

[cache]                             # TTL in hours before refetching
arxiv = 168
crossref = 168
s2 = 168
github = 24
news = 72

[export.bibtex]
fields = ["title", "author", "year", "journal", "booktitle", "doi", "url", "eprint"]
include_file_field = true

[ui]
rich = true
```

## Inspecting the effective config

```bash
scripts/litlib config path          # print the file being read
scripts/litlib config show          # dump the effective config
scripts/litlib config get library.path
```

## Suggested global config

Put this in `~/.config/sciforge/config.toml` to keep a single library
across all projects:

```toml
[library]
path = "~/.sciforge/library"
```

## `.gitignore` for the library

The DB, cache, and PDF files are large and regenerable. Sidecar JSON
and `notes.md` are text and diff nicely. In a project that commits the
library, use:

```gitignore
library/index.db
library/index.db-wal
library/index.db-shm
library/cache/
library/papers/*/paper.pdf
library/papers/*/si/
```
