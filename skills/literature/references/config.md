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

# PDF → Markdown converter binaries (used by `litlib convert` and
# `litlib add --and-convert`). MinerU / Docling are invoked as CLI
# subprocesses; install them separately (`pipx install mineru`, etc.).
[converter]
default = "mineru"                  # picked when `--converter` is omitted

[converter.mineru]
command = "mineru"                  # anything shlex/subprocess can exec
env     = "LITLIB_MINERU_BIN"       # env var override; wins over `command`
extra_args = []                     # appended to every invocation

[converter.docling]
command = "docling"
env     = "LITLIB_DOCLING_BIN"
extra_args = []

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

## Converter env-var overrides

Both converter env vars (`LITLIB_MINERU_BIN`, `LITLIB_DOCLING_BIN`)
accept a full command string, not just a path. This is how you run
MinerU from a conda env or Docker without editing config:

```bash
# Conda env
export LITLIB_MINERU_BIN="/home/you/miniconda3/envs/mineru/bin/mineru"

# `python -m …` launch
export LITLIB_MINERU_BIN="python -m magic_pdf"

# Docker wrapper
export LITLIB_MINERU_BIN="docker run --rm -v /tmp:/tmp mineru-image mineru"
```

`litlib doctor` reports the resolved command for each converter.

## Inspecting the effective config

```bash
scripts/litlib config path          # print the file being read
scripts/litlib config show          # dump the effective config
scripts/litlib config get library.path
scripts/litlib config get converter.default
```

## Suggested global config

Put this in `~/.config/sciforge/config.toml` to keep a single library
across all projects:

```toml
[library]
path = "~/.sciforge/library"

[converter]
default = "mineru"
```

## `.gitignore` for the library

The DB, cache, converter outputs, and PDF files are large and
regenerable. Sidecar JSON and `notes.md` are text and diff nicely. In a
project that commits the library, use:

```gitignore
library/index.db
library/index.db-wal
library/index.db-shm
library/cache/
library/papers/*/paper.pdf
library/papers/*/paper.md
library/papers/*/converter_output/
library/papers/*/si/
```

Keeping `paper.md` out of git is deliberate: it's derived from the PDF
via a specific converter version, and `metadata.json` +
`converter.json` on disk together let `rebuild-db` restore it (or
`convert --reconvert` re-render it).
