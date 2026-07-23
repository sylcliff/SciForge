# SQLite schema

The library's canonical index. Written by `scripts/init_db.py`. Bumped
via `PRAGMA user_version` alternative in the `meta` table.

## Tables

- `meta(key, value)` — key-value store. `schema_version=1` written on init.
- `papers` — one row per paper. Primary key is `citekey`. Uniques on
  `doi`, `arxiv_id`, `s2_paper_id`. Fields: `title`, `abstract`, `year`,
  `venue`, `venue_full`, `url`, `pdf_path`, `notes_path`, `source`,
  `added_at`, `updated_at`.
- `authors(id, full_name, last_name, orcid)` + `paper_authors(citekey,
  author_id, position, is_corresponding)` — many-to-many. `position`
  is 1-indexed and preserves the byline order.
- `tags(id, name, kind)` — `kind` is `'tag'` or `'collection'`.
  `paper_tags(citekey, tag_id)` links them.
- `si_files(id, citekey, path, label, source_url, checksum_sha256)`
- `github_projects(id, citekey, owner, repo, url, stars, latest_release,
  readme_summary, last_checked_at)` — unique on `(citekey, owner, repo)`.
- `news_links(id, citekey, url, title, source_name, published_at, kind,
  discovered_at)` — unique on `(citekey, url)`.
- `citations(citing_citekey, cited_doi, cited_arxiv_id, cited_title,
  cited_citekey)` — reference list from S2 when available.

## FTS5

`papers_fts` mirrors `papers` for full-text search. Columns:
`citekey` (UNINDEXED), `title`, `abstract`, `authors_flat`, `venue`,
`year_str`, `tags_flat`.

Triggers:

- `papers_ai` — insert into papers_fts on paper insert (title/abstract/venue only).
- `papers_ad` — delete matching row on paper delete.
- `papers_au` — delete + reinsert on paper update.

`authors_flat` and `tags_flat` are populated explicitly by `add.py` and
tag operations, respectively — they don't have their own triggers
because authors/tags live in separate tables.

## Pragmas

- `journal_mode = WAL` — safer for a single-user CLI + occasional
  agent concurrency.
- `foreign_keys = ON` — every cascade delete relies on this.

Writes use `BEGIN IMMEDIATE` (`db.Atomic`) so concurrent writers hit
`SQLITE_BUSY` immediately rather than mid-transaction.

## Migrations

Currently at schema v1. When adding tables/columns, bump the version
constant in `scripts/init_db.py`, add an idempotent CREATE for new
tables, and add ALTERs guarded on the current version.
