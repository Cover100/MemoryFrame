#!/usr/bin/env bash
# MF-36: product mode (hotspot at boot) and developer mode (home Wi-Fi), with offline recovery.
#
#   sudo deploy/netmode.sh product   # hotspot now, and at every boot (writes the boot-partition mode file)
#   sudo deploy/netmode.sh dev       # developer Wi-Fi now, and at every boot
#   deploy/netmode.sh status         # requested mode, applied mode, active connection
#   deploy/netmode.sh boot           # what memoryframe-netmode.service runs at boot
#
# MF-18 (the app starts these as root oneshot units via polkit, deploy/units + memoryframe-polkit.rules):
#   netmode.sh wifi-apply      # reads "ssid\npassword\n" on STDIN (never argv), writes a root-only
#                              # MemoryFrame-AP-pending keyfile and switches to it; old profile kept
#   netmode.sh wifi-commit     # pending becomes the hotspot profile, old profile deleted
#   netmode.sh wifi-rollback   # old hotspot back up, pending profile deleted
#   netmode.sh home-wifi --request   # #68: dev fallback only: test + save the developer (home) Wi-Fi from
#                              # the app's request file as MemoryFrame-home; hotspot always restored
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
#   dev:     hotspot down, the dev profile up (retries for up to MEMORYFRAME_DEV_WAIT s, #135; a saved
#            client Wi-Fi NM already joined is kept). If it
#            doesn't connect: the hotspot is started for this boot only (the frame shows "Developer
#            Wi-Fi not found"); client autoconnect and the mode file are left alone, so the next
#            boot tries the home Wi-Fi again.
#   Idempotent: the same mode twice changes nothing; profiles are never created or duplicated
#   (except the hotspot profile on memoryframe-reset, via deploy/hotspot.sh).
#   Refused (exit 3) while another mode switch runs or an MF-18 Wi-Fi change is pending.
#
# The frame app reads $STATE_DIR/netmode.json (no secrets in it). It carries hotspot_ssid, the SSID of
# the MemoryFrame-hotspot profile, refreshed whenever the state is written (boot, wifi-apply/commit/
# rollback, mode changes, reset), so the app shows what is really broadcast (#89).
# Test hooks: NMCLI, MEMORYFRAME_BOOT_DIR, MEMORYFRAME_STATE_DIR, MEMORYFRAME_HOTSPOT_SH,
# MEMORYFRAME_DEV_WAIT, MEMORYFRAME_DEV_TRY_WAIT, MEMORYFRAME_DEV_RETRY_SLEEP, MEMORYFRAME_BOOT_ID_FILE,
# MEMORYFRAME_NETPLAN_DIR (read only).
# #68 dev-only test hook (root may use it): the file $STATE_DIR/force-dev-fallback (tmpfs, so gone after a
# reboot) makes dev mode act as if no developer Wi-Fi connected: hotspot for now, dev_fallback=true.
# No profile is edited. Product mode never looks at it.
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
DEV_TRY_WAIT="${MEMORYFRAME_DEV_TRY_WAIT:-15}"      # #135: max wait per boot try
DEV_RETRY_SLEEP="${MEMORYFRAME_DEV_RETRY_SLEEP:-3}"
RESTORE_WAIT=15                                      # #135: max wait to bring the previous link back
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
NETPLAN_DIR="${MEMORYFRAME_NETPLAN_DIR:-/etc/netplan}"   # #135: only ever read
HOME_CON="MemoryFrame-home"                   # #68: the developer Wi-Fi saved from the phone
HOME_PENDING="MemoryFrame-home-pending"
HOME_OLD="MemoryFrame-home-old"
HOME_STATE="$STATE_DIR/home-wifi.json"
FORCE_FALLBACK="$STATE_DIR/force-dev-fallback"
RESET_DONE=false
COMMIT_RECOVERED=false

log() {
    echo "netmode: $*" >&2
    logger -t memoryframe-netmode -- "$*" 2>/dev/null || true
}

usage() { echo "usage: $0 product|dev|dev-temp|status|boot|wifi-apply|wifi-commit|wifi-rollback|home-wifi" >&2; exit 2; }

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
# #89: the SSID the hotspot profile really broadcasts. "-e no" = raw value (no ':' / '\' escaping).
# Only the SSID is ever read here: the password is never read back, printed or put in netmode.json.
# Empty unless the name is clean: valid UTF-8, 1-32 bytes, no control characters.
hotspot_ssid() {
    local s LC_ALL=C
    s="$("$NMCLI" -e no -g 802-11-wireless.ssid connection show "$HOTSPOT_CON" 2>/dev/null || true)"
    case "$s" in *[[:cntrl:]]*) s="" ;; esac
    if [ "${#s}" -gt 32 ] || ! utf8_ok "$s"; then s=""; fi
    printf '%s' "$s"
}
active() { "$NMCLI" -g GENERAL.CONNECTION device show "$IFACE" 2>/dev/null || true; }

client_profiles() {
    local n
    while IFS= read -r n; do
        if [ -z "$n" ] || [ "$n" = "$HOTSPOT_CON" ]; then continue; fi
        if [ "$n" != "$HOME_CON" ]; then
            case "$n" in MemoryFrame-*) continue ;; esac  # pending/old profiles are not clients (#68: except MemoryFrame-home)
        fi
        [ "$(prop "$n" connection.type)" = 802-11-wireless ] || continue
        local ifn; ifn="$(prop "$n" connection.interface-name)"
        if [ -n "$ifn" ] && [ "$ifn" != "$IFACE" ]; then continue; fi   # saved for another interface
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

utf8_ok() {   # valid UTF-8? (iconv is in libc-bin; without it, only ASCII counts as valid)
    if command -v iconv >/dev/null 2>&1; then printf '%s' "$1" | iconv -f UTF-8 -t UTF-8 >/dev/null 2>&1
    else [[ "$1" != *[^\ -~]* ]]; fi
}
json_str() {   # #89: never corrupts netmode.json: invalid UTF-8 -> "", TAB/CR/LF -> space, other controls dropped
    local s="$1" LC_ALL=C
    utf8_ok "$s" || s=""
    s="${s//[$'\t\r\n']/ }"; s="${s//[[:cntrl:]]/}"
    s="${s//\\/\\\\}"; s="${s//\"/\\\"}"
    printf '"%s"' "$s"
}

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
        printf '"hotspot_uuid": %s, "wifi_profiles": %s, "commit_recovered": %s, "hotspot_ssid": %s}\n' \
            "$(json_str "$(prop "$HOTSPOT_CON" connection.uuid)")" "$wp" "$COMMIT_RECOVERED" \
            "$(json_str "$(hotspot_ssid)")"
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
        # #135 review M2: capped, so boot stays inside TimeoutStartSec=150 (~53 s dev + 2x30 s hotspot + 15 s)
        "$NMCLI" --wait "$RESTORE_WAIT" connection up "$prev" >/dev/null 2>&1 || log "could not restore $prev"
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
    # #68: the Wi-Fi saved from the phone wins (the later intent; open decision recorded in the PR)
    local all; all="$(client_profiles)"        # (not piped into grep -q: SIGPIPE + pipefail)
    if [[ $'\n'"$all"$'\n' == *$'\n'"$HOME_CON"$'\n'* ]]; then printf '%s\n' "$HOME_CON"; return 0; fi
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

dev_up() {   # profile [once]
    # #135: at boot (memoryframe-netmode.service runs right after NetworkManager starts) wlan0 or the
    # first Wi-Fi scan may not be ready, and `nmcli connection up` then fails at once ("device not
    # ready", "SSID not found") instead of waiting. Persistent dev therefore retries until DEV_WAIT is
    # used up; each try waits at most DEV_TRY_WAIT s, so a try that blocks can't eat the whole window.
    # Worst case about DEV_WAIT + 5 + DEV_RETRY_SLEEP s, then the hotspot fallback (~60 s toast, MF-51).
    # "once" (dev-temp, unit TimeoutStartSec=90): one try with the full wait, as before.
    local p="$1" end="${DEV_END:-$((SECONDS + DEV_WAIT))}" wait err
    while :; do
        if [ "${2:-}" = once ]; then wait="$DEV_WAIT"
        else
            wait=$((end - SECONDS)); [ "$wait" -ge 5 ] || wait=5
            [ "$wait" -le "$DEV_TRY_WAIT" ] || wait="$DEV_TRY_WAIT"
        fi
        if err="$("$NMCLI" --wait "$wait" connection up "$p" 2>&1 >/dev/null)" && [ "$(active)" = "$p" ]; then return 0; fi
        # nmcli's error text never contains a password; logged so a failed boot says why.
        log "dev: $p not up yet ($(printf '%s' "$err" | tr '\r\n' '  ' | cut -c1-160))"
        if [ "${2:-}" = once ] || [ "$SECONDS" -ge "$end" ]; then return 1; fi
        "$NMCLI" device wifi rescan ifname "$IFACE" >/dev/null 2>&1 || true
        sleep "$DEV_RETRY_SLEEP"
    done
}

on_client_wifi() {   # active [ssid]: is wlan0 already on a saved client (home) Wi-Fi profile?
    # #135 (H2, owner decision 10 Nov): NM may already have autoconnected a saved home profile at boot
    # (e.g. the netplan one). That counts as dev: switching away from a working link only risks it.
    # An explicit ssid= in memoryframe-dev-wifi.txt still wins: then only that network counts.
    local cur="$1" ssid="${2:-}" all
    if [ -z "$cur" ] || [ "$cur" = "$HOTSPOT_CON" ]; then return 1; fi
    all="$(client_profiles)"      # Wi-Fi client profiles on wlan0 only (no hotspot, no MemoryFrame-*-pending/old)
    [[ $'\n'"$all"$'\n' == *$'\n'"$cur"$'\n'* ]] || return 1
    [ -z "$ssid" ] || [ "$(prop "$cur" 802-11-wireless.ssid)" = "$ssid" ]
}

apply_dev() {   # [temp]: MF-51 menu "Dev mode" until reboot (hotspot profile left untouched)
    local ssid p temp="${1:-}" req=dev
    if [ "$temp" = temp ]; then req=dev-temp; fi
    ssid="$(dev_ssid)"
    if [ -e "$FORCE_FALLBACK" ]; then
        log "dev: test hook force-dev-fallback present: starting the hotspot (no profile changed)"
        apply_product "$req" true fallback || true
        return 1
    fi
    local DEV_END=$((SECONDS + DEV_WAIT))   # #135: one 45 s budget for finding AND joining the dev Wi-Fi
    until p="$(dev_profile "$ssid")"; do
        # At boot NM may not have loaded every profile yet: look again until the budget is used up.
        if [ "$temp" = temp ] || [ "$SECONDS" -ge "$DEV_END" ]; then
            log "dev: no matching NetworkManager Wi-Fi profile${ssid:+ for the configured ssid}; starting the hotspot"
            apply_product "$req" true fallback || true   # dev_fallback=true: the phone's Developer Wi-Fi page (#68)
            return 1
        fi
        sleep "$DEV_RETRY_SLEEP"
    done
    if [ "$temp" != temp ] && profiles | grep -qxF "$HOTSPOT_CON"; then hotspot_autoconnect no; fi
    if [ "$(active)" = "$p" ]; then
        log "dev: already on $p"
        write_state dev "$req" false false
        return 0
    fi
    if on_client_wifi "$(active)" "$ssid"; then
        log "dev: already on saved Wi-Fi $(active); keeping it"
        write_state dev "$req" false false
        return 0
    fi
    if [ "$(active)" = "$HOTSPOT_CON" ]; then "$NMCLI" connection down "$HOTSPOT_CON" >/dev/null 2>&1 || true; fi
    if dev_up "$p" ${temp:+once}; then
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

# #135: NetworkManager KEEPS a keyfile's file name when a profile is renamed (`connection modify ...
# connection.id`, keyfile rename=false is NM's default). After a promotion the live MemoryFrame-home /
# MemoryFrame-hotspot profile can therefore be backed by a file called *-pending*.nmconnection, so a
# keyfile is NEVER removed by its name alone: profiles are deleted through NM by id (NM removes the file
# that really backs it), and a leftover file is removed only while its id= line still names the
# pending profile (e.g. a power cut before `connection load`). New pending keyfiles get a unique name,
# so writing one can never overwrite the file of a live profile.
remove_stale_keyfiles() {   # id
    local id="$1" f
    for f in "$NM_DIR/$id".nmconnection "$NM_DIR/$id"-*.nmconnection "$NM_DIR/$id"*.tmp; do
        [ -f "$f" ] || continue
        if [ ! -s "$f" ] || grep -qxF "id=$id" "$f" 2>/dev/null; then rm -f "$f"   # empty: cut before written
        else log "kept $(basename "$f"): it now backs another profile"; fi
    done
}

new_keyfile() {   # id -> prints a fresh, unique keyfile path in NM_DIR (created 0600, empty)
    mkdir -p "$NM_DIR"
    mktemp --suffix=.nmconnection "$NM_DIR/$1-XXXXXX"
}

write_keyfile() {   # path; content on stdin. tmp + mv, 0600 (root-only: it holds a password); checked steps
    if ( umask 077; cat > "$1.tmp" && [ -s "$1.tmp" ] && chmod 0600 "$1.tmp" && sync -- "$1.tmp" && mv -f "$1.tmp" "$1" ); then
        return 0
    fi
    rm -f "$1.tmp"
    return 1
}

sync_nm_dir() {   # #135: make a promoted keyfile (renamed in place by NM) durable before we report success
    sync -f "$NM_DIR" 2>/dev/null || sync 2>/dev/null || true
}

durable_install() {   # tmp final (same dir): 0600, fsync file, rename, fsync dir (power-cut safe, #135)
    # A failed step before mv leaves final untouched; the dir fsync after mv is best effort.
    chmod 0600 "$1" && sync -- "$1" && mv -f "$1" "$2" || return 1
    sync -- "$(dirname "$2")" 2>/dev/null || sync 2>/dev/null || true
}

delete_pending_profile() {
    if profiles | grep -qxF "$PENDING_CON"; then "$NMCLI" connection delete "$PENDING_CON" >/dev/null 2>&1 || true; fi
    remove_stale_keyfiles "$PENDING_CON"
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
    sync_nm_dir
    rm -f "$PENDING"
    local err=false
    if [ "$ok" != 0 ]; then err=true; fi
    write_state product product false "$err"
    log "wifi change rolled back"
    return "$ok"
}

# #62: the request file the app leaves in its own 0700 runtime dir (memoryframe-wifi-apply.service runs
# `wifi-apply --request`). Read by the helper itself, never via systemd: no symlinks (nofollow), no
# FIFOs/devices (regular files only, nonblock), and it must be owned by the dir's owner (the app user).
APPLY_REQ="${MEMORYFRAME_APPLY_REQ:-/run/memoryframe-app/wifi-apply}"
read_request() {
    local f="$APPLY_REQ" dir owner
    dir="$(dirname "$f")"
    if [ -L "$f" ] || [ ! -f "$f" ]; then log "wifi change refused: request is not a regular file"; return 1; fi
    owner="$(stat -c %u "$dir" 2>/dev/null)" || return 1
    if [ "$owner" = 0 ] || [ "$(stat -c %u "$f" 2>/dev/null)" != "$owner" ]; then
        log "wifi change refused: request not owned by the app user"; return 1
    fi
    dd if="$f" iflag=nofollow,nonblock bs=512 count=1 status=none 2>/dev/null
}

wifi_apply() {
    local ssid="" psk="" f req
    if [ "${1:-}" = --request ]; then
        if ! req="$(read_request)"; then rm -f "$APPLY_REQ"; exit 2; fi
        rm -f "$APPLY_REQ"
        { IFS= read -r ssid || true; IFS= read -r psk || true; } <<< "$req"
    else
        IFS= read -r ssid || true
        IFS= read -r psk || true
    fi
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
    f="$(new_keyfile "$PENDING_CON")"
    {
        printf '[connection]\nid=%s\ntype=wifi\ninterface-name=%s\nautoconnect=false\n\n' "$PENDING_CON" "$IFACE"
        printf '[wifi]\nmode=ap\nband=bg\nchannel=6\nssid=%s\n\n' "$(ssid_bytes "$ssid")"
        printf '[wifi-security]\nkey-mgmt=wpa-psk\nproto=rsn\npairwise=ccmp\ngroup=ccmp\npsk=%s\n\n' "$(keyfile_escape "$psk")"
        printf '[ipv4]\nmethod=shared\naddress1=10.42.0.1/24\n\n[ipv6]\nmethod=disabled\n'
    } | write_keyfile "$f"
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
    sync_nm_dir
    save_hotspot || true   # #137: its own final file, same UUID, no restart
    write_state product product false false
    log "wifi change kept"
}

# #137: the hotspot profile gets its OWN durable final file, MemoryFrame-hotspot.nmconnection, like
# MemoryFrame-home (#135): temp in NM_DIR -> fsync -> rename -> fsync dir -> load -> verify file + live UUID.
# The UUID is KEPT (wifi.py reconcile matches the app's new_uuid with netmode.json hotspot_uuid), so this is
# a storage move, not a new profile: the old file (NM keeps a *-pending-* name on rename) is removed and both
# paths are loaded in ONE `connection load`: NM (keyfile plugin >= 1.20) unloads a missing file's storage and
# keeps the profile on the other file with that UUID (same dir: newer mtime wins), so the live, even active,
# profile is re-bound without re-activation (no hotspot restart). Only files in NM_DIR are touched.
hotspot_file() {   # uuid -> the file NetworkManager says backs that profile (empty if none)
    "$NMCLI" -g UUID,FILENAME connection show 2>/dev/null | sed -n "s|^$1:||p" | head -n1
}

hotspot_saved() {   # final uuid: on disk (non-empty, our uuid= and id=) AND live in NM from that file
    [ -s "$1" ] && grep -qxF "uuid=$2" "$1" && grep -qxF "id=$HOTSPOT_CON" "$1" \
        && [ "$(hotspot_file "$2")" = "$1" ] && [ "$(prop "$HOTSPOT_CON" connection.uuid)" = "$2" ]
}

save_hotspot() {   # boot and wifi-commit; never fails the caller (the profile stays where it was)
    local final="$NM_DIR/$HOTSPOT_CON.nmconnection" uuid src tmp prio f
    uuid="$(prop "$HOTSPOT_CON" connection.uuid)"
    [[ $uuid =~ ^[0-9A-Za-z-]{1,64}$ ]] || return 0
    src="$(hotspot_file "$uuid")"
    case "$src" in "$NM_DIR"/*.nmconnection) ;; "") return 0 ;;
        *) log "hotspot: backed by $src (netplan/OS profile); left as it is"; return 0 ;; esac
    if [ ! -s "$src" ]; then   # memory only (its file was removed): NM writes it back to its own file
        prio="$(prop "$HOTSPOT_CON" connection.autoconnect-priority)"
        "$NMCLI" connection modify "$HOTSPOT_CON" connection.autoconnect-priority "$(( ${prio:-0} + 1 ))" >/dev/null 2>&1 || true
        "$NMCLI" connection modify "$HOTSPOT_CON" connection.autoconnect-priority "${prio:-0}" >/dev/null 2>&1 || true
        if [ ! -s "$src" ]; then log "hotspot: profile is in memory only and could not be written out"; return 1; fi
        log "hotspot: profile was in memory only; written back to $(basename "$src")"
    fi
    if [ "$src" != "$final" ]; then
        if [ -s "$final" ] && ! grep -qxF "uuid=$uuid" "$final"; then   # e.g. the old hotspot not deleted yet
            log "hotspot: $(basename "$final") still backs another profile; kept in $(basename "$src")"; return 1
        fi
        tmp="$(mktemp "$NM_DIR/.$HOTSPOT_CON.XXXXXX")" || { log "hotspot: no temp file; kept in $(basename "$src")"; return 1; }
        if ! { sed -e "s/^id=.*/id=$HOTSPOT_CON/" -e "/^uuid=/d" -e "s/^\[connection\]\$/[connection]\nuuid=$uuid/" "$src" > "$tmp" \
                && grep -qxF "uuid=$uuid" "$tmp" && grep -qxF "id=$HOTSPOT_CON" "$tmp" && durable_install "$tmp" "$final"; }; then
            rm -f "$tmp"; log "hotspot: could not write $(basename "$final"); kept in $(basename "$src")"; return 1
        fi
        rm -f "$src"; sync_nm_dir
        "$NMCLI" connection load "$final" "$src" >/dev/null 2>&1 || true
    fi
    for f in "$NM_DIR/$PENDING_CON"-*.nmconnection; do   # a power cut before the old file was removed:
        # NM prefers the newer file (final), so this old copy only shadows it; unloaded once NM uses final
        if [ -f "$f" ] && grep -qxF "uuid=$uuid" "$f" && [ "$(hotspot_file "$uuid")" = "$final" ]; then
            rm -f "$f"; sync_nm_dir; "$NMCLI" connection load "$f" >/dev/null 2>&1 || true
        fi
    done
    if hotspot_saved "$final" "$uuid"; then log "hotspot: saved in $(basename "$final")"; return 0; fi
    if [ -s "$final" ] && grep -qxF "uuid=$uuid" "$final"; then
        log "hotspot: saved in $(basename "$final"), but NetworkManager has not switched to it; it is used from the next boot"
    else log "hotspot: $(basename "$final") could not be verified"; fi
    return 1
}

# ---------- #68: developer (home) Wi-Fi from the phone, dev fallback only ----------

HOME_REQ="${MEMORYFRAME_HOME_REQ:-/run/memoryframe-app/home-wifi}"
HOME_TEST_WAIT="${MEMORYFRAME_HOME_WAIT:-25}"

home_state() {   # state reason ssid_hex (never a password)
    mkdir -p "$STATE_DIR"
    printf '{"state": %s, "reason": %s, "ssid_hex": %s, "ts": %s}\n' "$(json_str "$1")" "$(json_str "$2")" \
        "$(json_str "$3")" "$(date +%s)" > "$HOME_STATE.tmp"
    chmod 0644 "$HOME_STATE.tmp"
    mv "$HOME_STATE.tmp" "$HOME_STATE"
}

in_dev_fallback() {
    [ -f "$STATE" ] && grep -q '"mode": "product"' "$STATE" && grep -q '"dev_fallback": true' "$STATE" \
        && grep -qE '"requested": "dev(-temp)?"' "$STATE"
}

valid_home_psk() { printf '%s' "$1" | LC_ALL=C grep -qxE '[ -~]{8,63}|[0-9A-Fa-f]{64}'; }

hex_to_bytes() {   # even-length lowercase/uppercase hex -> raw bytes on stdout
    local h="$1" i
    [[ "$h" =~ ^([0-9A-Fa-f]{2}){1,32}$ ]] || return 1
    for ((i = 0; i < ${#h}; i += 2)); do printf '%b' "\\x${h:i:2}"; done
}

delete_home_pending() {   # #135: by id through NM, never a blind rm (see remove_stale_keyfiles)
    if profiles | grep -qxF "$HOME_PENDING"; then "$NMCLI" connection delete "$HOME_PENDING" >/dev/null 2>&1 || true; fi
    remove_stale_keyfiles "$HOME_PENDING"
    clean_home_temps
}

# #135: the tested Wi-Fi is saved under its OWN final file, MemoryFrame-home.nmconnection (root 0600):
# temp file in the same dir -> fsync -> rename -> fsync the dir -> `nmcli connection load` -> verify the
# file on disk and the live profile by UUID. It is never `connection modify`-ed afterwards (NM would
# rewrite it with its own writer). The previous MemoryFrame-home is deleted by UUID only after the new one
# is verified, and the pending profile last, so a power cut leaves at least one saved copy on disk.
HOME_BAK=".$HOME_CON.bak"   # in NM_DIR; dot files are never loaded by NM

promote_home() {   # pending keyfile path
    local src="$1" final="$NM_DIR/$HOME_CON.nmconnection" bak="$NM_DIR/$HOME_BAK" uuid old
    uuid="$(cat /proc/sys/kernel/random/uuid)"
    if ! [[ $uuid =~ ^[0-9a-f-]{36}$ ]]; then log "home-wifi: no valid new UUID; nothing changed"; return 1; fi
    old="$(prop "$HOME_CON" connection.uuid)"
    # NOTE: called as `if ! promote_home`, so set -e is OFF in here: every step is checked explicitly.
    # A failed or short copy must never replace a good final file (#135 review M1), and if NM then
    # refuses the new file, the previous one is put back (#135 QA N1). Log lines say what really happened.
    rm -f "$bak"
    if [ -s "$final" ]; then
        # #137 R2: the backup holds the password: created 0600 itself, never inheriting the final file's mode
        if ! { ( umask 077; cp "$final" "$bak" ) && chmod 0600 "$bak" && sync -- "$bak"; }; then
            rm -f "$bak"; log "home-wifi: could not back up $final; nothing changed"; return 1
        fi
    fi
    if ! stage_home "$src" "$uuid" "$final"; then
        rm -f "$bak"; log "home-wifi: could not write $final; previous developer Wi-Fi kept"; return 1
    fi
    "$NMCLI" connection load "$final" >/dev/null 2>&1 || true
    if ! home_saved "$final" "$uuid"; then restore_home_bak "NetworkManager did not load the new file" "$uuid"; return 1; fi
    if [ -n "$old" ] && [ "$old" != "$uuid" ]; then
        "$NMCLI" connection delete uuid "$old" >/dev/null 2>&1 || true
        if [ ! -s "$final" ]; then   # the old profile was backed by the same path: write it once more
            if stage_home "$src" "$uuid" "$final"; then "$NMCLI" connection load "$final" >/dev/null 2>&1 || true; fi
            if ! home_saved "$final" "$uuid"; then restore_home_bak "saved file lost while replacing the old one" "$uuid"; return 1; fi
        fi
    fi
    rm -f "$bak"
    return 0
}

restore_home_bak() {   # why new_uuid: undo a failed save. Previous file back (durably) and live again,
    # else (first save) nothing saved at all, so the phone's "failed" is the whole truth (#137 N3).
    local final="$NM_DIR/$HOME_CON.nmconnection" bak="$NM_DIR/$HOME_BAK"
    # #137 N4: drop the half-loaded new profile first (NM may hold it under its UUID; deleting it by UUID
    # also removes its file, which is replaced or removed right below anyway)
    if [[ ${2:-} =~ ^[0-9a-f-]{36}$ ]]; then "$NMCLI" connection delete uuid "$2" >/dev/null 2>&1 || true; fi
    if [ ! -s "$bak" ]; then   # first save (or a pre-#135 profile under another file name, left as it is)
        rm -f "$final" "$bak"; log "home-wifi: $1; nothing new saved"
    elif durable_install "$bak" "$final"; then
        if load_home "$final"; then log "home-wifi: $1; previous developer Wi-Fi restored"
        else log "home-wifi: $1; previous developer Wi-Fi file restored but NetworkManager did not load it"; fi
    else   # #138 M1: never remove the last good copy; boot (clean_home_temps) puts it back if final is gone
        # #137 double fault: the untested new file must not be used at the next boot (the phone said "failed")
        rm -f "$final"
        HOME_BAK_KEEP=1; log "home-wifi: $1; could not put the previous developer Wi-Fi file back; new file removed, backup kept"
    fi
}

load_home() {   # final: load it and verify the live profile has the file's uuid= (as MemoryFrame-home)
    local u
    "$NMCLI" connection load "$1" >/dev/null 2>&1 || true
    u="$(sed -n 's/^uuid=//p' "$1" 2>/dev/null | head -n1)"
    if [ -n "$u" ]; then home_saved "$1" "$u"; else [ "$(prop "$HOME_CON" connection.id)" = "$HOME_CON" ]; fi
}

clean_home_temps() {   # #135 QA: no stray copy of the password may stay behind (failure paths, kill, boot)
    local final="$NM_DIR/$HOME_CON.nmconnection" f
    if [ -s "$NM_DIR/$HOME_BAK" ] && [ ! -s "$final" ]; then   # cut while replacing: previous file back
        # #137 R1: NM started before us and skipped the empty file: load it now so this boot can use it
        if durable_install "$NM_DIR/$HOME_BAK" "$final"; then
            if load_home "$final"; then log "home-wifi: previous developer Wi-Fi file restored and loaded"
            else log "home-wifi: previous developer Wi-Fi file restored, but NetworkManager did not load it"; fi
        else HOME_BAK_KEEP=1; log "home-wifi: could not restore the previous developer Wi-Fi file; backup kept"; fi
    fi
    # #138 M1: a backup that could not be put back is the last copy of the previous Wi-Fi: keep it
    for f in "$NM_DIR/.$HOME_CON".*; do [[ -n ${HOME_BAK_KEEP:-} && $f == "$NM_DIR/$HOME_BAK" ]] || rm -f "$f"; done
    return 0
}

stage_home() {   # src uuid final: copy the tested pending keyfile as MemoryFrame-home and install it durably
    # The password stays in the files: sed copies it, it is never in a shell variable or argv.
    # Only a complete copy (non-empty, carrying our uuid= and id=) is installed; otherwise final is untouched.
    local src="$1" uuid="$2" final="$3" tmp
    tmp="$(mktemp "$NM_DIR/.$HOME_CON.XXXXXX")" || return 1   # dot file: NM never loads it
    if sed -e "s/^id=.*/id=$HOME_CON/" -e "/^uuid=/d" -e "s/^\[connection\]\$/[connection]\nuuid=$uuid/" "$src" > "$tmp" \
            && [ -s "$tmp" ] && grep -qxF "uuid=$uuid" "$tmp" && grep -qxF "id=$HOME_CON" "$tmp" \
            && grep -q '^psk=' "$tmp" && durable_install "$tmp" "$final"; then
        return 0
    fi
    rm -f "$tmp"
    return 1
}

home_saved() {   # final uuid: on disk (non-empty, our UUID) AND live in NM under that UUID as MemoryFrame-home
    [ -s "$1" ] && grep -qxF "uuid=$2" "$1" \
        && [ "$("$NMCLI" -g connection.id connection show uuid "$2" 2>/dev/null)" = "$HOME_CON" ]
}

warn_empty_netplan() {   # #135: read-only. NM's netplan files can be left 0 bytes by a power cut
    # (raspberrypi/trixie-feedback#99); the home profile then silently vanishes. /etc/netplan is never touched.
    local f
    for f in "$NETPLAN_DIR"/90-NM-*.yaml; do
        if [ -f "$f" ] && [ ! -s "$f" ]; then
            log "WARNING: $(basename "$f") is empty (0 bytes): its NetworkManager profile is gone; re-save the developer Wi-Fi from the phone"
        fi
    done
}

recover_home() {   # boot: never trust an untested profile; finish an interrupted rename
    delete_home_pending
    if profiles | grep -qxF "$HOME_OLD"; then
        if profiles | grep -qxF "$HOME_CON"; then "$NMCLI" connection delete "$HOME_OLD" >/dev/null 2>&1 || true
        else "$NMCLI" connection modify "$HOME_OLD" connection.id "$HOME_CON" >/dev/null 2>&1 || true; fi
    fi
}

HOME_RESTORE_ARMED=false
home_restore() {   # always: pending down + deleted unless promoted, hotspot back (2 tries)
    [ "$HOME_RESTORE_ARMED" = true ] || return 0
    HOME_RESTORE_ARMED=false
    set +e
    if [ "$(active)" = "$HOME_PENDING" ]; then "$NMCLI" connection down "$HOME_PENDING" >/dev/null 2>&1; fi
    delete_home_pending
    if [ "$(active)" != "$HOTSPOT_CON" ]; then apply_product "$HOME_REQUESTED" true fallback >/dev/null 2>&1; fi
}

home_wifi() {
    local req ver hex ssid psk f r=connect_failed
    [ "${1:-}" = --request ] || { log "home-wifi: --request required"; exit 2; }
    req="$(APPLY_REQ="$HOME_REQ" read_request)" || { rm -f "$HOME_REQ"; home_state failed busy ""; exit 2; }
    rm -f "$HOME_REQ"
    { IFS= read -r ver || true; IFS= read -r hex || true; IFS= read -r psk || true; } <<< "$req"
    req=""
    if ! in_dev_fallback || [ "$(active)" != "$HOTSPOT_CON" ]; then
        log "home-wifi refused: only while developer mode fell back to the hotspot"; home_state failed busy ""; exit 3
    fi
    if [ "$ver" != v1 ] || ! ssid="$(hex_to_bytes "$hex")" \
            || [ "$(printf '%s' "$ssid" | LC_ALL=C wc -c)" -ne "$((${#hex} / 2))" ] || ! valid_ssid "$ssid" || ! valid_home_psk "$psk" \
            || [ "$ssid" = "$(hotspot_ssid)" ]; then
        log "home-wifi refused: invalid name or password"; home_state failed invalid ""; exit 2
    fi
    HOME_REQUESTED="$(sed -n 's/.*"requested": "\([a-z-]*\)".*/\1/p' "$STATE")"
    home_state testing "" "$hex"
    delete_home_pending
    f="$(new_keyfile "$HOME_PENDING")"
    {
        printf '[connection]\nid=%s\ntype=wifi\ninterface-name=%s\nautoconnect=false\n\n' "$HOME_PENDING" "$IFACE"
        printf '[wifi]\nmode=infrastructure\nssid=%s\n\n' "$(ssid_bytes "$ssid")"
        printf '[wifi-security]\nkey-mgmt=wpa-psk\npsk=%s\n\n' "$(keyfile_escape "$psk")"
        printf '[ipv4]\nmethod=auto\n\n[ipv6]\nmethod=auto\n'
    } | write_keyfile "$f"
    psk=""
    HOME_RESTORE_ARMED=true
    trap 'home_restore' EXIT
    trap 'exit 143' TERM
    trap 'exit 130' INT
    if ! "$NMCLI" connection load "$f" >/dev/null 2>&1; then
        log "home-wifi: could not load the profile"; home_state failed connect_failed "$hex"; exit 4
    fi
    "$NMCLI" connection down "$HOTSPOT_CON" >/dev/null 2>&1 || true
    if timeout "$((HOME_TEST_WAIT + 5))" "$NMCLI" --wait "$HOME_TEST_WAIT" connection up "$HOME_PENDING" >/dev/null 2>&1 \
            && [ "$(active)" = "$HOME_PENDING" ]; then
        local i
        r=timeout
        for i in 1 2 3 4 5; do
            if [ -n "$("$NMCLI" -g IP4.ADDRESS device show "$IFACE" 2>/dev/null)" ]; then r=ok; break; fi
            sleep 1
        done
    fi
    "$NMCLI" connection down "$HOME_PENDING" >/dev/null 2>&1 || true
    if [ "$r" = ok ] && ! promote_home "$f"; then r=save_failed; fi
    if [ "$r" = ok ]; then
        home_state saved ok "$hex"
        log "home-wifi: saved (ssid length ${#ssid})"
    else
        home_state failed "$r" "$hex"
        if [ "$r" = save_failed ]; then log "home-wifi: tested OK but not saved (see above)"
        else log "home-wifi: test failed ($r); previous developer Wi-Fi kept"; fi
    fi
    home_restore
    trap - EXIT
    [ "$r" = ok ]
}

home_wifi_restore() {   # ExecStopPost: after a kill/timeout, the hotspot is back and the result is recorded
    if [ -f "$HOME_STATE" ] && grep -q '"state": "testing"' "$HOME_STATE"; then
        HOME_REQUESTED="$(sed -n 's/.*"requested": "\([a-z-]*\)".*/\1/p' "$STATE" 2>/dev/null)"
        HOME_RESTORE_ARMED=true; home_restore
        home_state failed timeout "$(sed -n 's/.*"ssid_hex": "\([0-9a-fA-F]*\)".*/\1/p' "$HOME_STATE")"
        log "home-wifi: stopped before it finished; hotspot restored"
    fi
}

case "${1:-}" in
    status) status ;;
    wifi-apply) take_lock; wifi_apply "${2:-}" ;;   # stdout (/run/memoryframe/*.out, 0644): uuid only, never the password
    wifi-commit) take_lock nopending; wifi_commit ;;
    wifi-rollback) take_lock nopending; wifi_rollback ;;
    home-wifi) take_lock; home_wifi "${2:-}" ;;
    home-wifi-restore) take_lock nopending; home_wifi_restore ;;   # stdout: nothing secret; result in home-wifi.json
    product|dev)
        take_lock
        write_mode_file "$1"
        apply "$1" ;;
    dev-temp)   # MF-51: frame menu "Dev mode": home Wi-Fi until the next reboot. No file is written;
        take_lock   # /run is tmpfs, so nothing survives a reboot and boot returns to product.
        DEV_WAIT="${MEMORYFRAME_DEV_TEMP_WAIT:-30}"   # client must connect within ~30 s, else hotspot back
        if apply_dev temp; then echo "ok: joined $(active)"; else echo "error: home Wi-Fi did not connect; hotspot kept on"; exit 1; fi ;;
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
        recover_home
        warn_empty_netplan
        if [ -e "$PENDING" ] || profiles | grep -qxF "$PENDING_CON"; then
            log "unconfirmed Wi-Fi change found at boot: rolling back"
            delete_pending_profile; rm -f "$PENDING"
        fi
        save_hotspot || true   # #137: migrate a hotspot still in a *-pending-* file or in memory only
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
