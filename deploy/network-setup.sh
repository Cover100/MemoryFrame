#!/usr/bin/env bash
# MF-10: hostname, DNS and Wi-Fi settings for the frame's hotspot.
#
#   deploy/network-setup.sh            # apply (idempotent)
#   deploy/network-setup.sh --remove   # undo the files this script wrote (hostname is kept)
#
# What it does:
#   - hostname "memoryframe" (+ /etc/hosts 127.0.1.1) and avahi-daemon, so
#     http://memoryframe.local/ works (mDNS, Android 12+)
#   - NetworkManager dnsmasq-shared drop-in, used ONLY while the hotspot is active:
#       memoryframe.lan -> 10.42.0.1, every other name -> NXDOMAIN immediately,
#       no upstream forwarding. Phones see "no internet" (no captive-portal
#       sheet: we never redirect).
#   - Wi-Fi power save off for all NetworkManager Wi-Fi connections
#   - Wi-Fi country AU
#
# SAFETY: this script never brings any connection up or down and never edits
# the home Wi-Fi profile. NetworkManager re-reads its config ("nmcli general
# reload conf"), which does not disconnect. The DNS drop-in takes effect the
# next time the hotspot is started (deploy/hotspot.sh --test).
#
# Test hooks: ROOT (prefix for /etc paths), SUDO ("" = none).
set -euo pipefail

ROOT="${ROOT:-}"
SUDO="${SUDO-sudo}"
HOSTNAME_WANTED="${MEMORYFRAME_HOSTNAME:-memoryframe}"
AP_IP="${MEMORYFRAME_AP_IP:-10.42.0.1}"
COUNTRY="${MEMORYFRAME_WIFI_COUNTRY:-AU}"

DNS_DROPIN="$ROOT/etc/NetworkManager/dnsmasq-shared.d/memoryframe.conf"
PS_DROPIN="$ROOT/etc/NetworkManager/conf.d/memoryframe-wifi-powersave.conf"
HOSTS="$ROOT/etc/hosts"

write_file() {  # $1 = path, content on stdin; only rewrites when different
    local tmp; tmp="$(mktemp)"
    cat > "$tmp"
    if [ -f "$1" ] && cmp -s "$tmp" "$1"; then
        echo "unchanged: $1"; rm -f "$tmp"; return 0
    fi
    $SUDO mkdir -p "$(dirname "$1")"
    $SUDO install -m 644 "$tmp" "$1"
    rm -f "$tmp"
    echo "wrote:     $1"
}

if [ "${1:-}" = "--remove" ]; then
    $SUDO rm -f "$DNS_DROPIN" "$PS_DROPIN"
    echo "removed:   $DNS_DROPIN $PS_DROPIN"
    nmcli general reload conf || true
    echo "Hostname and Wi-Fi country left as they are."
    exit 0
fi

echo "== DNS for the hotspot (dnsmasq-shared drop-in)"
write_file "$DNS_DROPIN" <<CONF
# Memory Frame (MF-10). Read by NetworkManager's dnsmasq while the hotspot
# (ipv4.method shared) is active. Managed by deploy/network-setup.sh.
# Never forward queries upstream: the frame is offline.
no-resolv
# The frame's own names.
address=/memoryframe.lan/$AP_IP
address=/$HOSTNAME_WANTED/$AP_IP
# Everything else: NXDOMAIN immediately (no captive-portal redirect).
address=/#/
CONF

echo "== Wi-Fi power save off"
write_file "$PS_DROPIN" <<'CONF'
# Memory Frame (MF-10): keep the Wi-Fi radio awake (2 = disable power save).
[connection]
wifi.powersave = 2
CONF

echo "== Hostname $HOSTNAME_WANTED (memoryframe.local via avahi)"
current="$(hostnamectl --static 2>/dev/null || hostname)"
if [ "$current" != "$HOSTNAME_WANTED" ]; then
    $SUDO hostnamectl set-hostname "$HOSTNAME_WANTED"
    echo "hostname:  $current -> $HOSTNAME_WANTED"
else
    echo "hostname:  already $HOSTNAME_WANTED"
fi
if [ -f "$HOSTS" ]; then
    if grep -qE "^127\.0\.1\.1[[:space:]]" "$HOSTS"; then
        $SUDO sed -i -E "s/^127\.0\.1\.1[[:space:]].*/127.0.1.1\t$HOSTNAME_WANTED/" "$HOSTS"
    else
        echo -e "127.0.1.1\t$HOSTNAME_WANTED" | $SUDO tee -a "$HOSTS" >/dev/null
    fi
fi
if ! command -v avahi-daemon >/dev/null 2>&1; then
    $SUDO apt-get install -y avahi-daemon
fi
$SUDO systemctl enable --now avahi-daemon
$SUDO systemctl restart avahi-daemon

echo "== Wi-Fi country $COUNTRY"
if command -v raspi-config >/dev/null 2>&1; then
    $SUDO raspi-config nonint do_wifi_country "$COUNTRY"
else
    $SUDO iw reg set "$COUNTRY"
fi

echo "== Reload NetworkManager config (no disconnect)"
$SUDO nmcli general reload conf

echo "Done. DNS changes apply the next time the hotspot starts."
