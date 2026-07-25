# Ingest interface — `add --meta-json` schema

This is the contract between companion skills and `literature`. Any
external skill that fetches metadata (arXiv, DOI, GitHub, news, …)
should emit JSON conforming to this schema and pipe it into
`litlib add --meta-json -`. Companion skills are expected to hand off
**three products**:

1. A **metadata JSON** matching the schema below.
2. A **local PDF path** (non-zero-byte file). Optional only when the
   caller has explicitly declared "no PDF" via CLI-mode `--manual`.
3. A **citekey suggestion** (optional; precompute via
   `litlib citekey --author X --year Y --title Z`).

`add` is strictly the **catalog** step. It never triggers MinerU or
Docling. A freshly-added paper has ``md_status='absent'``. Full-text
search is enabled by a separate `litlib convert <citekey>` call. The
companion contract does **not** require the companion to run
conversion — that is a `literature` responsibility once the paper is
catalogued. Pass `--and-convert` to `add` when you want the two steps
in one command.

## Top-level object

```jsonc
{
  // Required
  "title": "Attention Is All You Need",

  // Optional — every field below is nullable / omitable
  "authors": ["Ashish Vaswani", "Noam Shazeer", "…"],
  "abstract": "The dominant sequence transduction models…",
  "year": 2017,
  "venue": "NeurIPS",                          // short name
  "venue_full": "Advances in Neural Information…",  // full journal/conference name

  "doi": null,                                 // e.g. "10.1038/s41586-020-2649-2"
  "arxiv_id": "1706.03762",                    // no version suffix
  "s2_paper_id": null,                         // Semantic Scholar paper ID
  "url": "https://arxiv.org/abs/1706.03762",

  "notes": "Seed text for notes.md",           // optional initial content

  "tags": ["ml", "transformer"],
  "collections": ["reading-2026"],

  // Nested associations — inline so a single fetch skill can emit it all
  "github": [
    {
      "owner": "tensorflow",
      "repo": "tensor2tensor",
      "url": "https://github.com/tensorflow/tensor2tensor",
      "stars": 15000,
      "latest_release": "v1.15.0",
      "readme_summary": "Library for training deep learning models…",
      "last_checked_at": "2026-07-23T08:00:00Z"
    }
  ],
  "news": [
    {
      "url": "https://example.com/attention-explained",
      "title": "Attention explained",
      "source_name": "Nature News",
      "published_at": "2024-01-15",
      "kind": "blog"        // "news" | "blog" | "discussion" | "video"
    }
  ],
  "si": [
    {
      "path": "/tmp/expanded-supplement.pdf",   // local path to copy in
      "label": "Supplementary Information",
      "url": "https://static-content.springer.com/…/supp.pdf",
      "checksum_sha256": "…"
    }
  ],

  // Optional hint — usually the citekey is generated automatically
  "citekey": "vaswani2017attention"
}
```

## Field conventions

- `authors` — simple string per author: `"First Last"` or
  `"Last, First"`. The system normalises both.
- `arxiv_id` — must omit the version suffix (`v1`, `v2`, …). The system
  strips it anyway, but external skills should present the canonical ID.
- `doi` — always lowercased. Leading `https://doi.org/` is tolerated
  but the canonical form is `10.xxxx/…`.
- `github.owner` + `github.repo` — the only required fields in a GitHub
  entry. All others are optional and will be filled lazily on refresh.
- `news.url` — the only required field in a news entry. Title and source
  should be supplied when available.
- `si.path` — if provided, the file is **copied** into the library's SI
  subdirectory. `si.url` stores the original source URL for provenance.
  At least one of `path` or `url` is required.

## SI is not converted

Supplementary information attached via `add-si` or the `si` list in the
metadata blob is stored as-is. It does **not** run through
MinerU/Docling and is **not** part of `paper.md` / `papers_md_fts`. If
you want an SI PDF to be searchable, add it as a separate paper.

## Versioning

This schema is versioned with the library's `schema_version` in `meta`.
The current schema is v2. Bumping the schema version implies the
interface may have changed and companion skills should be updated.

## Example minimal entry

```json
{
  "title": "A quick note",
  "authors": ["A B"],
  "year": 2024,
  "arxiv_id": "2401.00001"
}
```

## Example full entry (emitted by a hypothetical arxiv-fetch skill)

```json
{
  "title": "Attention Is All You Need",
  "authors": ["Ashish Vaswani", "Noam Shazeer", "Niki Parmar",
              "Jakob Uszkoreit", "Llion Jones", "Aidan N. Gomez",
              "Lukasz Kaiser", "Illia Polosukhin"],
  "abstract": "The dominant sequence transduction models…",
  "year": 2017,
  "arxiv_id": "1706.03762",
  "url": "https://arxiv.org/abs/1706.03762",
  "tags": ["nlp", "transformer"],
  "collections": ["deep-learning-basics"],
  "github": [
    {
      "owner": "tensorflow",
      "repo": "tensor2tensor",
      "url": "https://github.com/tensorflow/tensor2tensor"
    }
  ]
}
```
