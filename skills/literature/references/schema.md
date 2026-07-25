# SQLite schema

The library's canonical index. Written by `scripts/init_db.py`. Schema
version tracked in the `meta` table (`schema_version`).

The v2 schema is **MD-first**: full-text search runs against Markdown
rendered from each paper's PDF (`paper.md`), not against structured
metadata fields. Metadata is still used for **WHERE filters**
(year / tag / author / has-md) but does not participate in ranking.

## Tables

### Catalog (metadata layer)

- `meta(key, value)` — key-value store. `schema_version=2` written on init.
- `papers` — one row per paper. Primary key is `citekey`. Uniques on
  `doi`, `arxiv_id`, `s2_paper_id`. Fields:
  - `title`, `abstract`, `year`, `venue`, `venue_full`, `url`
  - `pdf_path` — relative to library root (e.g. `papers/<key>/paper.pdf`)
  - `notes_path` — relative to library root
  - `source` — how the row was created (`meta-json`, `cli`, `manual`,
    `sidecar`)
  - `added_at`, `updated_at`
  - **`md_status`** ∈ `{absent, ready, failed, stale}` — see below
  - **`md_last_error`** — populated when `md_status='failed'`
- `authors(id, full_name, last_name, orcid)` + `paper_authors(citekey,
  author_id, position, is_corresponding)` — many-to-many. `position`
  is 1-indexed and preserves the byline order.
- `tags(id, name, kind)` — `kind` is `'tag'` or `'collection'`.
  `paper_tags(citekey, tag_id)` links them.
- `si_files(id, citekey, path, label, source_url, checksum_sha256)` —
  SI attachments; **not** part of MD full-text (see SKILL.md).
- `github_projects(id, citekey, owner, repo, url, stars, latest_release,
  readme_summary, last_checked_at)` — unique on `(citekey, owner, repo)`.
- `news_links(id, citekey, url, title, source_name, published_at, kind,
  discovered_at)` — unique on `(citekey, url)`.
- `citations(citing_citekey, cited_doi, cited_arxiv_id, cited_title,
  cited_citekey)` — reference list when available.

### MD content store & FTS

- `papers_md(citekey, markdown, converter, converter_version,
  converted_at, pdf_sha256, char_count)` — one row per paper that has a
  canonical `paper.md` ingested. `citekey` is both PK and FK to
  `papers.citekey` with ON DELETE CASCADE.
- `papers_md_fts` — FTS5 virtual table over `papers_md.markdown`,
  external-content mode (`content='papers_md'`,
  `content_rowid='rowid'`). Tokenizer:
  `porter unicode61 remove_diacritics 2` — English stemming plus
  Unicode-aware, diacritic-folded segmentation.
- Sync triggers `papers_md_ai` / `papers_md_ad` / `papers_md_au` keep
  the FTS index consistent with `papers_md` on INSERT / DELETE / UPDATE.

## `md_status` semantics

| State | Meaning | How to change |
|---|---|---|
| `absent` | `paper.md` does not exist; not indexed in `papers_md`. Set at ingest for `add --pdf-path` (without `--and-convert`). | `litlib convert <key>` → `ready` |
| `ready` | Canonical `paper.md` exists on disk **and** `papers_md` row present **and** FTS index in sync. Search hits this paper. | Overwritten by `convert --reconvert`, invalidated to `stale` if the PDF changes. |
| `failed` | Last convert attempt raised. `md_last_error` holds a summary. `papers_md` row **not** written. | `litlib convert <key> --reconvert` |
| `stale` | Canonical `paper.md` claims to be current but the PDF sha256 in `converter.json` no longer matches (PDF was replaced, or `paper.md` was hand-edited / deleted). Search still returns hits (the FTS row is still there), but `read` and `status` warn. | `litlib convert <key> --reconvert` |

`status`, `list --md-status <state>`, and the `MD:` line in `show <key>`
are the three ways to inspect this. The `ready` claim is re-validated
by `status` on each call — it stats the file on disk instead of trusting
the DB blindly.

## No metadata FTS

There is intentionally no `papers_fts` in v2. Title / abstract / venue
words are searchable via `papers_md_fts` **only when the paper has been
converted** — a paper's title always appears in its `paper.md` (as the
top-level heading), so ordinary search covers it. Papers with
`md_status='absent'` are only reachable via structured filters
(`--author`, `--year`, `--tag`, ...) or `show <key>`.

## Pragmas

- `journal_mode = WAL` — safer for a single-user CLI + occasional agent
  concurrency.
- `foreign_keys = ON` — every cascade delete relies on this.

Writes use `BEGIN IMMEDIATE` (`db.Atomic`) so concurrent writers hit
`SQLITE_BUSY` immediately rather than mid-transaction.

## Migrations

Currently at schema v2. When adding tables/columns, bump the version
constant in `scripts/init_db.py`, add an idempotent CREATE for new
tables, and add ALTERs guarded on the current version.
