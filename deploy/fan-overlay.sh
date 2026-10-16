#!/usr/bin/env bash
# Argon/Armor Lite case fan: on at 60 °C via the gpio-fan overlay on GPIO 14.
# Idempotent: adds the line once, never duplicates it, and backs up config.txt before editing.
# An existing (different) gpio-fan overlay is left alone. Takes effect after the next reboot.
# Test hook: MEMORYFRAME_BOOT_CONFIG.
set -euo pipefail
CONFIG="${MEMORYFRAME_BOOT_CONFIG:-/boot/firmware/config.txt}"   # empty -> default
LINE="dtoverlay=gpio-fan,gpiopin=14,temp=60000"

if [ ! -f "$CONFIG" ]; then
    echo "fan: $CONFIG not found; skipping (add '$LINE' by hand)" >&2
    exit 0
fi
if grep -qxF "$LINE" "$CONFIG"; then
    echo "fan: already configured"
    exit 0
fi
if grep -qE '^[[:space:]]*dtoverlay=gpio-fan' "$CONFIG"; then
    echo "fan: a different gpio-fan overlay is already in $CONFIG; left unchanged" >&2
    exit 0
fi
cp -p "$CONFIG" "$CONFIG.memoryframe-bak.$(date +%Y%m%d%H%M%S)"
if [ -s "$CONFIG" ] && [ -n "$(tail -c1 "$CONFIG")" ]; then printf '\n' >> "$CONFIG"; fi
printf '# MemoryFrame: Armor Lite fan on at 60 C (added by deploy/install.sh)\n%s\n' "$LINE" >> "$CONFIG"
echo "fan: added '$LINE' to $CONFIG (backup kept; reboot to apply)"
