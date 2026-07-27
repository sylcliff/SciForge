---
name: remote-parse
description: Parse PDFs to Markdown + structured JSON on an SSH-reachable server using MinerU (pipeline backend, CPU-only, defaults to 8-way parallelism). Backend abstraction — currently supports a generic `server` backend. Use whenever the user wants to turn one or many PDFs (remote or local) into machine-readable Markdown for downstream analysis — literature reading, RAG, LLM ingestion, figure/table extraction. Trigger on phrases like "解析 PDF", "convert PDF to markdown", "run mineru", "parse this paper", "batch parse literature", or when a follow-up on `remote-paper` wants the downloaded PDFs turned into text.
allowed-tools: Bash, Read
---

# remote-parse

Parse PDFs into Markdown + structured JSON on an SSH-reachable **remote
server** using [MinerU](https://github.com/opendatalab/MinerU).

The skill is **backend-oriented**: a thin `parse.sh` / `ensure_setup.sh`
dispatcher parses `--backend <name>` and delegates all real work to
`scripts/backends/<name>-{parse,setup}.sh`. Adding a second server means
adding two backend files — no changes to the main scripts.

**Currently supported backend:** `server` — a generic SSH-reachable Linux
host with MinerU installed in a conda env. Configure via `SERVER_*`
environment variables (see below) and an entry in `~/.ssh/config` for the
host alias.

The reference target is a shared login node with ~40 cores, ~180 GB RAM,
no GPU — this skill runs MinerU's `pipeline` backend on CPU and fans out
with **8-way parallelism by default**, which uses ~12–16 cores and ~24–32
GB RAM and leaves the login node responsive for other users.

## When to use

- User asks to convert one or more PDFs to Markdown / structured text.
- A batch is already staged on the server and needs turning into MD.
- Follow-up to `remote-paper`: papers were downloaded, now the user wants them parsed.
- User has local PDFs and wants them parsed via the server (this skill will upload them first).

## Configuration (defaults, override with env vars)

Backend-agnostic:

| Setting | Default | Env var |
|---|---|---|
| Parallelism | `8` | `REMOTE_PARSE_PARALLEL` |
| Per-PDF timeout (seconds) | `1200` (20 min) | `REMOTE_PARSE_TIMEOUT` |
| Local pull dir | `D:\remote_parse` | `REMOTE_PARSE_LOCAL_DIR` |
| OCR language | `ch` | `REMOTE_PARSE_LANG` |

Backend `server` specific:

| Setting | Default | Env var |
|---|---|---|
| SSH host / alias | `server` | `SERVER_HOST` |
| Conda env name | `minerU` | `SERVER_MINERU_ENV` |
| Conda root on server | `$HOME/miniconda3` | `SERVER_CONDA_ROOT` |
| Remote output dir | `$HOME/mineru_out` | `SERVER_MINERU_OUT` |
| Remote upload inbox | `$HOME/mineru_inbox` | `SERVER_MINERU_INBOX` |
| MinerU model source | `modelscope` | `MINERU_MODEL_SOURCE` |

Set these in your shell profile (they never enter git). Example:

```bash
export SERVER_HOST=my-hpc                    # matches your ~/.ssh/config alias
export SERVER_CONDA_ROOT=/opt/anaconda3
export SERVER_MINERU_OUT=/scratch/mineru_out
```

Rationale for the defaults:
- **`modelscope`**: ModelScope is reachable from most CN academic networks; HuggingFace often isn't. Override with `MINERU_MODEL_SOURCE=huggingface` on hosts with global network access.
- **`pipeline` backend** (not `hybrid-engine`): hybrid needs a GPU; pipeline is CPU-only and battle-tested. Each instance uses ~1.5–2 cores and 3–4 GB RAM.
- **Parallelism 8**: at 8-way, resource use is 12–16 cores / 24–32 GB — safe on a login node under normal load. Going higher risks starving other users and BLAS threads start contending. Push to 12–16 only when the node is idle *and* you have exclusive claim.
- **OCR language `ch`**: MinerU's Chinese model handles English text fine (slightly slower OCR); it's the safest default for a mixed-language literature corpus. Use `--lang en` for pure English batches.

## How to run

**Step 1 — first parse of a session: verify the environment.** Idempotent; skip on subsequent calls in the same session.

```bash
bash skills/remote-parse/scripts/ensure_setup.sh --backend server
```

It checks: MinerU conda env exists, `mineru` CLI works, ModelScope is reachable, remote output dir is writable, disk space is OK. If the model cache is empty/tiny it warms it up with a single serial parse so the first real batch doesn't have every worker fighting to download the same weights.

**Step 2 — parse PDFs.** The `parse.sh` script accepts three input shapes:

```bash
# a) explicit remote PDF paths (already on the server)
bash skills/remote-parse/scripts/parse.sh --backend server \
  "$SERVER_MINERU_OUT/../papers/paper1.pdf" \
  "$SERVER_MINERU_OUT/../papers/paper2.pdf"

# b) a remote directory (parses every *.pdf inside, non-recursive)
bash skills/remote-parse/scripts/parse.sh --backend server \
  --dir '<REMOTE_PDF_DIR>'

# c) local PDFs — uploaded to the server first (via rsync), then parsed
bash skills/remote-parse/scripts/parse.sh --backend server \
  --upload /d/my_papers/*.pdf
```

Common flags (all backend-agnostic):

| Flag | Meaning |
|---|---|
| `--parallel N` / `-j N` | Override default 8 |
| `--out <path>` | Override remote output dir |
| `--pull` | **Default.** After parsing, copy full results (md + json + images + layout pdfs) for **this batch** back to `REMOTE_PARSE_LOCAL_DIR` |
| `--pull-md-only` | After parsing, only pull the `.md` files (small, fast) — great for RAG ingestion |
| `--no-pull` | Skip the copy-back step; results stay on the server |
| `--resume` | Skip PDFs whose output already exists (default behavior) |
| `--force` | Re-parse even if output exists |
| `--lang <code>` | OCR language (default `ch`); e.g. `en`, `japan`, `ch_en` |

Pull is **scoped to this batch's PDFs**: only the subdirectories under `<out>` that this invocation produced (or skipped as already-done) are copied back, not the entire remote output tree.

Each PDF produces (in `<out>/<pdf_basename>/auto/`):

- `<name>.md` — main Markdown output
- `<name>_content_list.json` / `_middle.json` — structured content (blocks, positions)
- `<name>_layout.pdf` / `_span.pdf` — visualization of what MinerU detected (useful QA)
- `images/*.jpg` — extracted figures / tables

The script prints a per-file summary (pages, MD size, time) and an aggregate table at the end.

## Key facts baked in (don't re-discover)

- **`mineru` starts its own local FastAPI worker per invocation.** That's why one instance is ~1.5–2 cores instead of just 1 — there's the CLI + a subprocess model server. Do NOT increase `OMP_NUM_THREADS` etc. inside each invocation, the internal pool already covers that; the outer parallelism is what scales throughput.
- **Model cache lives at `~/.cache/modelscope/` on the server.** First run downloads ~2 GB of models; subsequent runs reuse them. `ensure_setup.sh` warms the cache with a small dummy PDF if models are missing, so the first real batch doesn't have every worker fighting to download the same weights.
- **Parallelism is done with `xargs -P N`, not GNU parallel.** GNU parallel isn't guaranteed to be present on every login node; `xargs` is. Each job writes to its own subdir under the output root, so no locking is needed.
- **`--upload` uses rsync**, not scp. This avoids per-file overhead, supports resume after network hiccups, and handles large batches gracefully. For really big trees, do the rsync manually first and then use `--dir`.
- **Failures are per-PDF, not fatal to the batch.** A single PDF that crashes MinerU (rare — sometimes on truly weird scanned PDFs) leaves an `ERROR` marker file and the batch continues. The final summary lists which ones failed.
- **Resume by default.** If `<out>/<name>/auto/<name>.md` already exists, that PDF is skipped. Use `--force` to re-parse. This makes it safe to Ctrl-C and re-run.
- **Login-node etiquette.** 8-way parallelism is the default because it's what leaves headroom for the login node under normal load. If you see `load average > 30`, drop to 4 or submit via a batch scheduler.
- **~3.5 minutes per 20-page PDF** in pipeline / CPU on a typical Xeon-class core; heavily formula-dense papers (e.g. 300+ equations) can take 10–15 minutes each because MFR (Math Formula Recognition) is the most expensive stage.

## When to prefer a batch scheduler (SLURM/PBS/etc.)

If any of these are true, stop using the login node and submit a proper job:

- The batch is > 100 PDFs.
- `uptime` shows the login-node load average > 25.
- The user explicitly asks for GPU / hybrid-engine (that backend needs a compute node with a GPU).

Tell the user, and offer to write a batch-scheduler wrapper. This skill deliberately doesn't ship one because the exact partition / account / walltime settings depend on the cluster and only surface when there's a real batch to submit.

## Troubleshooting

- **"mineru: command not found"** — the conda env activation failed. Re-run `ensure_setup.sh --backend server`; check that `$SERVER_CONDA_ROOT/envs/$SERVER_MINERU_ENV` exists on the server.
- **First worker hangs at "Downloading model..."** — ModelScope is slow or unreachable. Wait; if truly stuck, run `ensure_setup.sh --backend server` which warms the cache serially before spawning workers.
- **"Table-ocr" loops forever on one PDF** — that PDF likely has malformed embedded fonts. Kill the batch, add `--start N --end M` inside `server-parse.sh` (edit) to skip the bad page range, or drop the PDF and note it.
- **`--upload` slow** — rsync should handle it, but Windows-side ssh can be slow. For really large trees, rsync manually first and then run with `--dir`.
- **`.md` output has garbled Chinese or wrong-language OCR** — mostly an OCR-language mismatch. Default is `-l ch`; for English-only papers pass `--lang en`.

## Relationship to `remote-paper`

`remote-paper` downloads PDFs from journals to the server. After that runs, this skill is the natural next step:

```bash
# 1) download 10 papers with remote-paper
bash skills/remote-paper/scripts/fetch.sh --backend server <DOI1> <DOI2> ...

# 2) parse them all to markdown, pull md-only back for RAG
bash skills/remote-parse/scripts/parse.sh --backend server \
  --dir "$REMOTE_PAPER_REMOTE_DIR" \
  --pull-md-only
```

## Script inventory

| File | Purpose |
|---|---|
| `scripts/parse.sh` | Main dispatcher: parses `--backend <name>`, delegates to `backends/<name>-parse.sh`. |
| `scripts/ensure_setup.sh` | Main dispatcher: parses `--backend <name>`, delegates to `backends/<name>-setup.sh`. |
| `scripts/backends/server-parse.sh` | Generic `server` backend parser: SSH to the host, xargs-driven MinerU workers, per-PDF timeout, resume, optional rsync upload, batch-scoped pull-back. |
| `scripts/backends/server-setup.sh` | Generic `server` backend environment check: conda env, mineru CLI, disk, ModelScope reachability, model-cache warm-up. Idempotent. |

## Adding a new backend

To add e.g. `hpc2`:

1. Write `scripts/backends/hpc2-parse.sh` (same interface as `server-parse.sh`: accepts remote paths / `--dir` / `--upload`, prints `OK|SKIP|FAIL` status lines, exits 0 iff every PDF succeeded).
2. Write `scripts/backends/hpc2-setup.sh` (idempotent environment check for that backend).
3. No changes to the main `parse.sh` / `ensure_setup.sh` — they'll pick up the new backend automatically via the `--backend` value.

The main dispatchers do **not** enforce a whitelist; a missing backend file produces a clean error at invocation time.
