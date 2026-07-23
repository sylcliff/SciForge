# BibTeX & citekey conventions

## Citekey format

`<lastname><year><firstsigword>`

- `lastname` — slugified last name of the first author. ASCII-only when
  possible; falls back to unicode-lower for CJK / non-Latin scripts.
- `year` — 4-digit publication year, or `nd` (no date) when unknown.
- `firstsigword` — first significant (non-stopword) word from the
  title, slugified. Stopwords: a, an, the, of, on, in, for, and, or,
  to, with, from, by, as, at, is, are, be, this, that, we, our, not,
  how, what, why, when.

Examples:

- Vaswani et al. 2017, "Attention Is All You Need" → `vaswani2017attention`
- Harris et al. 2020, "Array programming with NumPy" → `harris2020array`
- Ho et al. 2020, "Denoising Diffusion Probabilistic Models" → `ho2020denoising`

## Collision handling

Config `[citekey] on_collision`:

- `suffix` (default) — append `_a`, `_b`, ..., `_z`, `_aa`, ...
- `error` — refuse the add; the caller must resolve.

Stability: once a citekey is written, it's stored in `metadata.json`
alongside the paper. `rebuild-db` reads that sidecar and uses the
persisted value — it does not regenerate.

## BibTeX field mapping

Chosen automatically from the paper's `venue`, `venue_full`, and other
fields:

| Paper field | BibTeX field | Notes |
|---|---|---|
| `title` | `title` | wrapped in `{...}` to preserve casing |
| authors (joined by ` and `) | `author` | `Last, First` form when possible |
| `year` | `year` |  |
| `venue_full` (or `venue`) | `journal` / `booktitle` | `journal` for articles, `booktitle` for conferences (best-effort) |
| `doi` | `doi` |  |
| `url` | `url` | canonical arXiv URL, DOI URL, or landing page |
| `arxiv_id` | `eprint` + `archivePrefix = {arXiv}` | when present |
| `pdf_path` (absolute) | `file` | Zotero-style `{:pdf}` suffix — controlled by `[export.bibtex] include_file_field` |

Entry type heuristic:

- `arxiv_id` present, no venue → `@misc` with `archivePrefix = {arXiv}`
- `venue` contains "arXiv" → `@misc`
- conference-like venue (NeurIPS, ICLR, ICML, CVPR, ACL, EMNLP, ...) → `@inproceedings`
- default → `@article`

The heuristic is deliberately simple; users who want tight BibTeX can
edit the cached `bibtex.bib` next to each paper.
