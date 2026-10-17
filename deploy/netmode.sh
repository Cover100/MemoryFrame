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
#   memoryframe-mode.txt      one word: product | dev. Missing/empty/other -> PRODUCT (hotspot)
#                             at every boot (MF-51). "dev" forces developer Wi-Fi persistently on
#                             every boot (the dev Pi's guard and the offline recovery path).
#                             install.sh writes "dev" only when the Pi is on a home Wi-Fi client
#                             connection and no mode file exists; it never changes an existing file.
#                             `netmode.sh dev-temp` (frame's hidden Developer Wi-Fi) writes nothing:
#                             home Wi-Fi until the next reboot only.
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
#            Once up, the hotspot ALWAYS stays on (no "no phone joined" fallback; MF-51 decision).
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
# As root (boot unit, the app's polkit-started units, sudo by hand) every hook above is IGNORED and
# PATH is fixed, so an inherited environment can never point root at other code or directories.
# MEMORYFRAME_AS_ROOT_TEST=1 only makes a non-root test run behave as root (it can't do the reverse).
set -euo pipefail

if [ "$EUID" = 0 ] || [ "${MEMORYFRAME_AS_ROOT_TEST:-}" = 1 ]; then
    export PATH=/usr/sbin:/usr/bin:/sbin:/bin
    for _v in $(compgen -e); do
        case "$_v" in MEMORYFRAME_*|NMCLI|IW|SYSTEMD_RUN|SUDO) unset "$_v" ;; esac
    done
    unset _v
    MEMORYFRAME_HOTSPOT_SH=/usr/local/sbin/memoryframe-hotspot   # root-owned copy (install.sh)
fi

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
OLD_CON="${MEMORYFRAME_OLD_CON:-MemoryFrame-hotspot-old}"   # only during wifi-commit
NM_DIR="${MEMORYFRAME_NM_DIR:-/etc/NetworkManager/system-connections}"
RESET_DONE=false
COMMIT_RECOVERED=false

log() {
    echo "netmode: $*" >&2
    logger -t memoryframe-netmode -- "$*" 2>/dev/null || true
}

usage() { echo "usage: $0 product|dev|dev-temp|status|boot|wifi-apply|wifi-commit|wifi-rollback" >&2; exit 2; }

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
    # MF-51: every boot is product mode (the hotspot) unless the file explicitly says "dev"
    # (the dev Pi's persistent guard and the offline recovery path). Missing/empty/unknown -> product.
    case "$line" in
        product|dev) echo "$line" ;;
        "") echo product ;;
        *) log "memoryframe-mode.txt: unknown mode, using product (write 'dev' to force developer mode)"; echo product ;;
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
        if "$NMCLI" connection modify "$HOTSPOT_CON" connection.autoconnect "$want" \
            connection.autoconnect-priority "$prio" >/dev/null 2>&1; then
            log "hotspot autoconnect $want"
        fi
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
        printf '"active": %s, "boot_id": %s, "reset": %s, ' "$(json_str "$(active)")" "$(json_str "$boot_id")" "$RESET_DONE"
        # MF-18 reconciliation for the app (no secrets): the hotspot profile's uuid, whether an
        # MF-18 pending/old profile still exists, and whether boot finished an interrupted commit.
        local wp=false
        if profiles | grep -qxF -e "$PENDING_CON" -e "$OLD_CON"; then wp=true; fi
        printf '"hotspot_uuid": %s, "wifi_profiles": %s, "commit_recovered": %s}\n' \
            "$(json_str "$(prop "$HOTSPOT_CON" connection.uuid)")" "$wp" "$COMMIT_RECOVERED"
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

apply_dev() {   # [temp]: MF-51 menu "Dev mode" until reboot (hotspot profile left untouched)
    local ssid p temp="${1:-}" req=dev
    if [ "$temp" = temp ]; then req=dev-temp; fi
    ssid="$(dev_ssid)"
    if ! p="$(dev_profile "$ssid")"; then
        log "dev: no matching NetworkManager Wi-Fi profile${ssid:+ for the configured ssid}; starting the hotspot"
        apply_product "$req" true fallback || true
        return 1
    fi
    if [ "$temp" != temp ] && profiles | grep -qxF "$HOTSPOT_CON"; then hotspot_autoconnect no; fi
    if [ "$(active)" = "$p" ]; then
        log "dev: already on $p"
        write_state dev "$req" false false
        return 0
    fi
    if [ "$(active)" = "$HOTSPOT_CON" ]; then "$NMCLI" connection down "$HOTSPOT_CON" >/dev/null 2>&1 || true; fi
    if "$NMCLI" --wait "$DEV_WAIT" connection up "$p" >/dev/null 2>&1 && [ "$(active)" = "$p" ]; then
        log "dev: connected ($p)${temp:+ until the next reboot}"
        write_state dev "$req" false false
        return 0
    fi
    log "dev: developer Wi-Fi did not connect; starting the hotspot (mode file unchanged; retried next boot)"
    apply_product "$req" true fallback || true
    return 1
}

apply() {
    case "$1" in
        dev) apply_dev ;;
        product) apply_product product false ;;
        *) apply_product product false ;;   # MF-51: anything else is product
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
        product) if [ -f "$MODE_FILE" ]; then echo "armed for next boot: product (memoryframe-mode.txt = product; the hotspot replaces home Wi-Fi)"
                 else echo "armed for next boot: product (no mode file: product is the default; write 'dev' to $MODE_FILE to keep home Wi-Fi)"; fi ;;
        dev)     echo "armed for next boot: dev (memoryframe-mode.txt = dev)" ;;
        *)       echo "armed for next boot: product (no mode file: product default)" ;;
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
    # Restore whatever was active before wifi-apply (recorded in $PENDING; default the hotspot).
    local prev=""
    if [ -s "$PENDING" ]; then prev="$(head -n1 "$PENDING")"; fi
    if [ -z "$prev" ] || [ "$prev" = "$PENDING_CON" ] || ! profiles | grep -qxF "$prev"; then prev="$HOTSPOT_CON"; fi
    if [ "$(active)" = "$PENDING_CON" ]; then "$NMCLI" connection down "$PENDING_CON" >/dev/null 2>&1 || true; fi
    local ok=0
    if [ "$(active)" != "$prev" ]; then
        "$NMCLI" --wait 30 connection up "$prev" >/dev/null 2>&1 || ok=1
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
    local prev
    prev="$(active)"
    if [ "$prev" != "$HOTSPOT_CON" ]; then
        log "wifi change refused: wlan0 is not on $HOTSPOT_CON (active: ${prev:-none})"; exit 3
    fi
    printf '%s\n' "$prev" > "$PENDING"          # rollback restores exactly this
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
        printf 'uuid=%s\n' "$(prop "$PENDING_CON" connection.uuid)"   # stdout -> app (not secret)
        return 0
    fi
    log "wifi change: the new hotspot did not start"
    wifi_rollback || true
    exit 4
}

recover_commit() {   # boot: finish or undo a wifi-commit cut short by a power loss
    if ! profiles | grep -qxF "$OLD_CON"; then return 0; fi
    if profiles | grep -qxF "$HOTSPOT_CON"; then
        log "boot: interrupted Wi-Fi commit: new hotspot profile in place, removing the old one"
        "$NMCLI" connection delete "$OLD_CON" >/dev/null 2>&1 || true
        COMMIT_RECOVERED=true
    else
        log "boot: interrupted Wi-Fi commit: restoring the old hotspot profile"
        "$NMCLI" connection modify "$OLD_CON" connection.id "$HOTSPOT_CON" >/dev/null 2>&1 || true
    fi
}

wifi_commit() {
    require_product
    if ! profiles | grep -qxF "$PENDING_CON" || [ "$(active)" != "$PENDING_CON" ]; then
        log "wifi commit refused: no active pending profile"; exit 4
    fi
    # Power-cut safe: rename old -> OLD_CON, pending -> HOTSPOT_CON, and only then delete the old
    # one. At every point a hotspot profile exists; boot (recover_commit) finishes or undoes it.
    if profiles | grep -qxF "$HOTSPOT_CON"; then
        "$NMCLI" connection modify "$HOTSPOT_CON" connection.id "$OLD_CON"
    fi
    "$NMCLI" connection modify "$PENDING_CON" connection.id "$HOTSPOT_CON"
    "$NMCLI" connection modify "$HOTSPOT_CON" connection.autoconnect yes connection.autoconnect-priority 100 >/dev/null 2>&1 || true
    rm -f "$PENDING"
    if profiles | grep -qxF "$OLD_CON"; then "$NMCLI" connection delete "$OLD_CON" >/dev/null 2>&1 || true; fi
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
    dev-temp)   # MF-51: frame menu "Dev mode": home Wi-Fi until the next reboot. No file is written;
        take_lock   # /run is tmpfs, so nothing survives a reboot and boot returns to product.
        apply_dev temp || exit 1 ;;
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
        recover_commit
        if [ -e "$PENDING" ] || profiles | grep -qxF "$PENDING_CON"; then
            log "unconfirmed Wi-Fi change found at boot: rolling back"
            delete_pending_profile; rm -f "$PENDING"
        fi
        # Boot never fails the unit (no reboot loop); the state file carries the outcome.
        # MF-51: product (hotspot) on every boot unless memoryframe-mode.txt says dev (persistent
        # dev). A missing file means product; install.sh writes "dev" on a Pi found on home Wi-Fi.
        if [ "$(requested_mode)" = dev ]; then
            log "boot: memoryframe-mode.txt = dev: developer Wi-Fi"
            apply_dev || true
        else
            apply_product product false || true
        fi ;;
    *) usage ;;
esac
