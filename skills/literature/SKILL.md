---
name: literature
description: Use when the user wants to manage the local scientific library — saving a paper from prepared metadata or a local PDF, searching what is already saved, or exporting citations. External metadata fetching lives in companion skills.
---

# Literature — local library data management

Manages a personal, PDF-backed library of scientific papers with a
SQLite index. **Scope: local data management only.** External data
fetching (arXiv, DOI, GitHub, news search, PDF text extraction) is
handled by separate companion skills that hand pre-assembled metadata
to this one.

## When to invoke

- User wants to **save** a paper for which metadata is already in hand
  (a JSON blob or explicit field values), or a local PDF file
- User wants to **manage** existing entries — tag, note, collection,
  attach a GitHub repo / news link / SI file
- User wants to **look up** what is already saved — `search`, `show`
- User wants to **cite** what is saved — `export` to BibTeX or JSON

Do NOT invoke this skill to fetch something the user hasn't already
provided. For "save arXiv paper 1706.03762", the agent should first
invoke `arxiv-fetch` (or the equivalent) to pull metadata, then pipe
its output into `litlib add --meta-json -`.

## First-run check

```bash
scripts/litlib doctor            # verify env + DB
scripts/litlib init              # only if doctor says the library is missing
```

`init` is idempotent — safe to call anytime.

## Ingestion — four entry points

External skills (or the user directly) route into the library through:

| Caller has | Command |
|---|---|
| A metadata JSON blob | `scripts/litlib add --meta-json <path or ->` |
| Explicit fields on the CLI | `scripts/litlib add --title "..." [--author X --arxiv-id Y ...]` |
| Minimal info (fill in later) | `scripts/litlib add --title "..." --manual` |
| A pre-written `metadata.json` under `library/papers/<key>/` | `scripts/litlib rebuild-db` |

Attach a PDF to any of the first three with `--pdf-path <path>`. The
file is **copied** by default; `--move-pdf` moves it.

To refresh an existing entry, add `--upsert`. Non-empty fields overwrite;
list fields (`tags`, `collections`, `authors`, `github`, `news`, `si`)
are merged as unions. Never destructive.

The JSON schema for `--meta-json` is in
[references/ingest-interface.md](references/ingest-interface.md).

## Routing

**Ingestion:**

| User asks for | Command |
|---|---|
| Save with CLI fields | `scripts/litlib add --title "..." --author ...` |
| Save from JSON | `scripts/litlib add --meta-json <path or ->` |
| Save this local PDF | `scripts/litlib add --title ... --pdf-path P` |
| Save from arXiv / DOI link | **First run a fetch skill**, then pipe into `add --meta-json -` |

**Read / write / export:**

| User asks for | Command |
|---|---|
| List / find | `scripts/litlib search <query> [--tag --year --author --has-pdf]` |
| Show one paper | `scripts/litlib show <citekey \| arxiv_id \| doi>` |
| Cite | `scripts/litlib export <selector> --format bibtex` |
| Open PDF / notes / repo | `scripts/litlib open <key> [pdf\|notes\|si\|si:N\|github\|url]` |
| Tag / collection / note | `scripts/litlib tag / collection / note` |
| Attach a GitHub repo | `scripts/litlib add-github <key> --owner O --repo R` |
| Attach a news link | `scripts/litlib add-news <key> --url U [--kind blog]` |
| Attach a SI file | `scripts/litlib add-si <key> --path P` |

## Interaction rules

- **Duplicate detection.** If an incoming paper's `arxiv_id`, `doi`,
  `s2_paper_id`, or explicit `citekey` already exists, `add` exits 2
  and prints the existing citekey. Pass `--upsert` to merge instead.
- **Overwrite guard.** `add-si --path P` copies P into the library; it
  never overwrites `paper.pdf` or the sidecar. For a fresh PDF on an
  ingested paper, use `add --arxiv-id X --pdf-path P --upsert`.
- **PDF integrity.** `--pdf-path` rejects missing or zero-byte files.

## Output rules

`add` prints machine-readable lines for programmatic use:

```
citekey=<key>
pdf=<absolute path or "(not provided)">
notes=<absolute path>
upsert=1                     # present only when --upsert merged
```

`show` renders a compact markdown card; pass `--json` for the full record.

## Companion skill contract

External fetch skills should:

1. Emit a JSON blob matching `references/ingest-interface.md`.
2. Optionally download the PDF to a temp path; pass `--pdf-path`.
3. Precompute the citekey via `scripts/litlib citekey ...` if they
   need it before ingest completes.

Example (assumes `arxiv-fetch` exists):

```bash
arxiv-fetch --id 1706.03762 --emit-json --with-pdf /tmp/paper.pdf \
  | scripts/litlib add --meta-json - --pdf-path /tmp/paper.pdf --move-pdf
```

## Reference documents

Read on demand:

- [references/schema.md](references/schema.md) — SQLite DDL and FTS triggers
- [references/config.md](references/config.md) — config keys
- [references/ingest-interface.md](references/ingest-interface.md) — JSON schema
- [references/recipes.md](references/recipes.md) — invocation patterns
- [references/bibtex.md](references/bibtex.md) — BibTeX mapping and citekey rules

## Verification

```bash
scripts/litlib init --path /tmp/testlib
scripts/litlib doctor
echo "%PDF-1.4 fake" > /tmp/fake.pdf
scripts/litlib add --title "Test paper" --author "Alice B Smith" --year 2024 \
    --arxiv-id 2401.00001 --pdf-path /tmp/fake.pdf --tag test
echo '{"title":"NumPy","authors":["Charles Harris"],"year":2020,"doi":"10.1038/x"}' \
  | scripts/litlib add --meta-json -
scripts/litlib search "test"
scripts/litlib show smith2024test --json
scripts/litlib export --all --format bibtex
```

Modules live under `scripts/`: entry point
[scripts/litlib](scripts/litlib); ingestion
[scripts/add.py](scripts/add.py); read/render
[scripts/search.py](scripts/search.py),
[scripts/show.py](scripts/show.py); relations
[scripts/associate.py](scripts/associate.py); export / open / rebuild
[scripts/export_lib.py](scripts/export_lib.py),
[scripts/open_file.py](scripts/open_file.py),
[scripts/rebuild.py](scripts/rebuild.py); shared helpers
[scripts/config.py](scripts/config.py),
[scripts/db.py](scripts/db.py),
[scripts/ids.py](scripts/ids.py),
[scripts/init_db.py](scripts/init_db.py).
