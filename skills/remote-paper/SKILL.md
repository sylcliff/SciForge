---
name: remote-paper
description: Download academic papers (paywalled or open-access) via an SSH-reachable server that sits on an institutional/campus IP, and copy them back to the local machine. Backend abstraction — currently supports `zj` (`zhaojin.ustc.edu.cn`). Use when the user gives a DOI or arXiv ID and wants the PDF, or says things like "下载这篇文献", "get this paper", "帮我下 PRL/JACS/Nature". Handles ACS, APS, Nature, Science, Wiley, Elsevier and more via scansci-pdf.
allowed-tools: Bash, Read
---

# remote-paper

Fetch papers via an SSH-reachable **remote server** that sits on a
university campus IP and therefore has paywalled-journal access. The
server runs `scansci-pdf`; this skill drives it over SSH and copies
finished PDFs back to the local machine.

The skill is **backend-oriented**: a thin `fetch.sh` / `ensure_setup.sh`
dispatcher parses `--backend <name>` and delegates all real work to
`scripts/backends/<name>-{fetch,setup}.sh`. Adding a second server
(e.g. another university) means adding two backend files — no changes
to the main scripts.

**Currently supported backend:** `zj` (`zhaojin.ustc.edu.cn`).

## When to use

Trigger on any request to obtain a paper by identifier that is likely
paywalled (or that `sf-download` has already reported as `paywalled`):

- "下载 10.1021/jacs.4c02086" / "get this DOI" / "帮我下这篇 PRL"
- A bare DOI or arXiv ID in the message
- A list of DOIs to fetch in batch
- `sf-download --fallback-remote zj` — auto-invoked when the OA fetcher
  reports `paywalled` (see `skills/download/SKILL.md`)

## Configuration (defaults, override with env vars)

Backend-agnostic:

| Setting | Default | Env var |
|---|---|---|
| Local save dir | `D:\zj_papers` (`/d/zj_papers`) | `REMOTE_PAPER_LOCAL_DIR` (fallback: `ZJ_LOCAL_DIR`) |
| Download timeout | `240` s | `REMOTE_PAPER_DL_TIMEOUT` (fallback: `ZJ_DL_TIMEOUT`) |

Backend `zj` specific:

| Setting | Default | Env var |
|---|---|---|
| SSH host | `zj` | `ZJ_HOST` |
| Remote work dir | `/public/home/syllzp/scansci_test/dl` | `ZJ_REMOTE_DIR` |

## How to run

**Step 1 — first download of a session: verify the server environment.**
This re-applies the APS/PRL patch if a scansci-pdf upgrade reverted it and
ensures the campus config is correct. Idempotent; skip on later downloads
in the same session.

```bash
bash skills/remote-paper/scripts/ensure_setup.sh --backend zj
```

**Step 2 — fetch one or more papers.** Pass DOIs and/or arXiv IDs as
positional args after `--backend`:

```bash
bash skills/remote-paper/scripts/fetch.sh --backend zj 10.1021/jacs.4c02086
bash skills/remote-paper/scripts/fetch.sh --backend zj 10.1103/PhysRevLett.134.176001 2502.01420
```

The script downloads server-side, copies the PDF back to the local dir,
and prints `pages / size / title` for each. It **auto-retries up to 3
times** on transient failures (see APS note below). Report the summary
and local path to the user.

**Machine-readable output.** On success, the very last stdout line for
each identifier is:

```
PDF_PATH=<absolute local path to the .pdf>
```

Callers (e.g. `sf-download --fallback-remote zj`) grep this to pick up
the file. Failed identifiers print no `PDF_PATH=` line for that item.

## Key facts baked in (do not re-discover)

- **`--strategy legal_only` is mandatory.** The default `fastest` strategy
  hard-runs a 13-source race that initializes Sci-Hub/Tor and floods the
  log with failures (the server can't reach tor mirrors or google).
  `legal_only` uses only the 10 legal institutional sources — clean, fast,
  and it's what gets full text.
- **APS/PRL patch.** `publisher_strategies.py:1663` originally opened
  `https://www.google.com/` as a browser warm-up page, but the server
  can't reach google, so APS/PRL downloads crashed. It's patched to
  `about:blank`. `ensure_setup.sh` re-applies this if an upgrade reverts
  it. Backup: `publisher_strategies.py.bak`.
- **Verified publishers:** ACS (JACS), APS (PRL, after patch), Nature/Springer,
  Science (AAAS), Wiley (Angew), Elsevier — all return full text on
  campus IP.
- **APS/PRL is probabilistic.** Even with the patch, the Cloudflare +
  browser flow fails intermittently (same DOI can fail then succeed).
  `fetch.sh` auto-retries 3×, which recovers almost all cases. If all 3
  fail, just run it again later or use arXiv.
- **arXiv fallback.** If a DOI fails and the paper has an arXiv preprint,
  retry with the arXiv ID (e.g. `bash fetch.sh --backend zj 2502.01420`).
- **Small PDFs (~50KB) are not necessarily failures** — could be a
  genuinely short item (book review, comment). Check the printed
  title/pages, not just size.
- **SSH resets are harmless.** Each download runs to completion server-side
  even if the SSH channel drops; the script re-connects for the copy-back
  step.

## Finding a DOI when the user only gives a title

Use `scansci-pdf search` on the server, or query Crossref, to resolve a
title to a real DOI before downloading. Do not invent DOIs — verify they
exist first.

## Troubleshooting

- **"no PDF produced"** → run `ensure_setup.sh --backend zj`; check the
  DOI is real (Crossref).
- **APS/PRL specifically fails** → the patch was likely reverted by an
  upgrade; `ensure_setup.sh --backend zj` fixes it.
- **APS/PRL fails 3× in a row** → run again later (probabilistic), or
  try the paper's arXiv preprint if it has one.
- **IOP JS challenge / AIP anti-bot** → both put the article behind a
  JavaScript fingerprint check that CloakBrowser 0.5.x can't yet
  auto-resolve. Manual browser download or arXiv preprint is the
  fallback.
- **A new publisher fails** → it may hard-depend on an unreachable
  domain like the old google.com issue; inspect the log and consider a
  similar patch.

## Script inventory

| File | Purpose |
|---|---|
| `scripts/fetch.sh` | Main dispatcher: parses `--backend <name>`, delegates to `backends/<name>-fetch.sh`. |
| `scripts/ensure_setup.sh` | Main dispatcher: parses `--backend <name>`, delegates to `backends/<name>-setup.sh`. |
| `scripts/backends/zj-fetch.sh` | zj backend downloader: SSH to zj, scansci-pdf legal_only, 3× retry, scp back, emit `PDF_PATH=...`. |
| `scripts/backends/zj-setup.sh` | zj backend environment repair: APS google.com patch, campus config, pymupdf install. Idempotent. |

## Adding a new backend

To add e.g. `tsinghua`:

1. Write `scripts/backends/tsinghua-fetch.sh` (same interface as
   `zj-fetch.sh`: takes DOIs/arXiv IDs as positional args, downloads,
   emits final `PDF_PATH=<local absolute path>` per success).
2. Write `scripts/backends/tsinghua-setup.sh` (idempotent environment
   check).
3. No changes to the main `fetch.sh` / `ensure_setup.sh` — they'll pick
   up the new backend automatically via the `--backend` value.

The main dispatchers do **not** enforce a whitelist; a missing backend
file produces a clean error at invocation time.
