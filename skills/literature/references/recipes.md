# Common invocation patterns

## First time on a new machine

```bash
scripts/litlib doctor              # check env + DB
scripts/litlib init                # creates ./library by default
```

## Save a paper from CLI flags (no external fetch)

```bash
scripts/litlib add \
  --title "Attention Is All You Need" \
  --author "Ashish Vaswani" --author "Noam Shazeer" \
  --year 2017 --venue NeurIPS \
  --arxiv-id 1706.03762 \
  --pdf-path /tmp/attention.pdf \
  --tag ml --tag transformer
```

## Save from a companion skill's JSON output

```bash
# assumes a companion skill like `arxiv-fetch` exists
arxiv-fetch --id 1706.03762 --emit-json --with-pdf /tmp/paper.pdf \
  | scripts/litlib add --meta-json - --pdf-path /tmp/paper.pdf --move-pdf
```

Or from a static file:

```bash
scripts/litlib add --meta-json ./paper-metadata.json --pdf-path /tmp/paper.pdf
```

## Update an existing entry from freshly-fetched data

```bash
arxiv-fetch --id 1706.03762 --emit-json \
  | scripts/litlib add --meta-json - --upsert
```

Non-empty fields overwrite; list fields (tags, authors, github, news, si)
are merged as unions.

## Bulk import from a list

```bash
while IFS= read -r meta_file; do
  scripts/litlib add --meta-json "$meta_file" \
    || echo "failed: $meta_file" >&2
done < paper_meta_files.txt
```

## Take a note from the command line

```bash
scripts/litlib note vaswani2017attention --append "Key idea: parallelizable attention."
scripts/litlib note vaswani2017attention                     # → prints the notes path
scripts/litlib note vaswani2017attention --set-from ./draft-notes.md
```

## Tag / collection / associations

```bash
scripts/litlib tag         vaswani2017attention seminal
scripts/litlib collection  reading-2026 add vaswani2017attention
scripts/litlib add-github  vaswani2017attention --owner tensorflow --repo tensor2tensor --stars 15000
scripts/litlib add-news    vaswani2017attention --url https://blog.example.com/x --title "Deep dive" --kind blog
scripts/litlib add-si      vaswani2017attention --path /tmp/supplement.pdf --label "SI-1"
```

## Search / show / open

```bash
scripts/litlib search "attention" --tag ml
scripts/litlib search --collection reading-2026 --year 2015-2024
scripts/litlib show vaswani2017attention
scripts/litlib show vaswani2017attention --json | jq .abstract
scripts/litlib open vaswani2017attention pdf
scripts/litlib open vaswani2017attention notes
scripts/litlib open vaswani2017attention si:1
```

## Export

```bash
scripts/litlib export vaswani2017attention --format bibtex
scripts/litlib export --tag ml --format bibtex --out ml.bib
scripts/litlib export --collection reading-2026 --format json --out reading.json
scripts/litlib export --all --include-file --format bibtex --out all.bib
```

## Precompute a citekey (for a companion skill)

```bash
scripts/litlib citekey --author "Vaswani" --year 2017 --title "Attention Is All You Need"
# → vaswani2017attention
```

## Disaster recovery — rebuild DB from sidecars

`library/papers/<citekey>/metadata.json` is the source of truth. If
`index.db` is lost or corrupted:

```bash
rm library/index.db
scripts/litlib rebuild-db --dry-run     # preview
scripts/litlib rebuild-db               # actually rebuild
```

## Switch between local and global library on the fly

```bash
SCIFORGE_CONFIG=~/.config/sciforge/global.toml scripts/litlib search "transformer"
```
