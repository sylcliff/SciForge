#!/usr/bin/env bash
# server-setup.sh — generic `server` backend for remote-parse: verify/prepare
# the server's MinerU environment. Called by ../ensure_setup.sh via
# --backend server. Idempotent; safe to run repeatedly. Run once per session.
#
# Checks / fixes:
#   1. minerU conda env exists, `mineru` binary works, version prints.
#   2. MINERU_MODEL_SOURCE=modelscope is honored; ModelScope reachable.
#   3. Remote output dir exists and is writable.
#   4. Enough free disk on the output partition (warn if < 5 GB).
#   5. Model cache warm-up: if ~/.cache/modelscope is missing / tiny,
#      run one small serial parse to prime it — otherwise 8 parallel workers
#      would each try to download the same 2 GB.
#
# Server configuration (env vars):
#   SERVER_HOST              SSH alias. Default: server
#   SERVER_MINERU_ENV        conda env name. Default: minerU
#   SERVER_CONDA_ROOT        conda install prefix on the server. Default: $HOME/miniconda3
#   SERVER_MINERU_OUT        remote output dir. Default: $HOME/mineru_out
set -uo pipefail

SSH_HOST="${SERVER_HOST:-server}"
ENV_NAME="${SERVER_MINERU_ENV:-minerU}"
CONDA_ROOT="${SERVER_CONDA_ROOT:-\$HOME/miniconda3}"
ENV_BIN="$CONDA_ROOT/envs/$ENV_NAME/bin"
CONDA_SH="$CONDA_ROOT/etc/profile.d/conda.sh"
REMOTE_OUT="${SERVER_MINERU_OUT:-\$HOME/mineru_out}"

BANNER='post-quantum\|vulnerable\|openssh.com\|may need to be upgraded'

echo "[server-setup] host=$SSH_HOST env=$ENV_NAME out=$REMOTE_OUT"

# All checks batched in one ssh round-trip. Python heredoc for anything
# more structured than one-line probes.
ssh -o ConnectTimeout=25 "$SSH_HOST" bash -s -- "$CONDA_SH" "$ENV_NAME" "$ENV_BIN" "$REMOTE_OUT" <<'REMEOF' 2>&1 | grep -v "$BANNER"
set -uo pipefail
CONDA_SH="$1"; ENV_NAME="$2"; ENV_BIN="$3"; REMOTE_OUT="$4"
report=()

# 1. conda env + mineru binary
if [ -x "$ENV_BIN/mineru" ]; then
    ver=$("$ENV_BIN/mineru" --version 2>&1 | head -1)
    report+=("MINERU: $ver  OK")
else
    report+=("MINERU: MISSING — $ENV_BIN/mineru not found")
fi

# 2. remote output dir
mkdir -p "$REMOTE_OUT" 2>/dev/null && [ -w "$REMOTE_OUT" ] \
    && report+=("OUTDIR: $REMOTE_OUT writable  OK") \
    || report+=("OUTDIR: $REMOTE_OUT NOT WRITABLE")

# 3. disk space (in GB free on the target partition)
free_gb=$(df -BG --output=avail "$REMOTE_OUT" 2>/dev/null | tail -1 | tr -dc '0-9')
if [ -n "$free_gb" ]; then
    if [ "$free_gb" -lt 5 ]; then
        report+=("DISK: only ${free_gb}G free — WARN, batches may fail")
    else
        report+=("DISK: ${free_gb}G free  OK")
    fi
fi

# 4. model cache status
cache="$HOME/.cache/modelscope"
if [ -d "$cache" ]; then
    cache_mb=$(du -sm "$cache" 2>/dev/null | awk '{print $1}')
    report+=("MODELS: cache=${cache_mb}MB at $cache")
    NEEDS_WARMUP=0
    [ "${cache_mb:-0}" -lt 500 ] && NEEDS_WARMUP=1
else
    report+=("MODELS: no cache yet — will warm up")
    NEEDS_WARMUP=1
fi

# 5. modelscope reachability (quick DNS/HTTP probe; don't block if slow)
if timeout 10 bash -c 'exec 3<>/dev/tcp/modelscope.cn/443' 2>/dev/null; then
    report+=("NETWORK: modelscope.cn:443 reachable  OK")
else
    report+=("NETWORK: modelscope.cn:443 UNREACHABLE — models won't download")
fi

printf '%s\n' "${report[@]}"
echo "---warmup_flag---$NEEDS_WARMUP"
REMEOF

# Warm-up path — only if flag set. Do this outside the initial ssh so the
# user sees log streaming rather than a silent 5-minute hang.
NEEDS_WARMUP=$(ssh -o ConnectTimeout=15 "$SSH_HOST" \
    "test -d ~/.cache/modelscope && du -sm ~/.cache/modelscope 2>/dev/null | awk '{print (\$1<500)?1:0}' || echo 1" \
    2>&1 | grep -v "$BANNER" | tail -1)

if [ "${NEEDS_WARMUP:-0}" = "1" ]; then
    echo "[server-setup] Model cache small/missing. Running warm-up parse (~4 min, one worker)…"
    # Use an already-present sample PDF as the warm-up input.
    ssh -o ConnectTimeout=25 "$SSH_HOST" \
        "source '$CONDA_SH'; conda activate '$ENV_NAME'; \
         export MINERU_MODEL_SOURCE=modelscope; \
         mkdir -p '$REMOTE_OUT/.warmup'; \
         if ls ~/*.pdf 2>/dev/null | head -1 | read s; then \
             mineru -p \"\$s\" -o '$REMOTE_OUT/.warmup' --backend pipeline 2>&1 | tail -20; \
         else \
             echo 'no sample PDF in ~ — skipping warm-up; first real parse will download models'; \
         fi" 2>&1 | grep -v "$BANNER" | sed 's/^/    /'
fi

echo "[server-setup] done"
