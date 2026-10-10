#!/usr/bin/env bash
# Create/update a persistent NetworkManager Wi-Fi hotspot for Memory Frame.
#
#   deploy/hotspot.sh              # create or update (idempotent)
#   deploy/hotspot.sh --rollback   # delete the Memory Frame profile, re-enable the old one
#
# SSID/password come from deploy/hotspot.conf (git-ignored, see
# hotspot.conf.example) or are prompted for. Nothing secret is stored in git.
# The frame is then reachable at http://10.42.0.1:5000 (no internet needed).
#
# WARNING: running this switches wlan0 to access-point mode. Keep a keyboard
# + screen or Ethernet connected while installing in case you need to roll back.
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
CONF="${MEMORYFRAME_HOTSPOT_CONF:-$DIR/hotspot.conf}"
MIN_LEN=8
RECOMMENDED_LEN=12
OLD_CON="Hotspot"   # name used by 'nmcli device wifi hotspot'

SSID="" PASSWORD=""
IFACE="wlan0" ADDRESS="10.42.0.1/24" CON_NAME="MemoryFrame-hotspot" BAND="bg" CHANNEL="6"
# shellcheck disable=SC1090
[ -f "$CONF" ] && . "$CONF"

NMCLI="${NMCLI:-nmcli}"
SUDO="${SUDO-sudo}"
nm() { $SUDO "$NMCLI" "$@"; }

if [ "${1:-}" = "--rollback" ]; then
    nm connection delete "$CON_NAME" 2>/dev/null && echo "Deleted $CON_NAME" || echo "$CON_NAME not present"
    if nm -g NAME connection show | grep -qx "$OLD_CON"; then
        nm connection modify "$OLD_CON" connection.autoconnect yes
        echo "Re-enabled autoconnect on old '$OLD_CON' profile"
    fi
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

if nm -g NAME connection show | grep -qx "$CON_NAME"; then
    echo "Updating existing profile $CON_NAME"
else
    echo "Creating profile $CON_NAME"
    nm connection add type wifi ifname "$IFACE" con-name "$CON_NAME" ssid "$SSID"
fi

nm connection modify "$CON_NAME" \
    connection.interface-name "$IFACE" \
    connection.autoconnect yes \
    connection.autoconnect-priority 100 \
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

# Stop the old manual hotspot profile from competing for wlan0 (kept, not deleted).
if [ "$OLD_CON" != "$CON_NAME" ] && nm -g NAME connection show | grep -qx "$OLD_CON"; then
    nm connection modify "$OLD_CON" connection.autoconnect no
    echo "Disabled autoconnect on old '$OLD_CON' profile (rollback re-enables it)"
fi

nm connection up "$CON_NAME"
echo "Hotspot '$SSID' active. Frame URL: http://${ADDRESS%/*}:5000"
