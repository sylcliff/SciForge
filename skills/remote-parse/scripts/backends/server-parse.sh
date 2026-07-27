#!/usr/bin/env bash
# server-parse.sh — generic `server` backend for remote-parse.
# Batch-parse PDFs on an SSH-reachable server with MinerU (CPU pipeline),
# fanning out with xargs at configurable parallelism (default 8).
#
# Called by ../parse.sh via --backend server. Accepts all the user-facing
# flags that parse.sh passes through.
#
# Input shapes:
#   server-parse.sh <remote.pdf> [<remote.pdf> ...]      # explicit remote paths
#   server-parse.sh --dir <remote_dir>                   # every *.pdf in dir (non-recursive)
#   server-parse.sh --upload <local.pdf> [<local.pdf>...]# upload local files first, then parse
#
# Common flags:
#   -j / --parallel N        override default parallelism (REMOTE_PARSE_PARALLEL)
#   -o / --out <path>        override remote output dir (SERVER_MINERU_OUT)
#   --pull                   scp full results (md + json + images) back to REMOTE_PARSE_LOCAL_DIR (DEFAULT)
#   --pull-md-only           only pull *.md back (small, fast)
#   --no-pull                leave results on the server
#   --force                  re-parse even if output already exists
#   --resume                 skip PDFs whose output exists (default)
#   --lang <code>            OCR language (REMOTE_PARSE_LANG, default ch)
#                             e.g. en, japan, ch_en
#
# Server configuration (env vars):
#   SERVER_HOST              SSH alias (must be in ~/.ssh/config). Default: server
#   SERVER_MINERU_ENV        conda env name. Default: minerU
#   SERVER_CONDA_ROOT        conda install prefix on the server. Default: $HOME/miniconda3
#   SERVER_MINERU_OUT        remote output dir. Default: $HOME/mineru_out
#   SERVER_MINERU_INBOX      remote upload inbox for --upload. Default: $HOME/mineru_inbox
#
# Exit code: 0 if every PDF succeeded, 1 if any failed. Failures don't abort the batch.
set -uo pipefail

# ---- config (env-overridable) ----
SSH_HOST="${SERVER_HOST:-server}"
ENV_NAME="${SERVER_MINERU_ENV:-minerU}"
CONDA_ROOT="${SERVER_CONDA_ROOT:-\$HOME/miniconda3}"
CONDA_SH="$CONDA_ROOT/etc/profile.d/conda.sh"
REMOTE_OUT_DEFAULT="${SERVER_MINERU_OUT:-\$HOME/mineru_out}"
REMOTE_INBOX="${SERVER_MINERU_INBOX:-\$HOME/mineru_inbox}"
LOCAL_DIR="${REMOTE_PARSE_LOCAL_DIR:-/d/remote_parse}"
PARALLEL_DEFAULT="${REMOTE_PARSE_PARALLEL:-8}"
PER_PDF_TIMEOUT="${REMOTE_PARSE_TIMEOUT:-1200}"
LANG_DEFAULT="${REMOTE_PARSE_LANG:-ch}"
MODEL_SOURCE="${MINERU_MODEL_SOURCE:-modelscope}"
# Filter noisy SSH banners that vary by server; add more patterns as needed.
BANNER='post-quantum\|vulnerable\|openssh.com\|may need to be upgraded'
# ----------------------------------

PARALLEL="$PARALLEL_DEFAULT"
REMOTE_OUT="$REMOTE_OUT_DEFAULT"
MODE=""            # "" | "dir" | "upload"
DIR_ARG=""
FORCE=0
PULL=0
PULL_MD_ONLY=0
NO_PULL=0
LANG_FLAG="$LANG_DEFAULT"
PDFS=()

usage() {
    grep '^#' "$0" | sed 's/^# \{0,1\}//'
    exit 1
}

# --- arg parsing ---
while [ $# -gt 0 ]; do
    case "$1" in
        -h|--help) usage ;;
        -j|--parallel) PARALLEL="$2"; shift 2 ;;
        -o|--out) REMOTE_OUT="$2"; shift 2 ;;
        --dir) MODE="dir"; DIR_ARG="$2"; shift 2 ;;
        --upload) MODE="upload"; shift; while [ $# -gt 0 ] && [ "${1:0:1}" != "-" ]; do PDFS+=("$1"); shift; done ;;
        --force) FORCE=1; shift ;;
        --resume) FORCE=0; shift ;;
        --pull) PULL=1; shift ;;
        --pull-md-only) PULL_MD_ONLY=1; shift ;;
        --no-pull) NO_PULL=1; shift ;;
        --lang) LANG_FLAG="$2"; shift 2 ;;
        --) shift; while [ $# -gt 0 ]; do PDFS+=("$1"); shift; done ;;
        -*) echo "unknown flag: $1" >&2; usage ;;
        *) PDFS+=("$1"); shift ;;
    esac
done

# Determine pull behavior: default is --pull (full pull)
if [ "$PULL" = "0" ] && [ "$PULL_MD_ONLY" = "0" ] && [ "$NO_PULL" = "0" ]; then
    PULL=1
fi

echo "════════════════════════════════════════════════════════"
echo "  remote-parse (server backend)"
echo "  host=$SSH_HOST  parallel=$PARALLEL  lang=$LANG_FLAG"
echo "  remote_out=$REMOTE_OUT"
echo "════════════════════════════════════════════════════════"

# --- upload mode: push local PDFs via rsync ---
if [ "$MODE" = "upload" ]; then
    [ ${#PDFS[@]} -eq 0 ] && { echo "no local PDFs given"; exit 1; }
    ssh -o ConnectTimeout=15 "$SSH_HOST" "mkdir -p '$REMOTE_INBOX'" 2>&1 | grep -v "$BANNER" >/dev/null
    echo "[upload] ${#PDFS[@]} file(s) → $SSH_HOST:$REMOTE_INBOX"
    # rsync avoids per-file scp overhead, supports resume if interrupted,
    # and handles large batches gracefully.
    upload_ok=0; upload_fail=0
    for f in "${PDFS[@]}"; do
        if [ ! -f "$f" ]; then
            echo "  ⚠ not found: $f (skipped)" >&2
            upload_fail=$((upload_fail + 1))
            continue
        fi
        rsync -az --progress "$f" "$SSH_HOST:$REMOTE_INBOX/" 2>&1 | grep -v "$BANNER" | tail -1
        if [ "${PIPESTATUS[0]:-1}" = "0" ]; then
            upload_ok=$((upload_ok + 1))
        else
            echo "  ✗ rsync failed for $f" >&2
            upload_fail=$((upload_fail + 1))
        fi
    done
    echo "[upload] done: uploaded=$upload_ok failed=$upload_fail"
    # Rewrite PDFS[] to remote paths — but only include files that actually
    # uploaded (so mineru doesn't try to open a path that never arrived).
    REMOTE_PDFS=()
    for f in "${PDFS[@]}"; do
        [ -f "$f" ] || continue
        REMOTE_PDFS+=("$REMOTE_INBOX/$(basename "$f")")
    done
    PDFS=("${REMOTE_PDFS[@]}")
    MODE=""
fi

# --- dir mode: expand server-side ---
if [ "$MODE" = "dir" ]; then
    LIST=$(ssh -o ConnectTimeout=15 "$SSH_HOST" \
        "find '$DIR_ARG' -maxdepth 1 -type f -name '*.pdf' | sort" 2>&1 | grep -v "$BANNER")
    if [ -z "$LIST" ]; then
        echo "no PDFs found under $DIR_ARG"; exit 1
    fi
    while IFS= read -r p; do PDFS+=("$p"); done <<< "$LIST"
fi

[ ${#PDFS[@]} -eq 0 ] && { echo "no PDFs given (see --help)"; exit 1; }
echo "[plan] ${#PDFS[@]} PDF(s) to consider"

# --- build worklist on server: skip already-done unless --force ---
WORKLIST_REMOTE="/tmp/remote_parse_work_$$.txt"
{
    for p in "${PDFS[@]}"; do printf '%s\n' "$p"; done
} | ssh -o ConnectTimeout=15 "$SSH_HOST" \
    "cat > $WORKLIST_REMOTE; wc -l < $WORKLIST_REMOTE" 2>&1 | grep -v "$BANNER" >/dev/null

# --- Ship the worker script; xargs will drive it per PDF ---
# Passed args: $1 = pdf path, $2 = out root, $3 = force, $4 = lang, $5 = timeout
WORKER_REMOTE="/tmp/remote_parse_worker_$$.sh"
ssh -o ConnectTimeout=15 "$SSH_HOST" "cat > $WORKER_REMOTE && chmod +x $WORKER_REMOTE" <<'WORKEREOF' 2>&1 | grep -v "$BANNER" >/dev/null
#!/usr/bin/env bash
# One PDF → mineru → status line. Never exits nonzero: the batch continues.
PDF="$1"; OUT_ROOT="$2"; FORCE="$3"; LANG="$4"; TMO="$5"
CONDA_SH="$6"; ENV_NAME="$7"; MODEL_SOURCE="$8"

base=$(basename "$PDF" .pdf)
outdir="$OUT_ROOT/$base"
md_expected="$outdir/auto/${base}.md"

start_ts=$(date +%s)

if [ "$FORCE" = "0" ] && [ -f "$md_expected" ]; then
    printf '%s\tSKIP\t%s\n' "$base" "$md_expected"
    exit 0
fi

# Activate env & run mineru with a per-PDF timeout.
# Log per-PDF into $outdir/mineru.log to keep aggregated stdout tidy.
mkdir -p "$outdir"
{
    source "$CONDA_SH"
    conda activate "$ENV_NAME"
    export MINERU_MODEL_SOURCE="$MODEL_SOURCE"
    export TOKENIZERS_PARALLELISM=false
    timeout "$TMO" mineru -p "$PDF" -o "$OUT_ROOT" \
        --backend pipeline -l "$LANG"
} > "$outdir/mineru.log" 2>&1
rc=$?

end_ts=$(date +%s)
elapsed=$((end_ts - start_ts))

if [ "$rc" = "0" ] && [ -f "$md_expected" ]; then
    md_size=$(stat -c '%s' "$md_expected" 2>/dev/null || echo 0)
    md_kb=$((md_size / 1024))
    printf '%s\tOK\t%ss\t%sKB\n' "$base" "$elapsed" "$md_kb"
else
    # Leave a marker for the summary pass.
    touch "$outdir/ERROR"
    tail_msg=$(tail -3 "$outdir/mineru.log" 2>/dev/null | tr '\n' ' | ')
    printf '%s\tFAIL\trc=%s\t%ss\t%s\n' "$base" "$rc" "$elapsed" "${tail_msg:0:120}"
fi
exit 0
WORKEREOF

# --- fire xargs remotely, streaming status lines back ---
echo "[run] launching $PARALLEL parallel workers…"
STATUS_FILE=$(mktemp)
ssh -o ConnectTimeout=25 "$SSH_HOST" \
    "cat $WORKLIST_REMOTE | xargs -n1 -P $PARALLEL -I {} \
     $WORKER_REMOTE {} '$REMOTE_OUT' $FORCE '$LANG_FLAG' $PER_PDF_TIMEOUT \
     '$CONDA_SH' '$ENV_NAME' '$MODEL_SOURCE'" \
    2>&1 | grep -v "$BANNER" | tee "$STATUS_FILE" | while IFS=$'\t' read -r name status rest; do
        case "$status" in
            OK)   printf '  ✓ %-40s %s\n' "$name" "$rest" ;;
            SKIP) printf '  ↷ %-40s (already done)\n' "$name" ;;
            FAIL) printf '  ✗ %-40s %s\n' "$name" "$rest" ;;
            *)    printf '    %s %s\n' "$name" "$status" ;;
        esac
    done

# --- cleanup remote temp files ---
ssh -o ConnectTimeout=15 "$SSH_HOST" "rm -f $WORKLIST_REMOTE $WORKER_REMOTE" 2>&1 | grep -v "$BANNER" >/dev/null

# --- summary ---
ok=$(awk -F'\t' '$2=="OK"'    "$STATUS_FILE" | wc -l)
skip=$(awk -F'\t' '$2=="SKIP"' "$STATUS_FILE" | wc -l)
fail=$(awk -F'\t' '$2=="FAIL"' "$STATUS_FILE" | wc -l)
echo
echo "════════════════════════════════════════════════════════"
printf "  summary:  ok=%d  skip=%d  fail=%d\n" "$ok" "$skip" "$fail"
if [ "$fail" -gt 0 ]; then
    echo "  failed PDFs:"
    awk -F'\t' '$2=="FAIL"{print "    "$1"  "$5}' "$STATUS_FILE"
fi
echo "════════════════════════════════════════════════════════"

# --- optional pull back (scoped to THIS batch's basenames only) ---
PULL_ACTION=""
if [ "$PULL" = "1" ]; then
    PULL_ACTION="full"
elif [ "$PULL_MD_ONLY" = "1" ]; then
    PULL_ACTION="md"
fi

if [ -n "$PULL_ACTION" ]; then
    # Build basename list from status file — pull only what this batch
    # produced (OK) or already had valid (SKIP). Skip FAIL (no output).
    # Falls back to the batch's PDF list if STATUS_FILE is unreadable.
    BASES=()
    if [ -s "$STATUS_FILE" ]; then
        while IFS=$'\t' read -r name status _; do
            case "$status" in
                OK|SKIP) BASES+=("$name") ;;
            esac
        done < "$STATUS_FILE"
    fi
    # Fallback: if we somehow have no status entries, derive from input paths.
    if [ ${#BASES[@]} -eq 0 ]; then
        for p in "${PDFS[@]}"; do
            BASES+=("$(basename "$p" .pdf)")
        done
    fi

    if [ ${#BASES[@]} -eq 0 ]; then
        echo "[pull] nothing to copy (no successful PDFs)"
    else
        mkdir -p "$LOCAL_DIR"
        # Ship the basename list to the server via a temp file — safe against
        # long arg lists and shell-quoting hazards in unusual filenames.
        PULL_LIST_REMOTE="/tmp/remote_parse_pull_$$.txt"
        printf '%s\n' "${BASES[@]}" \
            | ssh -o ConnectTimeout=15 "$SSH_HOST" "cat > $PULL_LIST_REMOTE" \
            2>&1 | grep -v "$BANNER" >/dev/null

        if [ "$PULL_ACTION" = "md" ]; then
            echo "[pull] copying *.md for ${#BASES[@]} PDF(s) → $LOCAL_DIR"
            ssh -o ConnectTimeout=25 "$SSH_HOST" \
                "cd '$REMOTE_OUT' && xargs -a '$PULL_LIST_REMOTE' -I{} \
                 find {} -type f -name '*.md' 2>/dev/null \
                 | tar -czf - -T -" \
                2>/dev/null | tar -xzf - -C "$LOCAL_DIR"
        else
            echo "[pull] copying full outputs for ${#BASES[@]} PDF(s) → $LOCAL_DIR"
            # tar -T reads target dirs from the file list. Each basename is
            # a subdir under $REMOTE_OUT, so tar bundles just those trees.
            ssh -o ConnectTimeout=25 "$SSH_HOST" \
                "cd '$REMOTE_OUT' && tar -czf - -T '$PULL_LIST_REMOTE'" \
                2>/dev/null | tar -xzf - -C "$LOCAL_DIR"
        fi

        ssh -o ConnectTimeout=15 "$SSH_HOST" "rm -f $PULL_LIST_REMOTE" \
            2>&1 | grep -v "$BANNER" >/dev/null
        echo "[pull] done → $LOCAL_DIR"
    fi
fi

rm -f "$STATUS_FILE"
[ "$fail" -eq 0 ] && exit 0 || exit 1
