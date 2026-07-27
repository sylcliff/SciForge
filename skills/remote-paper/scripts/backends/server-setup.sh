#!/usr/bin/env bash
# server-setup.sh — verify/repair the remote server's scansci-pdf environment.
# Called by ../ensure_setup.sh via --backend server. Idempotent; safe to run
# repeatedly. Run once per session or when a download fails unexpectedly.
#
# Checks / fixes:
#   1. google.com -> about:blank patch in publisher_strategies.py (APS/PRL fix;
#      a scansci-pdf upgrade overwrites it, so re-apply if missing).
#   2. Config: is_campus_network=true, browser_headless=true, scihub_enabled=false.
#   3. pymupdf installed (PDF validation / fetch imports).
#
# Server configuration (env vars):
#   SERVER_HOST              SSH alias. Default: server
#   SERVER_SCANSCI_ENV       conda env name with scansci-pdf. Default: scansci
#   SERVER_CONDA_ROOT        conda install prefix on the server. Default: $HOME/miniconda3
set -uo pipefail

SSH_HOST="${SERVER_HOST:-server}"
SCANSCI_ENV="${SERVER_SCANSCI_ENV:-scansci}"
CONDA_ROOT="${SERVER_CONDA_ROOT:-\$HOME/miniconda3}"
ENV_BIN="$CONDA_ROOT/envs/$SCANSCI_ENV/bin"
# scansci_pdf package dir, resolved on the server (python version may vary).
SITE_GLOB="$CONDA_ROOT/envs/$SCANSCI_ENV/lib/python*/site-packages/scansci_pdf"

echo "[server-setup] host=$SSH_HOST env=$SCANSCI_ENV"

# Resolve the actual site-packages/scansci_pdf path on the server (python
# minor version isn't known locally), then run the repair logic. Paths are
# passed as argv to the Python heredoc to avoid local/remote quote conflicts.
ssh -o ConnectTimeout=25 "$SSH_HOST" "bash -s -- '$SITE_GLOB' '$ENV_BIN'" <<'REMEOF' 2>&1 | grep -v 'post-quantum\|vulnerable\|openssh.com\|may need to be upgraded'
set -uo pipefail
SITE=$(ls -d $1 2>/dev/null | head -1)
ENV_BIN="$2"
if [ -z "$SITE" ]; then
    echo "PATCH: WARNING scansci_pdf package dir not found under $1"
    SITE="__missing__"
fi
PUB_FILE="$SITE/publisher_strategies.py"
"$ENV_BIN/python" - "$SITE" "$PUB_FILE" "$ENV_BIN" <<'PYEOF'
import subprocess, sys, os, importlib.util

SITE, PUB, ENV_BIN = sys.argv[1], sys.argv[2], sys.argv[3]
SC = ENV_BIN + '/scansci-pdf'
report = []

# --- 1. google.com -> about:blank patch ---
bad = 'create_tab("https://www.google.com/", config'
good = 'create_tab("about:blank", config'
try:
    src = open(PUB, encoding='utf-8').read()
    if good in src:
        report.append('PATCH: about:blank present  OK')
    elif bad in src:
        if not os.path.exists(PUB + '.bak'):
            open(PUB + '.bak', 'w', encoding='utf-8').write(src)
        open(PUB, 'w', encoding='utf-8').write(src.replace(bad, good))
        report.append('PATCH: RE-APPLIED google.com -> about:blank (was reverted, likely by upgrade)')
    else:
        report.append('PATCH: WARNING neither marker found — code changed, inspect manually')
except FileNotFoundError:
    report.append('PATCH: WARNING publisher_strategies.py not found')

# --- 2. config values ---
want = {'is_campus_network': 'true', 'browser_headless': 'true', 'scihub_enabled': 'false'}
try:
    cur = subprocess.run([SC, 'config-cmd'], capture_output=True, text=True, timeout=60).stdout
    curmap = {}
    for line in cur.splitlines():
        if '=' in line:
            k, _, v = line.partition('=')
            curmap[k.strip()] = v.strip().lower()
    for k, v in want.items():
        have = curmap.get(k, '')
        if have != v:
            subprocess.run([SC, 'config-cmd', k, v], capture_output=True, text=True, timeout=60)
            report.append('CONFIG: %s %s -> %s  SET' % (k, have or '(unset)', v))
        else:
            report.append('CONFIG: %s=%s  OK' % (k, v))
except Exception as e:
    report.append('CONFIG: ERROR %r' % (e,))

# --- 3. pymupdf present ---
try:
    if importlib.util.find_spec('pymupdf') is None:
        subprocess.run([ENV_BIN + '/pip', 'install', 'pymupdf'], capture_output=True, text=True, timeout=300)
        report.append('PYMUPDF: installed')
    else:
        report.append('PYMUPDF: present  OK')
except Exception as e:
    report.append('PYMUPDF: ERROR %r' % (e,))

print('\n'.join(report))
PYEOF
REMEOF

echo "[server-setup] done"
