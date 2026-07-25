# Recipes

Common ways to invoke `sf-download`, in rough order of what an agent
should try first.

## Single paper by identifier

The identifier can be a DOI, arXiv ID, OpenAlex ID, S2 hash, or any of
their URL forms.

```bash
# Bare arXiv ID
scripts/sf-download 1706.03762

# DOI
scripts/sf-download 10.1038/s41586-020-2649-2

# DOI URL (auto-normalized)
scripts/sf-download https://doi.org/10.1038/s41586-020-2649-2

# arXiv URL (auto-normalized)
scripts/sf-download https://arxiv.org/abs/1706.03762

# OpenAlex work ID
scripts/sf-download W2741809807
```

By default, output is human-oriented (short). Add `--emit-json` for
NDJSON that agents / scripts can parse:

```bash
scripts/sf-download 1706.03762 --emit-json
# {"index":0,"identifier":"1706.03762","status":"downloaded","pdf_path":"...", ...}
```

## Batch — inline list

```bash
scripts/sf-download --ids "1706.03762,10.1038/s41586-020-2649-2,W2741809807" \
    --emit-json
```

Output is 3 per-paper lines (any order — read `index` to reconstruct)
plus one summary line.

## Batch — from file

`sf-download` reads one identifier per line, blanks and `#`-prefixed
lines ignored:

```bash
cat > /tmp/ids.txt <<'EOF'
# ML classics
1706.03762
1810.04805
2005.14165

# Nature paper
10.1038/s41586-020-2649-2
EOF

scripts/sf-download --from-file /tmp/ids.txt --emit-json
```

## Title fallback

Use when you have only a title and no ID. Strict top-1 exact match
(see [output-schema.md#title-fallback](output-schema.md#title-fallback)):

```bash
scripts/sf-download --title "Attention Is All You Need" --emit-json
```

If ambiguous, output looks like:

```json
{"index":0,"identifier":"attention","status":"title_ambiguous",
 "candidates":[{"title":"Attention Is All You Need","doi":"...","year":2017,
                "first_author":"Ashish Vaswani"}, ...]}
```

Pick a DOI from `candidates` and re-invoke `sf-download <doi>`.

## Custom output directory

```bash
# One-off override
scripts/sf-download 1706.03762 --out ./papers

# Persistent override
export SCIFORGE_DOWNLOAD_DIR=/mnt/nas/papers
scripts/sf-download 1706.03762
```

Absent both, files land at `~/.sciforge/inbox/`.

## Pipe to sf-lit

The primary use case. Single paper, all-in-one:

```bash
scripts/sf-download 1706.03762 --emit-json > /tmp/r.jsonl
jq -c 'select(.status=="downloaded") | .meta' /tmp/r.jsonl \
  | ../literature/scripts/sf-lit add --meta-json - \
      --pdf-path "$(jq -r 'select(.status=="downloaded") | .pdf_path' /tmp/r.jsonl)" \
      --move-pdf --and-convert
```

The `--move-pdf` flag on `sf-lit add` moves (not copies) the file from
the inbox into the library, so nothing lingers under `~/.sciforge/inbox/`.

### Batch — using a shell loop

```bash
scripts/sf-download --from-file /tmp/ids.txt --emit-json \
  | jq -c 'select(.status=="downloaded")' \
  | while read -r line; do
      pdf=$(echo "$line" | jq -r .pdf_path)
      echo "$line" | jq -c .meta \
        | ../literature/scripts/sf-lit add --meta-json - \
            --pdf-path "$pdf" --move-pdf --and-convert
    done
```

Failures (`paywalled`, `metadata_only`, `identifier_not_found`, …) are
filtered out by the `jq select`. To also ingest metadata-only papers:

```bash
scripts/sf-download --from-file /tmp/ids.txt --emit-json \
  | jq -c 'select(.status=="downloaded" or .status=="metadata_only")' \
  | while read -r line; do
      status=$(echo "$line" | jq -r .status)
      pdf=$(echo "$line" | jq -r .pdf_path)
      if [ "$status" = "downloaded" ]; then
        echo "$line" | jq -c .meta \
          | ../literature/scripts/sf-lit add --meta-json - \
              --pdf-path "$pdf" --move-pdf --and-convert
      else
        echo "$line" | jq -c .meta \
          | ../literature/scripts/sf-lit add --meta-json - --manual
      fi
    done
```

## Feeding `sf-download` from a search skill

Discovery skills (`paperhound`, future `sf-search`, etc.) should emit
identifier lists. Compose them like this:

```bash
# Hypothetical discovery skill produces one identifier per line
some-search-skill --topic "transformers in genomics" --count 20 \
  | scripts/sf-download --from-file /dev/stdin --emit-json \
  | jq -c 'select(.status=="downloaded")' \
  | ... # pipe to sf-lit as above
```

## Doctor

Environment self-check. Prints resolved config and per-source
reachability + latency:

```bash
scripts/sf-download doctor
```

Sample output:

```
polite_email          you@example.com                            [ok]
s2_api_key            (unset)                                    [warn: 429 more likely]
http_timeout_seconds  30
download_dir          /home/you/.sciforge/inbox                  [ok, writable]
max_concurrency       4

reachability          https://export.arxiv.org                   [ok, 187ms]
reachability          https://api.crossref.org                   [ok, 92ms]
reachability          https://api.unpaywall.org                  [ok, 231ms]
reachability          https://api.openalex.org                   [ok, 156ms]
reachability          https://api.semanticscholar.org            [ok, 402ms]
```

`doctor` never exits non-zero on warnings; it exits `0` if the process
started fine, or `1` on an internal crash. Fatal-looking states
(`polite_email` unset, S2 key unset) are reported as `[warn: …]`.

## Debugging a stuck paper

If a batch shows `status=network_error` or `status=rate_limited` for
a paper, re-run just that one:

```bash
scripts/sf-download 10.xxxx/that-one --emit-json
```

Single-paper mode uses the same code path but you see one line only,
and the exit code carries an unambiguous signal (`0` if OK, `3` if
missing, `2` if malformed).

Read the `sources_queried` field to see which sources were reached and
which were skipped. If Unpaywall isn't in the list and you know the
paper is OA, you probably need to set `SCIFORGE_POLITE_EMAIL`.

## Reproducible testing

Fixture responses live under `tests/fixtures/responses/`. To capture a
new one for the test suite, use `--emit-json` and pipe through `jq -S`
(sort keys) to keep diffs stable. Never commit responses with your
polite email — sanitize before committing.
