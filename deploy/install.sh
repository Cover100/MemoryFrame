#!/usr/bin/env bash
# Install Memory Frame on a Raspberry Pi 4B (Raspberry Pi OS 64-bit, desktop).
# Run from the repo checkout as the desktop user (not root):  ./deploy/install.sh
# Idempotent: safe to re-run after a git pull.
# Options:
#   --extra-hosts LIST   extra Host names/IPs the app answers (comma list, e.g. 192.168.50.124);
#                        "" removes them. Also taken from $MEMORYFRAME_EXTRA_HOSTS. Omit to keep.
set -euo pipefail

EXTRA_HOSTS_SET=0
EXTRA_HOSTS="${MEMORYFRAME_EXTRA_HOSTS-}"
[ -n "${MEMORYFRAME_EXTRA_HOSTS+x}" ] && EXTRA_HOSTS_SET=1
while [ $# -gt 0 ]; do
    case "$1" in
        --extra-hosts) EXTRA_HOSTS="${2-}"; EXTRA_HOSTS_SET=1; shift 2 || shift ;;
        --extra-hosts=*) EXTRA_HOSTS="${1#*=}"; EXTRA_HOSTS_SET=1; shift ;;
        *) echo "Unknown option: $1" >&2; exit 2 ;;
    esac
done

APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
APP_USER="$(id -un)"
[ "$APP_USER" = root ] && { echo "Run as the desktop user, not root." >&2; exit 1; }

echo "== Packages"
sudo apt-get update
sudo apt-get install -y python3-venv curl
command -v chromium-browser >/dev/null || command -v chromium >/dev/null || sudo apt-get install -y chromium-browser

echo "== Python environment"
[ -d "$APP_DIR/.venv" ] || python3 -m venv "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/pip" install -r "$APP_DIR/requirements.txt"
mkdir -p "$APP_DIR/uploads"

echo "== Data directory and one-time photo migration (MF-11)"
DATA_DIR=/var/lib/memoryframe
sudo install -d -o "$APP_USER" -g "$(id -gn)" -m 750 "$DATA_DIR"
# Stop the running app first so the migration has the database to itself.
sudo systemctl stop memoryframe.service 2>/dev/null || true
# Backs up uploads/ to $DATA_DIR/backups/legacy-uploads-<time>.tar (verified),
# then COPIES the photos into $DATA_DIR/originals. uploads/ is never changed.
# Does nothing if the import was already done. Aborts (exit 1) if space is short.
MEMORYFRAME_DATA_DIR="$DATA_DIR" MEMORYFRAME_LEGACY_UPLOADS="$APP_DIR/uploads" \
    "$APP_DIR/.venv/bin/python" -m memoryframe migrate \
    || echo "WARNING: migration failed (see above). Nothing was changed; the service is restarted anyway. Fix and re-run install.sh."
# MF-15: downscaled display copies for all photos (originals untouched; idempotent).
MEMORYFRAME_DATA_DIR="$DATA_DIR" "$APP_DIR/.venv/bin/python" -m memoryframe display-copies \
    || echo "WARNING: some display copies failed; those photos are served as originals."
MEMORYFRAME_DATA_DIR="$DATA_DIR" MEMORYFRAME_LEGACY_UPLOADS="$APP_DIR/uploads" \
    "$APP_DIR/.venv/bin/python" -m memoryframe status

echo "== Audit and disable existing autostart entries"
"$APP_DIR/deploy/audit-autostart.sh" --disable

if [ "$EXTRA_HOSTS_SET" = 1 ]; then
    echo "== Extra allowed hosts (MF-13)"
    "$APP_DIR/deploy/env-file.sh" MEMORYFRAME_EXTRA_HOSTS "$EXTRA_HOSTS"
fi

echo "== systemd service"
sed -e "s#@USER@#$APP_USER#g" -e "s#@APP_DIR@#$APP_DIR#g" \
    "$APP_DIR/deploy/memoryframe.service" | sudo tee /etc/systemd/system/memoryframe.service >/dev/null
# MF-36/MF-18: root-owned copy of the network helper (the checkout is writable by the app user).
sudo install -o root -g root -m 0755 "$APP_DIR/deploy/netmode.sh" /usr/local/sbin/memoryframe-netmode
# Root-owned hotspot.sh + config for the memoryframe-reset boot path (root never runs checkout files).
sudo install -o root -g root -m 0755 "$APP_DIR/deploy/hotspot.sh" /usr/local/sbin/memoryframe-hotspot
if [ -f "$APP_DIR/deploy/hotspot.conf" ]; then
    sudo install -d -o root -g root -m 0755 /etc/memoryframe
    sudo install -o root -g root -m 0600 "$APP_DIR/deploy/hotspot.conf" /etc/memoryframe/hotspot.conf
fi
sed -e "s#@APP_DIR@#$APP_DIR#g" \
    "$APP_DIR/deploy/memoryframe-netmode.service" | sudo tee /etc/systemd/system/memoryframe-netmode.service >/dev/null
SUDOERS_TMP="$(mktemp)"
sed -e "s#@USER@#$APP_USER#g" "$APP_DIR/deploy/memoryframe-sudoers" > "$SUDOERS_TMP"
if sudo visudo -cf "$SUDOERS_TMP" >/dev/null; then
    sudo install -o root -g root -m 0440 "$SUDOERS_TMP" /etc/sudoers.d/memoryframe
else
    echo "WARNING: sudoers rule failed visudo -c; Frame Wi-Fi changes will be unavailable" >&2
fi
rm -f "$SUDOERS_TMP"
sudo systemctl daemon-reload
# MF-51 deploy guard: boot now defaults to product (hotspot). A Pi that is on a home Wi-Fi
# client connection and has no mode file is a dev Pi: write "dev" so its next reboot keeps
# home Wi-Fi (and Pi Connect). An existing mode file is never changed.
MODE_FILE="${MEMORYFRAME_BOOT_DIR:-/boot/firmware}/memoryframe-mode.txt"
CUR_WIFI="$("${NMCLI:-nmcli}" -g GENERAL.CONNECTION device show "${MEMORYFRAME_IFACE:-wlan0}" 2>/dev/null || true)"
if [ ! -e "$MODE_FILE" ] && [ -n "$CUR_WIFI" ] && [ "$CUR_WIFI" != "MemoryFrame-hotspot" ]; then
    echo "MF-51: on home Wi-Fi ($CUR_WIFI) with no mode file: writing 'dev' to $MODE_FILE so reboots keep home Wi-Fi"
    printf 'dev\n' | sudo tee "$MODE_FILE" >/dev/null
fi
# MF-36: applied at the NEXT boot (enabling does not switch the network now).
sudo systemctl enable memoryframe-netmode.service
# MF-49: narrow sudoers rule for the frame's "Shut down" menu item (only systemctl poweroff).
sed -e "s#@USER@#$APP_USER#g" "$APP_DIR/deploy/memoryframe-poweroff-sudoers" > "/tmp/memoryframe-poweroff.$$"
if sudo visudo -cf "/tmp/memoryframe-poweroff.$$" >/dev/null; then
    sudo install -o root -g root -m 0440 "/tmp/memoryframe-poweroff.$$" /etc/sudoers.d/memoryframe-poweroff
else
    echo "WARNING: poweroff sudoers rule failed visudo; Shut down from the menu will not work." >&2
fi
rm -f "/tmp/memoryframe-poweroff.$$"
sudo systemctl enable memoryframe.service
sudo systemctl restart memoryframe.service

echo "== Case fan (gpio-fan overlay, GPIO 14, on at 60 C)"
if ! sudo env MEMORYFRAME_BOOT_CONFIG="${MEMORYFRAME_BOOT_CONFIG:-}" bash "$APP_DIR/deploy/fan-overlay.sh"; then
    echo "WARNING: fan overlay not added" >&2
fi

echo "== Shutdown screen (late hook writes 'Safe to unplug' to fb0)"
sudo install -d -m 0755 /usr/lib/memoryframe /usr/lib/systemd/system-shutdown
sudo install -m 0644 "$APP_DIR/deploy/safe-screen/safe-to-unplug.xrgb8888" \
    "$APP_DIR/deploy/safe-screen/safe-to-unplug.rgb565" /usr/lib/memoryframe/
sudo install -m 0755 "$APP_DIR/deploy/safe-screen/memoryframe-safe-screen" /usr/lib/systemd/system-shutdown/

echo "== Kiosk autostart"
chmod +x "$APP_DIR/deploy/kiosk.sh"
mkdir -p "$HOME/.config/autostart"
sed -e "s#@APP_DIR@#$APP_DIR#g" "$APP_DIR/deploy/memoryframe-kiosk.desktop" \
    > "$HOME/.config/autostart/memoryframe-kiosk.desktop"

echo "== Session type: ${XDG_SESSION_TYPE:-unknown} (record this in the MF-04 PR)"
echo "Done. Reboot to verify: sudo reboot"
