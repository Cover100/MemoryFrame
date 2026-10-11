#!/usr/bin/env bash
# MF-36: product mode (hotspot at boot) and developer mode (home Wi-Fi), with offline recovery.
#
#   sudo deploy/netmode.sh product   # hotspot now, and at every boot (writes the boot-partition mode file)
#   sudo deploy/netmode.sh dev       # developer Wi-Fi now, and at every boot
#   deploy/netmode.sh status         # requested mode, applied mode, active connection
#   deploy/netmode.sh boot           # what memoryframe-netmode.service runs at boot
#
# MF-18 (the app calls these through sudo; the only commands in the sudoers rule):
#   netmode.sh wifi-apply      # reads "ssid\npassword\n" on STDIN (never argv), writes a root-only
#                              # MemoryFrame-AP-pending keyfile and switches to it; old profile kept
#   netmode.sh wifi-commit     # pending becomes the hotspot profile, old profile deleted
#   netmode.sh wifi-rollback   # old hotspot back up, pending profile deleted
#
# Boot-partition files (editable from any PC, drive "bootfs"):
#   memoryframe-mode.txt      one word: product | dev. Missing/empty/other -> developer default:
#                             the boot service changes NOTHING (home Wi-Fi stays). Only an explicit
#                             `sudo netmode.sh product` (or memoryframe-reset) writes product.
#                             At boot the service is a strict no-op unless the file says product
#                             (or product mode was applied before and has to be undone).
#                             install.sh never writes this file.
#   memoryframe-dev-wifi.txt  optional, only  ssid=<name>: which EXISTING NetworkManager
#                             profile to use in dev mode (default: the most recently used one). Never a password: dev Wi-Fi
#                             credentials live only in that root-only NM profile. Any other
#                             key (e.g. password=) is ignored and a warning is logged
#                             WITHOUT its value.
#   memoryframe-reset         empty file: recreate the default hotspot profile and go to
#                             product mode (the file is removed).
#
# Behaviour:
#   product: every Wi-Fi client profile gets autoconnect=no; the hotspot profile is brought up.
#            If that fails, the failure is recorded for the frame's error screen. Never reboots.
#   dev:     hotspot down, the dev profile up (waits up to MEMORYFRAME_DEV_WAIT s). If it
#            doesn't connect: the hotspot is started for this boot only (the frame shows "Developer
#            Wi-Fi not found"); client autoconnect and the mode file are left alone, so the next
#            boot tries the home Wi-Fi again.
#   Idempotent: the same mode twice changes nothing; profiles are never created or duplicated
#   (except the hotspot profile on memoryframe-reset, via deploy/hotspot.sh).
#   Refused (exit 3) while another mode switch runs or an MF-18 Wi-Fi change is pending.
#
# The frame app reads $STATE_DIR/netmode.json (no secrets in it).
# Test hooks: NMCLI, MEMORYFRAME_BOOT_DIR, MEMORYFRAME_STATE_DIR, MEMORYFRAME_HOTSPOT_SH,
# MEMORYFRAME_DEV_WAIT, MEMORYFRAME_BOOT_ID_FILE.
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
NMCLI="${NMCLI:-nmcli}"
BOOT_DIR="${MEMORYFRAME_BOOT_DIR:-/boot/firmware}"
STATE_DIR="${MEMORYFRAME_STATE_DIR:-/run/memoryframe}"
HOTSPOT_CON="${MEMORYFRAME_HOTSPOT_CON:-MemoryFrame-hotspot}"
HOTSPOT_SH="${MEMORYFRAME_HOTSPOT_SH:-$DIR/hotspot.sh}"
DEV_WAIT="${MEMORYFRAME_DEV_WAIT:-45}"
BOOT_ID_FILE="${MEMORYFRAME_BOOT_ID_FILE:-/proc/sys/kernel/random/boot_id}"
IFACE="${MEMORYFRAME_IFACE:-wlan0}"

MODE_FILE="$BOOT_DIR/memoryframe-mode.txt"
WIFI_FILE="$BOOT_DIR/memoryframe-dev-wifi.txt"
RESET_FILE="$BOOT_DIR/memoryframe-reset"
STATE="$STATE_DIR/netmode.json"
LOCK="$STATE_DIR/netmode.lock"
PENDING="$STATE_DIR/wifi-change-pending"     # written by the MF-18 helper while a change is pending
PENDING_CON="${MEMORYFRAME_PENDING_CON:-MemoryFrame-AP-pending}"
NM_DIR="${MEMORYFRAME_NM_DIR:-/etc/NetworkManager/system-connections}"
RESET_DONE=false

log() {
    echo "netmode: $*" >&2
    logger -t memoryframe-netmode -- "$*" 2>/dev/null || true
}

usage() { echo "usage: $0 product|dev|status|boot|wifi-apply|wifi-commit|wifi-rollback" >&2; exit 2; }

trim() { local s="${1//$'\r'/}"; s="${s#"${s%%[![:space:]]*}"}"; printf '%s' "${s%"${s##*[![:space:]]}"}"; }

# ---------- boot-partition files ----------

requested_mode() {
    local line=""
    if [ -f "$MODE_FILE" ]; then
        while IFS= read -r line || [ -n "$line" ]; do
            line="$(trim "$line")"
            [ -n "$line" ] && break
        done < "$MODE_FILE"
    fi
    line="$(printf '%s' "$line" | tr '[:upper:]' '[:lower:]')"
    # SAFETY (Delivery Lead/Tester): only an explicit "product" arms product mode. Missing, empty
    # or unknown -> "none": developer default, the network is left exactly as it is.
    case "$line" in
        product|dev) echo "$line" ;;
        "") echo none ;;
        *) log "memoryframe-mode.txt: unknown mode, treating as developer default (network unchanged)"; echo none ;;
    esac
}

dev_ssid() {
    # Only ssid= is used. Values of other keys are never printed or logged.
    local line key val ssid=""
    [ -f "$WIFI_FILE" ] || { echo ""; return; }
    while IFS= read -r line || [ -n "$line" ]; do
        line="$(trim "$line")"
        if [ -z "$line" ] || [ "${line:0:1}" = "#" ]; then continue; fi
        key="$(trim "${line%%=*}")"; key="$(printf '%s' "$key" | tr '[:upper:]' '[:lower:]')"
        val="${line#*=}"
        if [ "$key" = ssid ] && [ "$line" != "$key" ]; then
            ssid="$(trim "$val")"
        elif [[ "$key" == *pass* || "$key" == psk* || "$key" == *secret* || "$key" == *key* ]]; then
            log "memoryframe-dev-wifi.txt: ignoring a '${key//[^a-z0-9_-]/}' line (passwords are never read from the boot partition; value not logged)"
        else
            log "memoryframe-dev-wifi.txt: ignoring unknown key '$(printf '%s' "${key//[^a-z0-9_-]/}" | cut -c1-32)'"
        fi
    done < "$WIFI_FILE"
    echo "$ssid"
}

write_mode_file() {
    if printf '%s\n' "$1" > "$MODE_FILE.tmp" 2>/dev/null && mv "$MODE_FILE.tmp" "$MODE_FILE"; then
        sync -f "$MODE_FILE" 2>/dev/null || true
    else
        rm -f "$MODE_FILE.tmp" 2>/dev/null || true
        log "could not write $MODE_FILE (mode applies until the next boot only)"
    fi
}

# ---------- NetworkManager ----------

profiles() { "$NMCLI" -g NAME connection show; }
prop() { "$NMCLI" -g "$2" connection show "$1" 2>/dev/null || true; }
active() { "$NMCLI" -g GENERAL.CONNECTION device show "$IFACE" 2>/dev/null || true; }

client_profiles() {
    local n
    while IFS= read -r n; do
        if [ -z "$n" ] || [ "$n" = "$HOTSPOT_CON" ]; then continue; fi
        [ "$(prop "$n" connection.type)" = 802-11-wireless ] || continue
        if [ "$(prop "$n" 802-11-wireless.mode)" = ap ]; then continue; fi
        printf '%s\n' "$n"
    done < <(profiles)
}

clients_autoconnect_off() {
    local n
    while IFS= read -r n; do
        if [ "$(prop "$n" connection.autoconnect)" != no ]; then
            "$NMCLI" connection modify "$n" connection.autoconnect no && log "autoconnect off: $n"
        fi
    done < <(client_profiles)
}

json_str() { local s="${1//\\/\\\\}"; s="${s//\"/\\\"}"; printf '"%s"' "${s//[$'\t\r\n']/ }"; }

write_state() {   # mode requested dev_fallback hotspot_error
    local boot_id=""
    [ -r "$BOOT_ID_FILE" ] && boot_id="$(trim "$(cat "$BOOT_ID_FILE")")"
    mkdir -p "$STATE_DIR"
    {
        printf '{"mode": %s, "requested": %s, "dev_fallback": %s, "hotspot_error": %s, ' \
            "$(json_str "$1")" "$(json_str "$2")" "$3" "$4"
        printf '"active": %s, "boot_id": %s, "reset": %s}\n' "$(json_str "$(active)")" "$(json_str "$boot_id")" "$RESET_DONE"
    } > "$STATE.tmp"
    chmod 0644 "$STATE.tmp"
    mv "$STATE.tmp" "$STATE"
}

apply_product() {   # requested dev_fallback [keep-clients]
    # keep-clients (dev fallback): client profiles keep autoconnect, so the home Wi-Fi is retried
    # at the next boot; only an explicit product mode turns their autoconnect off.
    if [ "${3:-}" != keep-clients ]; then clients_autoconnect_off; fi
    if [ "$(active)" = "$HOTSPOT_CON" ]; then
        log "product: hotspot already active"
        write_state product "$1" "$2" false
        return 0
    fi
    if ! profiles | grep -qxF "$HOTSPOT_CON"; then
        log "product: hotspot profile '$HOTSPOT_CON' is missing (create it with deploy/hotspot.sh or memoryframe-reset)"
        write_state product "$1" "$2" true
        return 1
    fi
    local try
    for try in 1 2; do
        if "$NMCLI" --wait 30 connection up "$HOTSPOT_CON" >/dev/null 2>&1 && [ "$(active)" = "$HOTSPOT_CON" ]; then
            log "product: hotspot up"
            write_state product "$1" "$2" false
            return 0
        fi
        log "product: hotspot activation failed (attempt $try)"
    done
    write_state product "$1" "$2" true
    return 1
}

clients_autoconnect_on() {   # undo product mode (dev from the boot partition)
    local n
    while IFS= read -r n; do
        if [ "$(prop "$n" connection.autoconnect)" = no ]; then
            "$NMCLI" connection modify "$n" connection.autoconnect yes && log "autoconnect on: $n"
        fi
    done < <(client_profiles)
}

left_by_product() {
    # True only if product mode was applied before: there are client Wi-Fi profiles and every one
    # has autoconnect=no. A normal dev Pi (home Wi-Fi autoconnects) -> false -> boot does nothing.
    local n any=false
    while IFS= read -r n; do
        any=true
        if [ "$(prop "$n" connection.autoconnect)" != no ]; then return 1; fi
    done < <(client_profiles)
    [ "$any" = true ]
}

dev_profile() {
    # ssid= given: the profile for that SSID. Otherwise the most recently used client profile.
    local ssid="$1" n best="" best_ts=-1 ts
    while IFS= read -r n; do
        if [ -n "$ssid" ]; then
            if [ "$(prop "$n" 802-11-wireless.ssid)" = "$ssid" ]; then printf '%s\n' "$n"; return 0; fi
            continue
        fi
        ts="$(prop "$n" connection.timestamp)"
        [[ "$ts" =~ ^[0-9]+$ ]] || ts=0
        if [ "$ts" -gt "$best_ts" ]; then best="$n"; best_ts="$ts"; fi
    done < <(client_profiles)
    [ -n "$best" ] && printf '%s\n' "$best"
}

apply_dev() {
    local ssid p
    ssid="$(dev_ssid)"
    if ! p="$(dev_profile "$ssid")"; then
        log "dev: no matching NetworkManager Wi-Fi profile${ssid:+ for the configured ssid}; starting the hotspot"
        apply_product dev true keep-clients || true
        return 1
    fi
    if [ "$(active)" = "$p" ]; then
        log "dev: already on $p"
        write_state dev dev false false
        return 0
    fi
    if [ "$(active)" = "$HOTSPOT_CON" ]; then "$NMCLI" connection down "$HOTSPOT_CON" >/dev/null 2>&1 || true; fi
    if "$NMCLI" --wait "$DEV_WAIT" connection up "$p" >/dev/null 2>&1 && [ "$(active)" = "$p" ]; then
        log "dev: connected ($p)"
        write_state dev dev false false
        return 0
    fi
    log "dev: developer Wi-Fi did not connect; starting the hotspot (mode file unchanged; retried next boot)"
    apply_product dev true keep-clients || true
    return 1
}

apply() {
    case "$1" in
        dev) apply_dev ;;
        product) apply_product product false ;;
        *)  # unarmed: developer default, nothing touched (current behaviour before MF-36)
            log "no product mode armed (memoryframe-mode.txt missing/empty): developer default, network left as is"
            write_state dev none false false ;;
    esac
}

take_lock() {   # [nopending]
    mkdir -p "$STATE_DIR"
    exec 9>"$LOCK"
    if ! flock -n 9; then
        log "another network mode switch is in progress"; exit 3
    fi
    if [ "${1:-}" != nopending ] && [ -e "$PENDING" ]; then
        log "a Wi-Fi change is pending (MF-18); try again after it is kept or rolled back"; exit 3
    fi
}

status() {
    local mode="unknown"
    [ -f "$STATE" ] && mode="$(sed -n 's/.*"mode": "\([a-z]*\)".*/\1/p' "$STATE")"
    case "$(requested_mode)" in
        product) echo "armed for next boot: product (memoryframe-mode.txt = product; the hotspot replaces home Wi-Fi)" ;;
        dev)     echo "armed for next boot: dev (memoryframe-mode.txt = dev)" ;;
        *)       echo "armed for next boot: dev (no mode file: developer default, network left as is)" ;;
    esac
    echo "current mode: ${mode:-unknown}"
    echo "active: $(active)"
    if [ -f "$STATE" ] && grep -q '"dev_fallback": true' "$STATE"; then echo "note: developer Wi-Fi not found, hotspot started"; fi
    if [ -f "$STATE" ] && grep -q '"hotspot_error": true' "$STATE"; then echo "error: hotspot did not start"; fi
}

# ---------- MF-18: hotspot name/password change (pending -> commit | rollback) ----------

applied_mode() { if [ -f "$STATE" ]; then sed -n 's/.*"mode": "\([a-z]*\)".*/\1/p' "$STATE"; fi; }

valid_ssid() {
    local s="$1" n
    n="$(printf '%s' "$s" | LC_ALL=C wc -c)"
    [ "$n" -ge 1 ] && [ "$n" -le 32 ] && ! printf '%s' "$s" | LC_ALL=C grep -q '[[:cntrl:]]'
}

valid_psk() { printf '%s' "$1" | LC_ALL=C grep -qxE '[ -~]{8,63}'; }

ssid_bytes() { printf '%s' "$1" | od -An -tu1 -v | tr -s ' \n' ';' | sed 's/^;//'; }

keyfile_escape() {   # GKeyFile value: backslash, and leading/trailing spaces
    local v="${1//\\/\\\\}"
    [ "${v:0:1}" = " " ] && v="\\s${v:1}"
    [ "${v: -1}" = " " ] && v="${v:0:${#v}-1}\\s"
    printf '%s' "$v"
}

delete_pending_profile() {
    if profiles | grep -qxF "$PENDING_CON"; then "$NMCLI" connection delete "$PENDING_CON" >/dev/null 2>&1 || true; fi
    rm -f "$NM_DIR/$PENDING_CON.nmconnection"
}

require_product() {
    # SAFETY (#41): Wi-Fi changes only when product mode was applied this boot. A missing or
    # unreadable netmode.json means dev: never touch the home Wi-Fi.
    if [ "$(applied_mode)" != product ]; then
        log "wifi change refused: product mode is not applied (developer mode or no state)"; exit 3
    fi
}

wifi_rollback() {
    require_product
    if [ "$(active)" = "$PENDING_CON" ]; then "$NMCLI" connection down "$PENDING_CON" >/dev/null 2>&1 || true; fi
    local ok=0
    if [ "$(active)" != "$HOTSPOT_CON" ]; then
        "$NMCLI" --wait 30 connection up "$HOTSPOT_CON" >/dev/null 2>&1 || ok=1
    fi
    delete_pending_profile
    rm -f "$PENDING"
    local err=false
    if [ "$ok" != 0 ]; then err=true; fi
    write_state product product false "$err"
    log "wifi change rolled back"
    return "$ok"
}

wifi_apply() {
    local ssid="" psk="" f
    IFS= read -r ssid || true
    IFS= read -r psk || true
    require_product
    if ! valid_ssid "$ssid" || ! valid_psk "$psk"; then log "wifi change refused: invalid name or password"; exit 2; fi
    if ! profiles | grep -qxF "$HOTSPOT_CON"; then log "wifi change refused: no hotspot profile"; exit 1; fi
    : > "$PENDING"
    delete_pending_profile
    f="$NM_DIR/$PENDING_CON.nmconnection"
    mkdir -p "$NM_DIR"
    (
        umask 077
        {
            printf '[connection]\nid=%s\ntype=wifi\ninterface-name=%s\nautoconnect=false\n\n' "$PENDING_CON" "$IFACE"
            printf '[wifi]\nmode=ap\nband=bg\nchannel=6\nssid=%s\n\n' "$(ssid_bytes "$ssid")"
            printf '[wifi-security]\nkey-mgmt=wpa-psk\nproto=rsn\npairwise=ccmp\ngroup=ccmp\npsk=%s\n\n' "$(keyfile_escape "$psk")"
            printf '[ipv4]\nmethod=shared\naddress1=10.42.0.1/24\n\n[ipv6]\nmethod=disabled\n'
        } > "$f.tmp"
        chmod 0600 "$f.tmp"
        mv "$f.tmp" "$f"
    )
    psk=""
    if ! "$NMCLI" connection load "$f" >/dev/null 2>&1; then
        log "wifi change: could not load the new profile"; wifi_rollback || true; exit 4
    fi
    "$NMCLI" connection down "$HOTSPOT_CON" >/dev/null 2>&1 || true
    if "$NMCLI" --wait 30 connection up "$PENDING_CON" >/dev/null 2>&1 && [ "$(active)" = "$PENDING_CON" ]; then
        log "wifi change: new hotspot up, waiting for confirmation"
        write_state product product false false
        return 0
    fi
    log "wifi change: the new hotspot did not start"
    wifi_rollback || true
    exit 4
}

wifi_commit() {
    require_product
    if ! profiles | grep -qxF "$PENDING_CON" || [ "$(active)" != "$PENDING_CON" ]; then
        log "wifi commit refused: no active pending profile"; exit 4
    fi
    "$NMCLI" connection delete "$HOTSPOT_CON" >/dev/null 2>&1 || true
    "$NMCLI" connection modify "$PENDING_CON" connection.id "$HOTSPOT_CON"
    rm -f "$PENDING"
    write_state product product false false
    log "wifi change kept"
}

case "${1:-}" in
    status) status ;;
    wifi-apply) take_lock; wifi_apply ;;
    wifi-commit) take_lock nopending; wifi_commit ;;
    wifi-rollback) take_lock nopending; wifi_rollback ;;
    product|dev)
        take_lock
        write_mode_file "$1"
        apply "$1" ;;
    boot)
        take_lock nopending                   # a pending change is rolled back below
        if [ -e "$RESET_FILE" ]; then
            log "memoryframe-reset found: restoring the default hotspot, product mode"
            rm -f "$RESET_FILE"
            RESET_DONE=true
            write_mode_file product
            if [ -x "$HOTSPOT_SH" ]; then
                "$HOTSPOT_SH" </dev/null >/dev/null 2>&1 || log "hotspot profile reset failed"
            fi
        fi
        if [ -e "$PENDING" ] || profiles | grep -qxF "$PENDING_CON"; then
            log "unconfirmed Wi-Fi change found at boot: rolling back"
            delete_pending_profile; rm -f "$PENDING"
        fi
        # Boot never fails the unit (no reboot loop); the state file carries the outcome.
        # SAFETY: the boot service is a strict no-op unless the mode file explicitly says product,
        # or product mode was applied before and must be undone (mode file dev/missing).
        req="$(requested_mode)"
        if [ "$req" = product ]; then
            apply product || true
        elif left_by_product; then
            log "boot: product mode was applied before and is no longer armed: back to developer Wi-Fi"
            clients_autoconnect_on
            apply_dev || true
        else
            log "boot: product mode not armed; developer default, network left as is (no-op)"
            write_state dev "$req" false false
        fi ;;
    *) usage ;;
esac
