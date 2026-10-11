#!/usr/bin/env bash
# MF-36: product mode (hotspot at boot) and developer mode (home Wi-Fi), with offline recovery.
#
#   sudo deploy/netmode.sh product   # hotspot now, and at every boot (writes the boot-partition mode file)
#   sudo deploy/netmode.sh dev       # developer Wi-Fi now, and at every boot
#   deploy/netmode.sh status         # requested mode, applied mode, active connection
#   deploy/netmode.sh boot           # what memoryframe-netmode.service runs at boot
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
#   NEVER modifies a non-MemoryFrame profile (home Wi-Fi profiles may be netplan-generated, and
#   a modify writes back to /etc/netplan). Only MemoryFrame-* profiles are ever modified.
#   product: the hotspot is brought up and VERIFIED first; only then is it made the preferred
#            boot connection (MemoryFrame-hotspot autoconnect=yes, autoconnect-priority 100).
#            If it fails, the previous connection (home Wi-Fi) is brought back and the failure is
#            recorded for the frame's error screen. Never reboots.
#            First product boot after arming: if no phone associates with the hotspot within
#            MEMORYFRAME_PRODUCT_CONFIRM_MIN minutes (default 10; 0 = off), the frame reverts to
#            developer mode / home Wi-Fi and logs it (product-watch, run via systemd-run).
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
# MEMORYFRAME_DEV_WAIT, MEMORYFRAME_BOOT_ID_FILE, MEMORYFRAME_VAR_DIR, SYSTEMD_RUN, IW,
# MEMORYFRAME_PRODUCT_CONFIRM_MIN, MEMORYFRAME_CONFIRM_POLL.
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
VAR_DIR="${MEMORYFRAME_VAR_DIR:-/var/lib/memoryframe}"
SYSTEMD_RUN="${SYSTEMD_RUN-systemd-run}"
IW="${IW:-iw}"
CONFIRM_MIN="${MEMORYFRAME_PRODUCT_CONFIRM_MIN:-10}"
CONFIRM_POLL="${MEMORYFRAME_CONFIRM_POLL:-15}"
SELF="$DIR/$(basename "$0")"
CONFIRMED="$VAR_DIR/netmode-product-confirmed"   # a phone has joined the hotspot since product was armed

MODE_FILE="$BOOT_DIR/memoryframe-mode.txt"
WIFI_FILE="$BOOT_DIR/memoryframe-dev-wifi.txt"
RESET_FILE="$BOOT_DIR/memoryframe-reset"
STATE="$STATE_DIR/netmode.json"
LOCK="$STATE_DIR/netmode.lock"
PENDING="$STATE_DIR/wifi-change-pending"     # written by the MF-18 helper while a change is pending

log() {
    echo "netmode: $*" >&2
    logger -t memoryframe-netmode -- "$*" 2>/dev/null || true
}

usage() { echo "usage: $0 product|dev|status|boot" >&2; exit 2; }

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

hotspot_autoconnect() {   # yes|no: only ever the MemoryFrame hotspot profile is modified
    local want="$1" prio=0
    if [ "$want" = yes ]; then prio=100; fi
    if [ "$(prop "$HOTSPOT_CON" connection.autoconnect)" != "$want" ]; then
        "$NMCLI" connection modify "$HOTSPOT_CON" connection.autoconnect "$want" \
            connection.autoconnect-priority "$prio" >/dev/null 2>&1 && log "hotspot autoconnect $want" || true
    fi
}

json_str() { local s="${1//\\/\\\\}"; s="${s//\"/\\\"}"; printf '"%s"' "${s//[$'\t\r\n']/ }"; }

write_state() {   # mode requested dev_fallback hotspot_error
    local boot_id=""
    [ -r "$BOOT_ID_FILE" ] && boot_id="$(trim "$(cat "$BOOT_ID_FILE")")"
    mkdir -p "$STATE_DIR"
    {
        printf '{"mode": %s, "requested": %s, "dev_fallback": %s, "hotspot_error": %s, ' \
            "$(json_str "$1")" "$(json_str "$2")" "$3" "$4"
        printf '"active": %s, "boot_id": %s}\n' "$(json_str "$(active)")" "$(json_str "$boot_id")"
    } > "$STATE.tmp"
    chmod 0644 "$STATE.tmp"
    mv "$STATE.tmp" "$STATE"
}

apply_product() {   # requested dev_fallback [fallback]
    # Bring the hotspot up and VERIFY it before anything else changes. On failure the previous
    # connection is restored. "fallback" (dev Wi-Fi missing): hotspot for this boot only, not preferred.
    local prev try
    prev="$(active)"
    if [ "$prev" = "$HOTSPOT_CON" ]; then
        log "product: hotspot already active"
        if [ "${3:-}" != fallback ]; then hotspot_autoconnect yes; fi
        write_state product "$1" "$2" false
        return 0
    fi
    if ! profiles | grep -qxF "$HOTSPOT_CON"; then
        log "product: hotspot profile '$HOTSPOT_CON' is missing (create it with deploy/hotspot.sh or memoryframe-reset)"
        write_state product "$1" "$2" true
        return 1
    fi
    for try in 1 2; do
        if "$NMCLI" --wait 30 connection up "$HOTSPOT_CON" >/dev/null 2>&1 && [ "$(active)" = "$HOTSPOT_CON" ]; then
            log "product: hotspot up"
            if [ "${3:-}" != fallback ]; then hotspot_autoconnect yes; fi
            write_state product "$1" "$2" false
            return 0
        fi
        log "product: hotspot activation failed (attempt $try)"
    done
    if [ -n "$prev" ] && [ "$(active)" != "$prev" ]; then
        log "product: restoring the previous connection ($prev)"
        "$NMCLI" --wait "$DEV_WAIT" connection up "$prev" >/dev/null 2>&1 || log "could not restore $prev"
    fi
    write_state product "$1" "$2" true
    return 1
}

left_by_product() {
    # Product mode was applied before (the hotspot is the preferred boot connection).
    profiles | grep -qxF "$HOTSPOT_CON" && [ "$(prop "$HOTSPOT_CON" connection.autoconnect)" = yes ]
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
        apply_product dev true fallback || true
        return 1
    fi
    if profiles | grep -qxF "$HOTSPOT_CON"; then hotspot_autoconnect no; fi
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
    apply_product dev true fallback || true
    return 1
}

# ---------- first product boot: revert if no phone joins ----------

station_seen() { "$IW" dev "$IFACE" station dump 2>/dev/null | grep -q '^Station '; }

start_watch() {
    if [ -e "$CONFIRMED" ] || [ "$CONFIRM_MIN" = 0 ] || [ "$(active)" != "$HOTSPOT_CON" ]; then return 0; fi
    if [ -z "$SYSTEMD_RUN" ]; then return 0; fi
    log "product: first product boot; reverting to developer mode if no phone joins within $CONFIRM_MIN min"
    "$SYSTEMD_RUN" --unit=memoryframe-product-watch --collect --quiet "$SELF" product-watch \
        >/dev/null 2>&1 || log "could not start the product-watch timer"
}

product_watch() {
    local secs end
    secs="$(awk -v m="$CONFIRM_MIN" 'BEGIN { if (m + 0 <= 0) m = 10; printf "%d", m * 60 + 0.999 }')"
    end=$(( $(date +%s) + secs ))
    while :; do
        if station_seen; then
            mkdir -p "$VAR_DIR"; : > "$CONFIRMED"
            log "product: a phone joined the hotspot; product mode confirmed"
            return 0
        fi
        if [ "$(date +%s)" -ge "$end" ]; then break; fi
        sleep "$CONFIRM_POLL"
    done
    if [ "$(requested_mode)" != product ]; then return 0; fi
    take_lock
    log "product: no phone joined the hotspot within $CONFIRM_MIN min; reverting to developer mode (home Wi-Fi)"
    write_mode_file dev
    hotspot_autoconnect no
    apply_dev || true
}

apply() {
    case "$1" in
        dev) apply_dev ;;
        product) apply_product product false && start_watch ;;
        *)  # unarmed: developer default, nothing touched (current behaviour before MF-36)
            log "no product mode armed (memoryframe-mode.txt missing/empty): developer default, network left as is"
            write_state dev none false false ;;
    esac
}

take_lock() {
    mkdir -p "$STATE_DIR"
    exec 9>"$LOCK"
    if ! flock -n 9; then
        log "another network mode switch is in progress"; exit 3
    fi
    if [ -e "$PENDING" ]; then
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

case "${1:-}" in
    status) status ;;
    product|dev)
        take_lock
        if [ "$1" = product ]; then rm -f "$CONFIRMED"; fi   # explicit arm: watch the first product boot
        write_mode_file "$1"
        apply "$1" ;;
    product-watch) product_watch ;;
    boot)
        take_lock
        if [ -e "$RESET_FILE" ]; then
            log "memoryframe-reset found: restoring the default hotspot, product mode"
            rm -f "$RESET_FILE"
            write_mode_file product
            if [ -x "$HOTSPOT_SH" ]; then
                "$HOTSPOT_SH" </dev/null >/dev/null 2>&1 || log "hotspot profile reset failed"
            fi
        fi
        # Boot never fails the unit (no reboot loop); the state file carries the outcome.
        # SAFETY: the boot service is a strict no-op unless the mode file explicitly says product,
        # or product mode was applied before and must be undone (mode file dev/missing).
        req="$(requested_mode)"
        if [ "$req" = product ]; then
            apply product || true
        elif left_by_product; then
            log "boot: product mode was applied before and is no longer armed: back to developer Wi-Fi"
            hotspot_autoconnect no
            apply_dev || true
        else
            log "boot: product mode not armed; developer default, network left as is (no-op)"
            write_state dev "$req" false false
        fi ;;
    *) usage ;;
esac
