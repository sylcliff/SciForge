#!/usr/bin/env bash
# zj-fetch.sh — download one or more papers on the zj server and copy them
# back locally. Called by ../fetch.sh via --backend zj.
#
# Usage:  bash zj-fetch.sh <DOI-or-arXiv-id> [<DOI2> ...]
#
# For each identifier:
#   1. runs `scansci-pdf get <id> --output <remote_dir> --strategy legal_only` on zj
#   2. parses the "OK: <path>" line to locate the downloaded PDF
#   3. scp's it back to LOCAL_DIR (default D:\zj_papers, i.e. /d/zj_papers)
#   4. validates page count / size with pymupdf and prints a summary
#   5. prints a machine-readable `PDF_PATH=<local absolute path>` line on success
#
# Retries up to 3× on transient failure (mainly APS/PRL Cloudflare noise).
# No CrossRef fallback — if scansci-pdf can't get it after 3 tries, we
# report failure. Try again later or use the arXiv preprint.
#
# Exit code: 0 if every identifier succeeded, 1 if any failed.
set -uo pipefail

# ---- config (override via env) ----
SSH_HOST="${ZJ_HOST:-zj}"
LOCAL_DIR="${REMOTE_PAPER_LOCAL_DIR:-${ZJ_LOCAL_DIR:-/d/zj_papers}}"
REMOTE_DIR="${ZJ_REMOTE_DIR:-/public/home/syllzp/scansci_test/dl}"
ENV_BIN="/public/home/syllzp/software/dasp/anaconda3/envs/scansci/bin"
CONDA_SH="/public/home/syllzp/software/dasp/anaconda3/etc/profile.d/conda.sh"
DL_TIMEOUT="${REMOTE_PAPER_DL_TIMEOUT:-${ZJ_DL_TIMEOUT:-240}}"
# -----------------------------------

BANNER='post-quantum\|vulnerable\|openssh.com\|may need to be upgraded'
mkdir -p "$LOCAL_DIR"

fail=0

# Convert a possibly-mixed-form local path (e.g. /d/zj_papers/foo.pdf under
# Git Bash) to something a Windows caller can open. Git Bash's `cygpath` gives
# us the native form when available; otherwise leave as-is.
to_native_path() {
    local p="$1"
    if command -v cygpath >/dev/null 2>&1; then
        cygpath -w "$p" 2>/dev/null || echo "$p"
    else
        echo "$p"
    fi
}

fetch_one() {
    local id="$1"
    echo "════════════════════════════════════════════════════════"
    echo "  ⇩  $id"
    echo "════════════════════════════════════════════════════════"

    # Run download server-side, retrying on failure. Some publishers (notably
    # APS/PRL) are probabilistic: the Cloudflare + browser-automation flow fails
    # intermittently, and a retry usually succeeds. DOI-not-found is permanent —
    # bail immediately in that case.
    local log rpath="" attempt
    for attempt in 1 2 3; do
        [ "$attempt" -gt 1 ] && echo "    ↻ 重试 ($attempt/3)…"
        log=$(ssh -o ConnectTimeout=25 "$SSH_HOST" \
            "source '$CONDA_SH'; conda activate scansci; mkdir -p '$REMOTE_DIR'; \
             timeout $DL_TIMEOUT '$ENV_BIN/scansci-pdf' get '$id' --output '$REMOTE_DIR' --strategy legal_only" \
            2>&1 | grep -v "$BANNER")

        # Show the meaningful progress lines (not the whole noisy log).
        echo "$log" | grep -iE 'campus|found PDF|downloaded [0-9]|Source:|OK:|FAILED|SUSPICIOUS|paywall|Renamed' \
            | sed 's/^/    /'

        rpath=$(echo "$log" | grep -oE 'OK: /[^ ]+\.pdf' | head -1 | sed 's/^OK: //')
        [ -n "$rpath" ] && break

        # Permanent failure: bad DOI — no point retrying.
        if echo "$log" | grep -qiE 'DOI not found|Invalid DOI'; then
            echo "    ✗ FAILED — DOI invalid: $id"
            echo "    ↳ 若该文有 arXiv 预印本,可改用 arXiv ID 重试"
            return 1
        fi
    done

    if [ -z "$rpath" ]; then
        echo "    ✗ scansci-pdf could not resolve $id after 3 attempts"
        echo "    ↳ 该出版社通道可能不稳(如 APS)或需机构登录;稍后再试,或换 arXiv ID"
        return 1
    fi

    # Validate + copy back.
    local base
    base=$(basename "$rpath")
    scp -o ConnectTimeout=25 "$SSH_HOST:$rpath" "$LOCAL_DIR/" 2>&1 | grep -v "$BANNER" >/dev/null

    if [ ! -f "$LOCAL_DIR/$base" ]; then
        echo "    ✗ FAILED — scp did not retrieve $base"
        return 1
    fi

    # Ask the server for page count (pymupdf) — cheap and authoritative.
    local meta
    meta=$(ssh -o ConnectTimeout=20 "$SSH_HOST" \
        "$ENV_BIN/python -c \"import pymupdf,os,sys; d=pymupdf.open(sys.argv[1]); print(d.page_count, round(os.path.getsize(sys.argv[1])/1024), (d.metadata.get('title') or '').strip()[:80])\" '$rpath'" \
        2>&1 | grep -v "$BANNER")

    local pages size title
    pages=$(echo "$meta" | awk '{print $1}')
    size=$(echo "$meta" | awk '{print $2}')
    title=$(echo "$meta" | cut -d' ' -f3-)

    local local_path="$LOCAL_DIR/$base"
    echo "    ✓ SAVED  $local_path"
    echo "       pages=${pages:-?}  size=${size:-?}KB  title=${title:-（无标题元数据）}"
    # Gentle heads-up for suspiciously small files (may be a short item, not a failure).
    if [ -n "${size:-}" ] && [ "$size" -lt 80 ] 2>/dev/null; then
        echo "       ⚠ 文件较小（<80KB）——可能是短文/书评,或未拿到全文,请核对内容"
    fi
    # Machine-readable contract line (last line of this item on success).
    # Callers such as sf-download --fallback-remote zj grep this to pick up
    # the file.
    echo "PDF_PATH=$(to_native_path "$local_path")"
    return 0
}

for id in "$@"; do
    fetch_one "$id" || fail=1
done

echo
[ "$fail" -eq 0 ] && echo "全部完成 ✓" || echo "部分失败 ✗（见上）"
exit $fail
