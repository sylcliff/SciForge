---
name: literature
description: "Manage a local scientific library: catalog prepared metadata and PDFs, convert PDFs to searchable Markdown, search or read saved papers, and maintain citations or attachments. Use only after external fetch skills have produced local inputs."
---

# Literature

Use `./scripts/sf-lit` to manage the local PDF-backed SQLite library. This
skill is **local-only**: fetching metadata or PDFs belongs to companion
skills, which hand this skill prepared JSON and a local PDF path.

## Fast path

Run the narrowest command that completes the request. Do not run `doctor`,
`status`, or `show` before a normal command; commands validate their own
inputs. Run `doctor` only for setup or converter diagnosis, and `init` only
when a command reports that the library is missing.

Keep cataloguing and conversion separate unless the user requests both:

```bash
./scripts/sf-lit add --meta-json META.json --pdf-path PAPER.pdf
./scripts/sf-lit convert CITEKEY
```

`add` is fast and produces `md_status=absent`. `convert` invokes MinerU or
Docling synchronously and may take minutes. `add --and-convert` is the
explicit combined path.

## Route the request

| Intent | Command |
|---|---|
| Add prepared JSON + PDF | `./scripts/sf-lit add --meta-json PATH_OR_- --pdf-path P` |
| Add explicit fields + PDF | `./scripts/sf-lit add --title T --author A --year Y --pdf-path P` |
| Add placeholder without PDF | `./scripts/sf-lit add --title T --manual` |
| Convert an added paper | `./scripts/sf-lit convert KEY [--converter mineru\|docling]` |
| Import existing converter output | `./scripts/sf-lit convert KEY --converted-dir DIR` |
| Full-text or filtered search | `./scripts/sf-lit search [QUERY] [--tag T --year Y-Y --author A --has-md]` |
| Read whole paper | `./scripts/sf-lit read KEY` |
| Read a section | `./scripts/sf-lit read KEY --section S` |
| Read MinerU pages or blocks | `./scripts/sf-lit read KEY --pages 3-5` / `--kind table` |
| Regex search within one paper | `./scripts/sf-lit read KEY --grep RE` |
| Inspect one record or MD state | `./scripts/sf-lit show KEY` / `status KEY` |
| List conversion state | `./scripts/sf-lit list --md-status absent\|ready\|failed\|stale` |
| Export citations | `./scripts/sf-lit export SELECTOR --format bibtex\|json` |
| Maintain tags, collections, notes, or attachments | `./scripts/sf-lit tag\|collection\|note\|add-github\|add-news\|add-si ...` |
| Open a saved target | `./scripts/sf-lit open KEY [pdf\|md\|notes\|si:N\|github\|url]` |

Use `--json` only when structured output is needed for another step. Avoid
dumping a whole paper when a section, page range, block kind, or grep answers
the request.

## Ingest contract

External fetch skills must provide:

1. Metadata matching [the ingest interface](./references/ingest-interface.md).
2. A non-empty local PDF, unless the user explicitly wants `--manual`.
3. Optionally, a citekey generated with `sf-lit citekey ...`.

When the source is an arXiv ID, DOI, URL, or other remote identifier, run the
appropriate fetch skill first. Do not make network requests from this skill.

`add` copies the PDF by default; `--move-pdf` transfers it. A duplicate
`arxiv_id`, DOI, Semantic Scholar ID, or citekey exits `2` and prints the
existing citekey. Use `--upsert` only when merging into that record is intended.

## Conversion contract

- A bare `convert` refuses a paper already marked `ready`.
- `--reconvert` retries or rerenders; unchanged PDF + converter + version is a
  fast no-op. Add `--force` only when a real rerun is intended.
- `--converted-dir DIR` imports output already produced elsewhere and avoids
  launching a converter.
- Each paper has one canonical `paper.md`; switching converters preserves both
  converter output trees and changes the canonical copy.
- SI attachments are never converted or indexed. Add an SI PDF as a separate
  paper when it must be searchable.

Conversion is complete only when the command exits `0` and prints
`md_status=ready` or `action=noop`. On failure, report the converter error and
leave the persisted `failed` state intact for diagnosis.

## State and completion

`absent` means metadata exists but full text is not searchable; `ready` means
canonical Markdown is indexed; `failed` records the last conversion error;
`stale` means the PDF or Markdown no longer matches the recorded conversion.

Finish only when the requested mutation or query command exits `0` and its
output identifies the affected citekey(s) or requested results. Do not add a
second verification command unless the first command's output is insufficient
or the user asked for verification.

## Output contract

Exit `0` means success, `2` invalid input, `3` missing resource, `4` a
destructive action refused without `--force`, and `1` or `>=64` a runtime
failure. Human-readable output is the default; use supported `--json` flags for
machine handoffs.

This domain skill owns `sciforge://literature/<citekey>`. Resolve it with
`./scripts/sf-lit show KEY --json`, which returns at least:

```json
{"id":"KEY","type":"paper","uri":"sciforge://literature/KEY"}
```

`library/papers/<citekey>/metadata.json`, `paper.md`, and `converter.json` are
the durable record; `library/index.db` is rebuildable with `rebuild-db`.

## References

Load only the reference needed by the active branch:

- Metadata fields and companion handoff:
  [references/ingest-interface.md](./references/ingest-interface.md)
- Configuration and converter commands:
  [references/config.md](./references/config.md)
- Detailed examples and bulk workflows:
  [references/recipes.md](./references/recipes.md)
- Database or status internals:
  [references/schema.md](./references/schema.md)
- BibTeX mapping and citekey rules:
  [references/bibtex.md](./references/bibtex.md)

The full operator manual and command inventory live in [README.md](README.md).
