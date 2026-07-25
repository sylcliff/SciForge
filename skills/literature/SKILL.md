---
name: literature
description: Use when the user wants to manage the local scientific library — cataloguing a paper from prepared metadata + a local PDF, converting the PDF to searchable Markdown via MinerU or Docling, and searching / reading the resulting text. External metadata fetching lives in companion skills.
---

# Literature — local library data management

Manages a personal, PDF-backed library of scientific papers with a
SQLite index and **paper-level full-text search over Markdown** rendered
from each PDF. **Scope: local data management only.** External data
fetching (arXiv, DOI, GitHub, news search) is handled by separate
companion skills that hand pre-assembled metadata to this one.

## Two-phase ingest

Every paper flows through two commands:

1. **`litlib add`** — catalog step. Copies the PDF into the library,
   writes `metadata.json`, inserts a row with `md_status='absent'`. Fast.
2. **`litlib convert`** — MD step. Spawns MinerU (default) or Docling to
   render `paper.md`, promotes it as canonical, and indexes it into the
   FTS store. Synchronous; can take minutes on CPU. `md_status` becomes
   `ready` on success.

The two-in-one shortcut is `add --and-convert`.

## When to invoke

- User wants to **save** a paper for which metadata is already in hand
  (a JSON blob or explicit field values) plus a local PDF
- User wants to **render** an added paper's PDF to searchable Markdown
- User wants to **manage** existing entries — tag, note, collection,
  attach a GitHub repo / news link / SI file
- User wants to **search** the library — full-text over `paper.md`,
  structured filters (year / tag / author / has-md), or list papers
  by MD status
- User wants to **read** a specific paper — whole, by section, by
  page (MinerU), by block kind (MinerU), or via regex grep
- User wants to **cite** what is saved — `export` to BibTeX or JSON

Do NOT invoke this skill to fetch something the user hasn't already
provided. For "save arXiv paper 1706.03762", the agent should first
invoke `arxiv-fetch` (or the equivalent) to pull metadata + download the
PDF, then pipe its output into `litlib add --meta-json -`.

## First-run check

```bash
scripts/litlib doctor            # verify env + DB + converter binaries
scripts/litlib init              # only if doctor says the library is missing
```

`init` is idempotent.

## Ingest — four entry points

External skills (or the user directly) route into the catalog through:

| Caller has | Command |
|---|---|
| A metadata JSON blob + PDF | `scripts/litlib add --meta-json <path or -> --pdf-path P` |
| Explicit fields + PDF | `scripts/litlib add --title "..." [--author X --year Y ...] --pdf-path P` |
| Minimal info (fill in later) | `scripts/litlib add --title "..." --manual` |
| A pre-written `metadata.json` under `library/papers/<key>/` | `scripts/litlib rebuild-db` |

The PDF is **copied** by default; `--move-pdf` moves it. `--upsert`
merges non-empty fields (list fields — tags, collections, authors,
github, news, si — union).

Then convert:

```bash
scripts/litlib convert <citekey>                  # spawn MinerU
scripts/litlib convert <citekey> --converter docling
scripts/litlib convert <citekey> --reconvert      # re-render
scripts/litlib convert <citekey> --reconvert --force   # bypass sha256 fuse
scripts/litlib convert <citekey> --converted-dir DIR   # skip converter; ingest existing output
```

## Companion contract

External fetch skills produce three things:

1. Metadata JSON matching `references/ingest-interface.md`.
2. A local PDF path (non-zero-byte). Required unless the caller
   explicitly uses `--manual`.
3. Optional: a suggested citekey (precomputed via `litlib citekey ...`).

Example one-liner (assumes `arxiv-fetch` exists):

```bash
arxiv-fetch --id 1706.03762 --emit-json --with-pdf /tmp/paper.pdf \
  | scripts/litlib add --meta-json - --pdf-path /tmp/paper.pdf --move-pdf --and-convert
```

## Routing

**Ingest & convert:**

| User asks for | Command |
|---|---|
| Save with CLI fields | `scripts/litlib add --title "..." --author ... --pdf-path P` |
| Save from JSON | `scripts/litlib add --meta-json <path or -> --pdf-path P` |
| Save + convert in one step | append `--and-convert` |
| Save from arXiv / DOI link | **First run a fetch skill**, then pipe |
| Render an added paper to MD | `scripts/litlib convert <key>` |
| Re-render (new MinerU / different converter) | `scripts/litlib convert <key> --reconvert [--force] [--converter docling]` |

**Search / read / manage:**

| User asks for | Command |
|---|---|
| Full-text search | `scripts/litlib search "<query>" [--tag --year --author --has-md]` |
| List by MD status | `scripts/litlib list --md-status absent\|ready\|failed\|stale` |
| Read a paper (whole) | `scripts/litlib read <key>` |
| Read a section | `scripts/litlib read <key> --section "Methods"` |
| Read specific pages (MinerU) | `scripts/litlib read <key> --pages 3-5` |
| Read blocks by kind (MinerU) | `scripts/litlib read <key> --kind table\|equation\|image_caption` |
| Grep the MD | `scripts/litlib read <key> --grep "regex"` |
| Show one paper's metadata card | `scripts/litlib show <key>` |
| Check MD state | `scripts/litlib status <key>` |
| Cite | `scripts/litlib export <selector> --format bibtex` |
| Open PDF / MD / notes / repo | `scripts/litlib open <key> [pdf\|md\|notes\|si\|si:N\|github\|url]` |
| Tag / collection / note | `scripts/litlib tag / collection / note` |
| Attach a GitHub repo | `scripts/litlib add-github <key> --owner O --repo R` |
| Attach a news link | `scripts/litlib add-news <key> --url U [--kind blog]` |
| Attach an SI file | `scripts/litlib add-si <key> --path P` |

## Interaction rules

- **Two-phase.** `add` never runs MinerU/Docling. `convert` is a
  separate, explicit action. `add --and-convert` chains them.
- **Duplicate detection.** If an incoming paper's `arxiv_id`, `doi`,
  `s2_paper_id`, or explicit `citekey` already exists, `add` exits 2
  and prints the existing citekey. Pass `--upsert` to merge instead.
- **Reconvert fuse.** `convert --reconvert` on unchanged PDF + same
  converter + same version prints `action=noop` and exits 0. Pass
  `--force` to bypass.
- **Converter is per-paper.** Every paper is rendered by exactly one
  converter at a time; the choice is recorded in
  `library/papers/<key>/converter.json`. Switching converters writes
  a new output tree alongside the old one and swaps the canonical
  `paper.md`.
- **SI never runs through the converter.** `add-si` copies files as
  attachments; they are not part of `paper.md` and are not searchable
  by `search` / `read`. To make an SI PDF searchable, add it as a
  separate paper.
- **Overwrite guard.** `add-si` copies into the library; it never
  overwrites `paper.pdf` or the sidecar. To attach a replacement PDF,
  use `add --pdf-path P --upsert` followed by `convert --reconvert`.
- **PDF integrity.** `--pdf-path` rejects missing or zero-byte files.

## `md_status`

| State | Meaning |
|---|---|
| `absent` | No `paper.md` on disk; only metadata is indexed. Search cannot reach the body. |
| `ready` | `paper.md` exists and is in the FTS index. |
| `failed` | Last `convert` failed; `md_last_error` holds the reason. Retry with `convert --reconvert`. |
| `stale` | PDF changed since the last convert (or `paper.md` was hand-deleted). Search still hits the old MD; fix with `convert --reconvert`. |

`status <key>` re-validates the on-disk state each call — if the DB
claims `ready` but `paper.md` is missing, or the PDF sha256 no longer
matches `converter.json`, it downgrades to `stale` and persists.

## Output rules

`add` prints machine-readable lines for programmatic use:

```
citekey=<key>
pdf=<absolute path or "(not provided)">
notes=<absolute path>
md_status=absent
hint=run `litlib convert <key>` to enable full-text search
```

`convert` prints:

```
citekey=<key>
converter=mineru|docling
converter_version=<string>
md=<absolute path to paper.md>
chars=<int>
md_status=ready
```

Or `action=noop` when the fuse trips.

`search` prints a compact table (or `--json` for structured output with
`score`, `snippet`, `has_md`, `md_status`). `read` prints the requested
slice or, with `--json`, always returns an array (even for 0 or 1 hits).
`show` renders a markdown card with a `**md:** <state>` line.

## Search & read semantics

- **Ranking**: BM25 over `papers_md_fts` only (Q12/A). Score returned as
  "larger is better" (SQLite's raw BM25 is reversed).
- **Structured filters**: `--year`, `--tag`, `--author`, `--collection`,
  `--has-pdf`, `--has-md` are pure WHERE clauses and do not influence
  ranking.
- **No content in the FTS index** for papers with `md_status='absent'` —
  they can only be found via structured filters or `show`.
- **Tokenizer**: `porter unicode61 remove_diacritics 2`. English stems
  merge (`network` = `networks`); non-Latin scripts fall back to
  character-level indexing.
- **Section extraction** (`read --section`): fuzzy substring match
  (token-level, diacritic-folded) over headings. Works for MinerU
  (with page numbers) and Docling (headings parsed from markdown).
  Always returns an array — 0, 1, or N sections.
- **`--pages` / `--kind`**: MinerU-only; Docling papers get a clear
  error telling the caller to use `--section` instead.

## Reference documents

Read on demand:

- [references/schema.md](references/schema.md) — SQLite DDL, FTS
  triggers, and `md_status` semantics
- [references/config.md](references/config.md) — config keys, including
  `[converter]` and env-var overrides
- [references/ingest-interface.md](references/ingest-interface.md) —
  companion JSON schema
- [references/recipes.md](references/recipes.md) — invocation patterns
- [references/bibtex.md](references/bibtex.md) — BibTeX mapping and
  citekey rules

## Verification

Requires MinerU installed (`pipx install mineru`), or Docling
(`pipx install docling`), on `PATH`. Substitute `--converter docling` if
that's what you have.

```bash
scripts/litlib init --path /tmp/testlib
export SCIFORGE_CONFIG=<config.toml pointing at /tmp/testlib>
scripts/litlib doctor
echo "%PDF-1.4 fake" > /tmp/fake.pdf
scripts/litlib add --title "Test paper" --author "Alice B Smith" --year 2024 \
    --arxiv-id 2401.00001 --pdf-path /tmp/fake.pdf --tag test
scripts/litlib status  smith2024test         # → md_status=absent
scripts/litlib convert smith2024test         # → md_status=ready
scripts/litlib search "test"                 # BM25 hit + snippet
scripts/litlib read   smith2024test          # dumps paper.md
scripts/litlib show   smith2024test --json | jq .md
scripts/litlib export smith2024test --format bibtex
```

## Modules

Modules live under `scripts/`:

- Entry point: [scripts/litlib](scripts/litlib)
- Ingest & catalog: [scripts/add.py](scripts/add.py)
- PDF→MD conversion: [scripts/convert.py](scripts/convert.py)
- Search & read: [scripts/search.py](scripts/search.py),
  [scripts/read.py](scripts/read.py)
- Metadata & MD status: [scripts/show.py](scripts/show.py),
  [scripts/status.py](scripts/status.py)
- Relations: [scripts/associate.py](scripts/associate.py) (tag /
  collection / note / add-github / add-news / add-si)
- Export / open / rebuild: [scripts/export_lib.py](scripts/export_lib.py),
  [scripts/open_file.py](scripts/open_file.py),
  [scripts/rebuild.py](scripts/rebuild.py)
- Shared helpers: [scripts/config.py](scripts/config.py),
  [scripts/db.py](scripts/db.py), [scripts/ids.py](scripts/ids.py),
  [scripts/init_db.py](scripts/init_db.py)
