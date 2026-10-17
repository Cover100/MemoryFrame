#!/usr/bin/env bash
# Create/update a NetworkManager Wi-Fi hotspot profile for Memory Frame.
#
#   deploy/hotspot.sh                    # create/update profile ONLY (autoconnect=no, not activated)
#   deploy/hotspot.sh --test SECONDS     # create/update, then bring it up for SECONDS and
#                                        # automatically switch back to the previous Wi-Fi
#   deploy/hotspot.sh --rollback         # take the profile down, delete it, let NM reconnect wlan0
#
# The profile always has autoconnect=no (also when an existing profile is
# updated), so NetworkManager never starts it on its own. At boot it is brought
# up only by deploy/netmode.sh (MF-36 product mode, memoryframe-netmode.service);
# otherwise only through an explicit --test run.
#
# SAFETY: on a Raspberry Pi 4 wlan0 cannot be a client and an AP at the same
# time through NetworkManager. Activating the hotspot DROPS the current Wi-Fi
# (and any remote access over it). The default run therefore never activates
# anything. Only use --test with someone at the device
# (keyboard + screen, or Ethernet).
#
# SSID/password come from deploy/hotspot.conf (git-ignored, see
# hotspot.conf.example) or are prompted for. Nothing secret is stored in git.
# When active, the frame is reachable at http://10.42.0.1/ (or http://memoryframe.lan/).
#
# Other NetworkManager profiles (e.g. the home Wi-Fi or the old manual
# 'Hotspot' profile) are never modified or deleted by this script.
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
if [ "$(id -u)" = 0 ] || [ "${MEMORYFRAME_AS_ROOT_TEST:-}" = 1 ]; then
    # As root (e.g. the memoryframe-reset path at boot, via the root-owned /usr/local/sbin copy): no env
    # hooks, fixed PATH, and only the root-owned config.
    export PATH=/usr/sbin:/usr/bin:/sbin:/bin
    unset NMCLI SUDO SYSTEMD_RUN MEMORYFRAME_HOTSPOT_CONF
    CONF=/etc/memoryframe/hotspot.conf
    SUDO=""
else
    CONF="${MEMORYFRAME_HOTSPOT_CONF:-$DIR/hotspot.conf}"
fi
MIN_LEN=8
RECOMMENDED_LEN=12

SSID="" PASSWORD=""
IFACE="wlan0" ADDRESS="10.42.0.1/24" CON_NAME="MemoryFrame-hotspot" BAND="bg" CHANNEL="6"
# The config is parsed, never sourced: only these KEY=value lines (optionally quoted) are used.
parse_conf() {
    local line k v
    while IFS= read -r line || [ -n "$line" ]; do
        line="${line%$'\r'}"
        line="${line#"${line%%[![:space:]]*}"}"
        case "$line" in ''|'#'*) continue ;; esac
        if ! [[ "$line" =~ ^([A-Z_]+)=(.*)$ ]]; then
            echo "hotspot.conf: ignoring a line that is not KEY=value" >&2; continue
        fi
        k="${BASH_REMATCH[1]}" v="${BASH_REMATCH[2]}"
        if [[ "$v" =~ ^\"(.*)\"$ ]] || [[ "$v" =~ ^\'(.*)\'$ ]]; then v="${BASH_REMATCH[1]}"; fi
        case "$k" in
            SSID) SSID="$v" ;; PASSWORD) PASSWORD="$v" ;; IFACE) IFACE="$v" ;; ADDRESS) ADDRESS="$v" ;;
            CON_NAME) CON_NAME="$v" ;; BAND) BAND="$v" ;; CHANNEL) CHANNEL="$v" ;;
            *) echo "hotspot.conf: ignoring unknown key $k" >&2 ;;
        esac
    done < "$1"
}
if [ -f "$CONF" ]; then parse_conf "$CONF"; fi
[[ "$IFACE" =~ ^[a-zA-Z0-9_.-]{1,15}$ ]] || { echo "hotspot.conf: bad IFACE" >&2; exit 1; }
[[ "$CHANNEL" =~ ^[0-9]{1,3}$ ]] || { echo "hotspot.conf: bad CHANNEL" >&2; exit 1; }
case "$BAND" in a|bg) ;; *) echo "hotspot.conf: BAND must be a or bg" >&2; exit 1 ;; esac
[[ "$ADDRESS" =~ ^[0-9]{1,3}(\.[0-9]{1,3}){3}/[0-9]{1,2}$ ]] || { echo "hotspot.conf: bad ADDRESS" >&2; exit 1; }

NMCLI="${NMCLI:-nmcli}"
SUDO="${SUDO-sudo}"
SYSTEMD_RUN="${SYSTEMD_RUN:-systemd-run}"
nm() { $SUDO "$NMCLI" "$@"; }

MODE="create" TEST_SECONDS=""
case "${1:-}" in
    "") ;;
    --rollback) MODE="rollback" ;;
    --enable-at-boot)
        echo "--enable-at-boot is not supported: the hotspot is manual only (autoconnect is always off)." >&2
        echo "Use --test SECONDS for a timed run with automatic switch-back to the home Wi-Fi." >&2
        exit 2 ;;
    --test)
        MODE="test"; TEST_SECONDS="${2:-}"
        [[ "$TEST_SECONDS" =~ ^[0-9]+$ ]] && [ "$TEST_SECONDS" -ge 30 ] && [ "$TEST_SECONDS" -le 1800 ] \
            || { echo "--test needs SECONDS between 30 and 1800" >&2; exit 2; } ;;
    -h|--help) sed -n '2,25p' "$0"; exit 0 ;;
    *) echo "Unknown option: $1 (see --help)" >&2; exit 2 ;;
esac

profile_exists() { nm -g NAME connection show | grep -qx "$CON_NAME"; }

if [ "$MODE" = rollback ]; then
    if profile_exists; then
        nm connection down "$CON_NAME" 2>/dev/null || true
        nm connection delete "$CON_NAME"
        echo "Deleted $CON_NAME"
    else
        echo "$CON_NAME not present"
    fi
    # Let NetworkManager pick the best autoconnect profile again (e.g. home Wi-Fi).
    nm device connect "$IFACE" || echo "NetworkManager could not reconnect $IFACE automatically; run: nmcli con up <your Wi-Fi profile>" >&2
    exit 0
fi

if [ -z "$SSID" ] || [[ "$SSID" == CHANGE_ME* ]]; then
    read -r -p "Hotspot SSID: " SSID
fi
if [ -z "$PASSWORD" ] || [[ "$PASSWORD" == CHANGE_ME* ]]; then
    read -r -s -p "Hotspot password (min $RECOMMENDED_LEN chars recommended): " PASSWORD; echo
    read -r -s -p "Repeat password: " PASSWORD2; echo
    [ "$PASSWORD" = "$PASSWORD2" ] || { echo "Passwords do not match." >&2; exit 1; }
fi

[ -n "$SSID" ] && [ "${#SSID}" -le 32 ] || { echo "SSID must be 1-32 characters." >&2; exit 1; }
if [ "${#PASSWORD}" -lt "$MIN_LEN" ] || [ "${#PASSWORD}" -gt 63 ]; then
    echo "Password must be $MIN_LEN-63 characters (WPA2)." >&2; exit 1
fi
if [ "$(printf '%s' "$PASSWORD" | tr '[:upper:]' '[:lower:]')" = "memoryframe123" ]; then
    echo "The old default password is not allowed. Choose a new one." >&2; exit 1
fi
if [ "${#PASSWORD}" -lt "$RECOMMENDED_LEN" ]; then
    echo "Warning: passwords of $RECOMMENDED_LEN+ characters are recommended." >&2
fi

# Always off: the hotspot is manual only.
AUTOCONNECT=no

if profile_exists; then
    echo "Updating existing profile $CON_NAME"
else
    echo "Creating profile $CON_NAME"
    # autoconnect off from the very first moment, so NM never activates it on its own.
    nm connection add type wifi ifname "$IFACE" con-name "$CON_NAME" ssid "$SSID" autoconnect no
fi

nm connection modify "$CON_NAME" \
    connection.interface-name "$IFACE" \
    connection.autoconnect "$AUTOCONNECT" \
    connection.autoconnect-priority -10 \
    802-11-wireless.ssid "$SSID" \
    802-11-wireless.mode ap \
    802-11-wireless.band "$BAND" \
    802-11-wireless.channel "$CHANNEL" \
    ipv4.method shared \
    ipv4.addresses "$ADDRESS" \
    ipv6.method disabled \
    wifi-sec.key-mgmt wpa-psk \
    wifi-sec.proto rsn \
    wifi-sec.pairwise ccmp \
    wifi-sec.group ccmp \
    wifi-sec.psk "$PASSWORD"

case "$MODE" in
create)
    echo "Profile $CON_NAME saved with autoconnect=no. It is NOT active; your current Wi-Fi is untouched."
    echo "Test it (with the user at the Pi): $0 --test 120"
    ;;
test)
    PREV="$(nm -g GENERAL.CONNECTION device show "$IFACE" 2>/dev/null | head -n1 || true)"
    [ "$PREV" = "$CON_NAME" ] && PREV=""
    if [ -n "$PREV" ]; then
        REVERT="'$NMCLI' connection down '$CON_NAME'; '$NMCLI' connection up '$PREV' || '$NMCLI' device connect '$IFACE'"
    else
        REVERT="'$NMCLI' connection down '$CON_NAME'; '$NMCLI' device connect '$IFACE'"
    fi
    # Schedule the revert FIRST, as a transient systemd timer (runs as root, survives
    # this shell/SSH/Pi Connect session dying), then bring the hotspot up.
    $SUDO "$SYSTEMD_RUN" --on-active="$TEST_SECONDS" --unit="memoryframe-hotspot-revert-$$" \
        /bin/sh -c "$REVERT"
    echo "Revert scheduled in ${TEST_SECONDS}s (back to: ${PREV:-whatever NetworkManager picks})."
    nm connection up "$CON_NAME"
    echo "Hotspot '$SSID' active for ${TEST_SECONDS}s. Frame URL: http://${ADDRESS%/*}/ (or http://memoryframe.lan/)"
    ;;
esac
