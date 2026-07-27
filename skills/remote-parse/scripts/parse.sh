#!/usr/bin/env bash
# parse.sh — remote-parse main dispatcher.
#
# Usage:  bash parse.sh --backend <name> [flags] <input>
#
# Delegates all real work to scripts/backends/<name>-parse.sh, which is
# responsible for:
#   - Turning a set of PDFs (remote paths, remote dir, or local files to
#     upload) into MinerU output (.md + .json + images) on the server
#   - Optionally pulling results back to the local machine
#   - Exiting 0 if every PDF succeeded, 1 if any failed
#
# This dispatcher only:
#   - Parses `--backend <name>` off the front of argv
#   - Validates the backend script exists
#   - Execs the backend with the remaining args
set -uo pipefail

usage() {
    cat >&2 <<EOF
Usage: bash parse.sh --backend <name> [flags] <input>

Options:
  --backend <name>   Backend to use (e.g. server). Required.

Backend-specific flags/inputs are passed through unchanged.
Currently available backends (files under scripts/backends/):
$(ls "$(dirname "$0")/backends/" 2>/dev/null | grep -oE '^[a-z0-9_-]+-parse\.sh$' | sed 's/-parse\.sh$//' | sed 's/^/  /' || echo "  (none found)")
EOF
    exit 2
}

BACKEND=""
while [ $# -gt 0 ]; do
    case "$1" in
        --backend)
            [ $# -lt 2 ] && { echo "parse.sh: --backend requires a value" >&2; usage; }
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

[ -z "$BACKEND" ] && { echo "parse.sh: --backend is required" >&2; usage; }
[ $# -eq 0 ] && { echo "parse.sh: at least one input (PDF path, --dir, or --upload) is required" >&2; usage; }

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BACKEND_SCRIPT="$SCRIPT_DIR/backends/${BACKEND}-parse.sh"

if [ ! -f "$BACKEND_SCRIPT" ]; then
    echo "parse.sh: no such backend '$BACKEND' (expected $BACKEND_SCRIPT)" >&2
    usage
fi

exec bash "$BACKEND_SCRIPT" "$@"
