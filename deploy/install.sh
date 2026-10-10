#!/usr/bin/env bash
# Install Memory Frame on a Raspberry Pi 4B (Raspberry Pi OS 64-bit, desktop).
# Run from the repo checkout as the desktop user (not root):  ./deploy/install.sh
# Idempotent: safe to re-run after a git pull.
set -euo pipefail

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

echo "== Audit and disable existing autostart entries"
"$APP_DIR/deploy/audit-autostart.sh" --disable

echo "== systemd service"
sed -e "s#@USER@#$APP_USER#g" -e "s#@APP_DIR@#$APP_DIR#g" \
    "$APP_DIR/deploy/memoryframe.service" | sudo tee /etc/systemd/system/memoryframe.service >/dev/null
sudo systemctl daemon-reload
sudo systemctl enable memoryframe.service
sudo systemctl restart memoryframe.service

echo "== Kiosk autostart"
chmod +x "$APP_DIR/deploy/kiosk.sh"
mkdir -p "$HOME/.config/autostart"
sed -e "s#@APP_DIR@#$APP_DIR#g" "$APP_DIR/deploy/memoryframe-kiosk.desktop" \
    > "$HOME/.config/autostart/memoryframe-kiosk.desktop"

echo "== Session type: ${XDG_SESSION_TYPE:-unknown} (record this in the MF-04 PR)"
echo "Done. Reboot to verify: sudo reboot"
