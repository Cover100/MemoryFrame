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
sed -e "s#@APP_DIR@#$APP_DIR#g" \
    "$APP_DIR/deploy/memoryframe-netmode.service" | sudo tee /etc/systemd/system/memoryframe-netmode.service >/dev/null
chmod +x "$APP_DIR/deploy/netmode.sh"
sudo systemctl daemon-reload
# MF-36: applied at the NEXT boot (enabling does not switch the network now).
sudo systemctl enable memoryframe-netmode.service
sudo systemctl enable memoryframe.service
sudo systemctl restart memoryframe.service

echo "== Kiosk autostart"
chmod +x "$APP_DIR/deploy/kiosk.sh"
mkdir -p "$HOME/.config/autostart"
sed -e "s#@APP_DIR@#$APP_DIR#g" "$APP_DIR/deploy/memoryframe-kiosk.desktop" \
    > "$HOME/.config/autostart/memoryframe-kiosk.desktop"

echo "== Session type: ${XDG_SESSION_TYPE:-unknown} (record this in the MF-04 PR)"
echo "Done. Reboot to verify: sudo reboot"
