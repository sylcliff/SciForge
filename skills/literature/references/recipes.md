# Common invocation patterns

## First time on a new machine

```bash
scripts/sf-lit doctor              # check env + DB + converter binaries
scripts/sf-lit init                # creates ./library by default
```

## The two-phase ingest workflow

`add` catalogues; `convert` renders PDF → Markdown. Both are
synchronous; `add` never spawns MinerU/Docling on its own.

```bash
# 1. Ingest metadata + PDF (fast; catalog only)
scripts/sf-lit add \
  --title "Attention Is All You Need" \
  --author "Ashish Vaswani" --author "Noam Shazeer" \
  --year 2017 --venue NeurIPS \
  --arxiv-id 1706.03762 \
  --pdf-path /tmp/attention.pdf \
  --tag ml --tag transformer
# → citekey=vaswani2017attention, md_status=absent

# 2. Render Markdown (spawns MinerU; may take minutes on CPU)
scripts/sf-lit convert vaswani2017attention
# → md_status=ready
```

Two-in-one via sugar flag:

```bash
scripts/sf-lit add --title "…" --pdf-path /tmp/x.pdf --and-convert
```

## Save from a companion skill's JSON output

```bash
# assumes a companion skill like `arxiv-fetch` exists
arxiv-fetch --id 1706.03762 --emit-json --with-pdf /tmp/paper.pdf \
  | scripts/sf-lit add --meta-json - --pdf-path /tmp/paper.pdf --move-pdf
scripts/sf-lit convert vaswani2017attention
```

Or convert in one shot:

```bash
arxiv-fetch --id 1706.03762 --emit-json --with-pdf /tmp/paper.pdf \
  | scripts/sf-lit add --meta-json - --pdf-path /tmp/paper.pdf --move-pdf --and-convert
```

## Switch converters or force re-render

```bash
# Same converter, re-render (e.g. MinerU version bumped)
scripts/sf-lit convert vaswani2017attention --reconvert --force

# Switch this paper to Docling; new + old outputs coexist on disk.
scripts/sf-lit convert vaswani2017attention --converter docling --reconvert
```

## Bring your own converter output

Useful when MinerU ran elsewhere (cluster, Docker, offline machine):

```bash
scripts/sf-lit convert vaswani2017attention \
  --converter mineru \
  --converted-dir /tmp/mineru_output_for_vaswani
```

## Update an existing entry from freshly-fetched data

```bash
arxiv-fetch --id 1706.03762 --emit-json \
  | scripts/sf-lit add --meta-json - --upsert
```

Non-empty fields overwrite; list fields (tags, authors, github, news, si)
are merged as unions. `--upsert` **does not** rerender `paper.md` — if
the PDF changed too, pair with `convert --reconvert`.

## Bulk import from a list

```bash
while IFS= read -r meta_file; do
  scripts/sf-lit add --meta-json "$meta_file" \
    || echo "failed: $meta_file" >&2
done < paper_meta_files.txt

# Then convert everything that still needs it:
scripts/sf-lit list --md-status absent --json \
  | jq -r '.[].citekey' \
  | xargs -n1 scripts/sf-lit convert
```

## Search / read / show / open

```bash
scripts/sf-lit search "attention"                         # BM25 over paper.md
scripts/sf-lit search "attention" --tag ml --year 2015-2024
scripts/sf-lit search "attention" --has-md --json         # only ready papers
scripts/sf-lit search --collection reading-2026           # no query → catalog listing

scripts/sf-lit read vaswani2017attention                  # whole paper.md
scripts/sf-lit read vaswani2017attention --section "3.2 Baselines"
scripts/sf-lit read vaswani2017attention --pages 3-5      # MinerU only
scripts/sf-lit read vaswani2017attention --kind table     # MinerU only
scripts/sf-lit read vaswani2017attention --grep "attention.mechanism"

scripts/sf-lit show vaswani2017attention                  # metadata + MD status
scripts/sf-lit show vaswani2017attention --json | jq .abstract

scripts/sf-lit open vaswani2017attention pdf
scripts/sf-lit open vaswani2017attention md
scripts/sf-lit open vaswani2017attention notes
scripts/sf-lit open vaswani2017attention si:1
```

## MD conversion state

```bash
scripts/sf-lit status vaswani2017attention                # single paper
scripts/sf-lit status vaswani2017attention --json

scripts/sf-lit list --md-status absent                    # papers that still need convert
scripts/sf-lit list --md-status failed                    # last error visible via `show`
scripts/sf-lit list --md-status stale                     # PDF changed since convert
scripts/sf-lit list --md-status ready --json
```

## Take a note from the command line

```bash
scripts/sf-lit note vaswani2017attention --append "Key idea: parallelizable attention."
scripts/sf-lit note vaswani2017attention                     # → prints the notes path
scripts/sf-lit note vaswani2017attention --set-from ./draft-notes.md
```

## Tag / collection / associations

```bash
scripts/sf-lit tag         vaswani2017attention seminal
scripts/sf-lit collection  reading-2026 add vaswani2017attention
scripts/sf-lit add-github  vaswani2017attention --owner tensorflow --repo tensor2tensor --stars 15000
scripts/sf-lit add-news    vaswani2017attention --url https://blog.example.com/x --title "Deep dive" --kind blog
scripts/sf-lit add-si      vaswani2017attention --path /tmp/supplement.pdf --label "SI-1"
```

## Export

```bash
scripts/sf-lit export vaswani2017attention --format bibtex
scripts/sf-lit export --tag ml --format bibtex --out ml.bib
scripts/sf-lit export --collection reading-2026 --format json --out reading.json
scripts/sf-lit export --all --include-file --format bibtex --out all.bib
```

## Precompute a citekey (for a companion skill)

```bash
scripts/sf-lit citekey --author "Vaswani" --year 2017 --title "Attention Is All You Need"
# → vaswani2017attention
```

## Disaster recovery — rebuild DB from disk

`library/papers/<citekey>/metadata.json` is the source of truth for the
catalog. `paper.md` + `converter.json` restore the MD side. If
`index.db` is lost or corrupted:

```bash
rm library/index.db
scripts/sf-lit rebuild-db --dry-run     # preview
scripts/sf-lit rebuild-db               # actually rebuild (catalog + MD)
```

## Switch between local and global library on the fly

```bash
SCIFORGE_CONFIG=~/.config/sciforge/global.toml scripts/sf-lit search "transformer"
```
