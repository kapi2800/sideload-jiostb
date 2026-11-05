#!/usr/bin/env bash
#
# jhs_stage1.sh — JHS-series (JHSC200 / JioOS 2.0) helper workflow.
#
# ./stage1.sh now handles JHS boxes too, so for a normal run you usually don't
# need this script — it is kept for its extra utilities:
#
# Usage:
#   ./jhs/jhs_stage1.sh --queue            queue the TR-069 tasks (needs ACS running)
#   ./jhs/jhs_stage1.sh --post-setup       ADB post-setup: apps, launcher, OTA block
#   ./jhs/jhs_stage1.sh --status           show current box state
#   ./jhs/jhs_stage1.sh --clu-probe        queue the root-existence probe (see below)
#   ./jhs/jhs_stage1.sh --clu-read         print the CLU Output captured on the box
#
# --clu-probe
#   Device.JioSTB.Debugging.CommandLine is an on-boot command hook whose privileged
#   work is done by /system/bin/tr069native (a root daemon). Enabling it and setting
#   OnBootExecuteCmd='id' will, on the next boot, run that command and report its
#   output back through Device.JioSTB.Debugging.CommandLine.Output over TR-069.
#   If Output shows uid=0(root), you have root command execution with no binary.
#
#   It needs TWO reboots: one for the box to store the value, one to execute it.
#
set -uo pipefail

NBI_URL="${NBI_URL:-http://127.0.0.1:7557}"
STB_IP="${STB_IP:-192.168.1.128}"
ADB="${ADB_BIN:-adb}"
DEVICE_ID="${DEVICE_ID:-FCD5D9-JHSC200-RPCSBBG00082328}"

# JHS package names, corrected from the JMSC/JMSB names the repo assumes.
OTA_PKGS=(
    com.iwedia.httpupdate
    com.iwedia.updateservice
    com.iwedia.pvodupdateservice
    com.iwedia.updatercuotaservice
)
STOCK_LAUNCHER="com.iwedia.ui.rjio.launcher.reliance"
AD_PKG="com.jio.stbadservice"
MONET_PKG="com.klevico.monet"
SHEVERY_PKG="com.hamondev.shevery"

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; CYAN='\033[0;36m'; NC='\033[0m'
log()  { echo -e "  ${GREEN}✓${NC} $*"; }
warn() { echo -e "  ${YELLOW}!${NC} $*"; }
fail() { echo -e "  ${RED}✗${NC} $*"; }

adb_sh() { "$ADB" -s "$STB_IP:$ADB_PORT" shell "$@" 2>&1; }
ADB_PORT=5555

enc() { python -c "import urllib.parse,sys; print(urllib.parse.quote(sys.argv[1], safe=''))" "$1" 2>/dev/null \
        || python3 -c "import urllib.parse,sys; print(urllib.parse.quote(sys.argv[1], safe=''))" "$1"; }

post_json() {  # $1 = path, $2 = json body
    curl -s -X POST "$NBI_URL/devices/$(enc "$DEVICE_ID")/tasks?connection_request" \
        -H 'Content-Type: application/json' -d "$2" -w "\nHTTP %{http_code}\n"
}

case "${1:-}" in
    # ─────────────────────────────────────────────────────────── queue tasks
    --queue)
        echo -e "${CYAN}Queuing TR-069 tasks via GenieACS NBI (${NBI_URL})...${NC}"
        [ -f ./stage1.sh ] && ./stage1.sh --tasks-only && exit 0
        post_json "" '{"name":"refreshObject","objectName":""}' | tail -1
        post_json "" '{"name":"setParameterValues","parameterValues":[["Device.JioSTB.USB.USBDebugging",true,"xsd:boolean"]}' | tail -1
        post_json "" '{"name":"setParameterValues","parameterValues":[["Device.JioSTB.USB.DebugValidityDate","2037-01-01T00:00:00.000Z","xsd:dateTime"]}' | tail -1
        post_json "" '{"name":"setParameterValues","parameterValues":[["Device.JioSTB.FOTA.DowngradeEnable",true,"xsd:boolean"],["Device.JioSTB.FOTA.DowngradeValidityDate","2037-12-31T23:59:59.000Z","xsd:dateTime"]}' | tail -1
        echo -e "\n${YELLOW}Cold power-cycle the STB to apply.${NC}"
        ;;

    # ────────────────────────────────────────────── CLU root-existence probe
    --clu-probe)
        echo -e "${CYAN}Queueing CLU probe: Enable=1, OnBootExecuteCmd='id'${NC}"
        post_json "" '{
          "name":"setParameterValues","parameterValues":[
            ["Device.JioSTB.Debugging.CommandLine.Enable",1,"xsd:unsignedInt"],
            ["Device.JioSTB.Debugging.CommandLine.OnBootExecuteCmd","id","xsd:string"],
            ["Device.JioSTB.Debugging.CommandLine.ValidityDate","2037-12-31T23:59:59.000Z","xsd:dateTime"],
            ["Device.JioSTB.Debugging.CommandLine.SaveToFile",1,"xsd:unsignedInt"]
          ]}' | tail -1
        echo -e "\n  Now: reboot TWICE.  First boot stores the value; second executes it."
        echo -e "  Then: ${CYAN}./jhs/jhs_stage1.sh --clu-read${NC}"
        ;;

    --clu-read)
        ID="$(enc "$DEVICE_ID")"
        curl -s "$NBI_URL/devices/?query=%7B%22_id%22%3A%22$ID%22%7D" \
          | python3 -c "
import sys, json
d = json.load(sys.stdin)[0]
cl = d.get('Device', {}).get('JioSTB', {}).get('Debugging', {}).get('CommandLine', {})
for k in ('Enable','OnBootExecuteCmd','Output','Error','ValidityDate','lastExecutionTime'):
    v = cl.get(k, {})
    print(f'  {k:18} = {v.get(\"_value\")!r}')
"
        ;;

    # ─────────────────────────────────────────────────────────── status
    --status)
        echo -e "${CYAN}ADB:${NC} $("$ADB" devices 2>/dev/null | grep -c "$STB_IP" >/dev/null && echo present || echo missing)"
        echo -e "${CYAN}Persisted ADB state on box:${NC}"
        adb_sh getprop persist.iwedia.tr.adb.state
        adb_sh getprop persist.iwedia.tr.adb.date
        adb_sh getprop service.adb.tcp.port
        echo -e "${CYAN}Home activity:${NC}"
        adb_sh cmd package resolve-activity --brief -a android.intent.action.MAIN -c android.intent.category.HOME | tail -1
        echo -e "${CYAN}Disabled packages:${NC}"
        adb_sh pm list packages -d | grep -iE "iwedia|monet|launcher" | head -10
        echo -e "${CYAN}ACS the box resolves:${NC}"
        adb_sh ping -c1 -W2 acs.oss.jio.com | head -1
        ;;

    # ──────────────────────────────────────────────────────── post-setup
    --post-setup)
        echo -e "${CYAN}JHS post-setup (STB $STB_IP:$ADB_PORT)${NC}"
        [ "$("$ADB" get-state -s "$STB_IP:$ADB_PORT" 2>/dev/null)" = "device" ] \
            || { fail "ADB not authorised on $STB_IP:$ADB_PORT"; exit 1; }
        log "ADB authorised"

        adb_sh settings put global device_provisioned 1
        adb_sh settings put global development_settings_enabled 1
        log "provisioned + Developer Options enabled"

        # OTA / update services (JHS names)
        for p in "${OTA_PKGS[@]}"; do
            out="$(adb_sh pm disable-user --user 0 "$p" | head -1)"
            case "$out" in *"disabled-user"*) log "$p disabled" ;; *) warn "$p: $out" ;; esac
        done
        adb_sh pm clear com.iwedia.httpupdate >/dev/null 2>&1
        adb_sh pm clear com.iwedia.updateservice >/dev/null 2>&1
        log "staged updates cleared"

        # Ad service
        out="$(adb_sh pm uninstall -k --user 0 "$AD_PKG" | head -1)"
        log "$AD_PKG -> $out"

        # Apps
        HERE="$(cd "$(dirname "$0")/.." && pwd)"
        for apk in "$HERE"/*[Mm]onet*.apk "$HERE"/*[Ss]hevery*.apk; do
            [ -f "$apk" ] || continue
            out="$("$ADB" install -r -g "$apk" 2>&1 | tail -1)"
            log "$(basename "$apk") -> $out"
        done

        adb_sh pm grant "$MONET_PKG" android.permission.WRITE_SECURE_SETTINGS >/dev/null 2>&1
        # JHS ignores `pm suspend`; `pm disable-user` is what actually sticks.
        adb_sh pm disable-user --user 0 "$STOCK_LAUNCHER" >/dev/null 2>&1
        adb_sh cmd package set-home-activity "$MONET_PKG/.HomeActivity" >/dev/null 2>&1
        adb_sh input keyevent KEYCODE_HOME >/dev/null 2>&1
        log "$MONET_PKG set as home, stock launcher disabled"

        adb_sh pm grant "$SHEVERY_PKG" android.permission.WRITE_SECURE_SETTINGS >/dev/null 2>&1
        adb_sh pm grant "$SHEVERY_PKG" android.permission.PACKAGE_USAGE_STATS >/dev/null 2>&1
        log "$SHEVERY_PKG permissions granted"

        echo -e "\n${GREEN}Done.${NC}"
        echo -e "Install anything else with: ${CYAN}$ADB -s $STB_IP:$ADB_PORT install file.apk${NC}"
        echo -e "${YELLOW}Do NOT run ./stage1.sh --yes-disable-tr069${NC} — disabling com.jio.tr069client"
        echo -e "is what would stop the box from re-asserting USBDebugging on each boot."
        ;;

    *)
        grep '^#' "$0" | sed 's/^# \{0,1\}//' | head -32
        ;;
esac
