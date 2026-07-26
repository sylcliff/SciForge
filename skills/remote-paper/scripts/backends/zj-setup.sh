#!/usr/bin/env bash
# zj-setup.sh — verify/repair the zj server's scansci-pdf environment.
# Called by ../ensure_setup.sh via --backend zj. Idempotent; safe to run
# repeatedly. Run once per session or when a download fails unexpectedly.
#
# Checks / fixes:
#   1. google.com -> about:blank patch in publisher_strategies.py (APS/PRL fix;
#      a scansci-pdf upgrade overwrites it, so re-apply if missing).
#   2. Config: is_campus_network=true, browser_headless=true, scihub_enabled=false.
#   3. pymupdf installed (PDF validation / fetch imports).
set -uo pipefail

SSH_HOST="${ZJ_HOST:-zj}"
ENV_BIN="/public/home/syllzp/software/dasp/anaconda3/envs/scansci/bin"
SITE="/public/home/syllzp/software/dasp/anaconda3/envs/scansci/lib/python3.11/site-packages/scansci_pdf"
PUB_FILE="$SITE/publisher_strategies.py"

echo "[zj-setup] host=$SSH_HOST"

# Python logic goes over ssh STDIN (heredoc), paths passed as argv — this avoids
# all local/remote quote conflicts. The banner is filtered from the output.
ssh -o ConnectTimeout=25 "$SSH_HOST" "$ENV_BIN/python - '$SITE' '$PUB_FILE' '$ENV_BIN'" <<'PYEOF' 2>&1 | grep -v 'post-quantum\|vulnerable\|openssh.com\|may need to be upgraded'
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

echo "[zj-setup] done"
