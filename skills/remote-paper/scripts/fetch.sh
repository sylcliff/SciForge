#!/usr/bin/env bash
# fetch.sh — remote-paper main dispatcher.
#
# Usage:  bash fetch.sh --backend <name> <DOI-or-arXiv-id> [<DOI2> ...]
#
# Delegates all real work to scripts/backends/<name>-fetch.sh, which is
# responsible for:
#   - Downloading each identifier via the remote server
#   - Emitting a `PDF_PATH=<absolute local path>` line on stdout for
#     each successfully downloaded PDF (last line of that item's output)
#   - Exit 0 if every id succeeded, non-zero if any failed
#
# This dispatcher only:
#   - Parses `--backend <name>` off the front of argv
#   - Validates the backend script exists
#   - Execs the backend with the remaining args
set -uo pipefail

usage() {
    cat >&2 <<EOF
Usage: bash fetch.sh --backend <name> <DOI-or-arXiv-id> [<id2> ...]

Options:
  --backend <name>   Backend to use (e.g. zj). Required.

Currently available backends (files under scripts/backends/):
$(ls "$(dirname "$0")/backends/" 2>/dev/null | grep -oE '^[a-z0-9_-]+-fetch\.sh$' | sed 's/-fetch\.sh$//' | sed 's/^/  /' || echo "  (none found)")
EOF
    exit 2
}

BACKEND=""
while [ $# -gt 0 ]; do
    case "$1" in
        --backend)
            [ $# -lt 2 ] && { echo "fetch.sh: --backend requires a value" >&2; usage; }
            BACKEND="$2"
            shift 2
            ;;
        --backend=*)
            BACKEND="${1#--backend=}"
            shift 1
            ;;
        -h|--help)
            usage
            ;;
        --)
            shift
            break
            ;;
        *)
            break
            ;;
    esac
done

[ -z "$BACKEND" ] && { echo "fetch.sh: --backend is required" >&2; usage; }
[ $# -eq 0 ] && { echo "fetch.sh: at least one identifier is required" >&2; usage; }

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BACKEND_SCRIPT="$SCRIPT_DIR/backends/${BACKEND}-fetch.sh"

if [ ! -f "$BACKEND_SCRIPT" ]; then
    echo "fetch.sh: no such backend '$BACKEND' (expected $BACKEND_SCRIPT)" >&2
    usage
fi

exec bash "$BACKEND_SCRIPT" "$@"
