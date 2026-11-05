#!/usr/bin/env bash
#
# run_acs.sh — serve the correct ACS cert chain to a Jio JHS-series STB.
#
# Wraps tls/pass_front.py with TLS_CERT pointed at a forged eMudhra-signed chain.
# JHS boxes perform full TLS chain validation and reject the repo's acs_chain.pem
# (TLSV1_ALERT_UNKNOWN_CA); this chain anchors on a root the box already trusts.
# It is generated locally by jhs/forge_emudhra_chain.py — no prebuilt artifacts.
#
# Usage:
#   ./jhs/run_acs.sh                 # reuse tls/forged_chain.pem if valid, else mint
#   ./jhs/run_acs.sh --rebuild       # always regenerate the chain
#   TLS_CERT=/path/chain.pem ./jhs/run_acs.sh
#
# The STB must resolve acs.oss.jio.com to this machine (Technitium zone jio.com,
# record acs.oss -> this LAN IP). Then cold power-cycle the STB to make it Inform.
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(dirname "$SCRIPT_DIR")"
TLS_DIR="$REPO_DIR/tls"
CHAIN="${TLS_CERT:-$TLS_DIR/forged_chain.pem}"
VENV="$SCRIPT_DIR/.venv"

log()  { printf '  \033[0;32m✓\033[0m %s\n' "$*"; }
warn() { printf '  \033[1;33m!\033[0m %s\n' "$*"; }
die()  { printf '  \033[0;31m✗\033[0m %s\n' "$*" >&2; exit 1; }

# Minting needs `cryptography`. Prefer an interpreter that already has it;
# otherwise bootstrap a private venv so the script works on a fresh machine.
pick_python() {
    local c
    for c in python3 python; do
        command -v "$c" >/dev/null 2>&1 || continue
        if "$c" -c 'import cryptography' >/dev/null 2>&1; then echo "$c"; return 0; fi
    done
    return 1
}

ensure_python() {
    if PY="$(pick_python)"; then return 0; fi
    warn "'cryptography' not found in python3/python -- bootstrapping $VENV"
    local vpy
    for c in python3 python; do command -v "$c" >/dev/null 2>&1 && { vpy="$c"; break; }; done
    [ -n "${vpy:-}" ] || die "no python3 available to install into"
    [ -x "$VENV/bin/python" ] || [ -x "$VENV/Scripts/python.exe" ] || "$vpy" -m venv "$VENV"
    local vpip="$VENV/bin/pip"
    local vpython="$VENV/bin/python"
    if [ ! -x "$vpython" ] && [ -x "$VENV/Scripts/python.exe" ]; then
        vpython="$VENV/Scripts/python.exe"
        vpip="$VENV/Scripts/pip.exe"
    fi
    "$vpip" install --quiet cryptography >/dev/null 2>&1 \
        || die "could not install cryptography (no network?). Run: pip install cryptography"
    PY="$vpython"
    log "using $PY"
}

# True if the existing chain is present and still covers "now" (so a stale leaf
# or a machine whose clock has moved past the leaf's notAfter gets rebuilt).
chain_is_usable() {
    [ -f "$CHAIN" ] || return 1
    "$PY" - "$CHAIN" <<'PY' 2>/dev/null
import datetime, sys
from cryptography import x509
try:
    c = x509.load_pem_x509_certificate(open(sys.argv[1], "rb").read())
except Exception:
    sys.exit(1)
now = datetime.datetime.now(datetime.timezone.utc)
nb = getattr(c, "not_valid_before_utc", None) or c.not_valid_before.replace(tzinfo=datetime.timezone.utc)
na = getattr(c, "not_valid_after_utc", None) or c.not_valid_after.replace(tzinfo=datetime.timezone.utc)
sys.exit(0 if nb < now < na else 1)
PY
}

REBUILD=false
[ "${1:-}" = "--rebuild" ] && REBUILD=true

if [ "$REBUILD" = true ] || ! { ensure_python && chain_is_usable; }; then
    ensure_python
    log "minting forged chain -> $CHAIN"
    "$PY" "$SCRIPT_DIR/forge_emudhra_chain.py" --out "$CHAIN" \
        ${INTERMEDIATE_PEM:+--int-pem "$INTERMEDIATE_PEM"}
else
    log "reusing existing chain: $CHAIN"
fi

# Stop any pass_front already running. Match the script path, never this shell.
pkill -f "pass_front.py" 2>/dev/null || true
sleep 1

echo
log "serving $CHAIN"
log "listening on 0.0.0.0:8443 and :9443 -> GenieACS 127.0.0.1:7547"
echo
echo "  Next: cold power-cycle the STB (unplug wall power)."
echo "  Then: ./stage1.sh --tasks-only      (queue the ADB-enable tasks)"
echo "  And:  jhs/jhs_stage1.sh --post-setup"
echo

cd "$TLS_DIR"
exec env TLS_CERT="$CHAIN" "${PY:-python3}" pass_front.py
