#!/usr/bin/env bash
# ensure_setup.sh — remote-paper main dispatcher for environment setup.
#
# Usage:  bash ensure_setup.sh --backend <name>
#
# Delegates to scripts/backends/<name>-setup.sh. Idempotent per backend.
set -uo pipefail

usage() {
    cat >&2 <<EOF
Usage: bash ensure_setup.sh --backend <name>

Options:
  --backend <name>   Backend to set up (e.g. zj). Required.

Currently available backends (files under scripts/backends/):
$(ls "$(dirname "$0")/backends/" 2>/dev/null | grep -oE '^[a-z0-9_-]+-setup\.sh$' | sed 's/-setup\.sh$//' | sed 's/^/  /' || echo "  (none found)")
EOF
    exit 2
}

BACKEND=""
while [ $# -gt 0 ]; do
    case "$1" in
        --backend)
            [ $# -lt 2 ] && { echo "ensure_setup.sh: --backend requires a value" >&2; usage; }
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
        *)
            echo "ensure_setup.sh: unexpected arg '$1'" >&2
            usage
            ;;
    esac
done

[ -z "$BACKEND" ] && { echo "ensure_setup.sh: --backend is required" >&2; usage; }

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BACKEND_SCRIPT="$SCRIPT_DIR/backends/${BACKEND}-setup.sh"

if [ ! -f "$BACKEND_SCRIPT" ]; then
    echo "ensure_setup.sh: no such backend '$BACKEND' (expected $BACKEND_SCRIPT)" >&2
    usage
fi

exec bash "$BACKEND_SCRIPT"
