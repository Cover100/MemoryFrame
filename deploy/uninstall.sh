#!/usr/bin/env bash
# Roll back deploy/install.sh. Disabled entries found by the audit keep their
# *.disabled-by-memoryframe.* / *.bak-memoryframe.* backups; restore manually.
set -u
sudo systemctl disable --now memoryframe.service 2>/dev/null || true
sudo rm -f /etc/systemd/system/memoryframe.service
sudo systemctl disable memoryframe-netmode.service 2>/dev/null || true
sudo rm -f /etc/systemd/system/memoryframe-netmode.service /etc/sudoers.d/memoryframe /usr/local/sbin/memoryframe-netmode
sudo rm -f /etc/systemd/system/memoryframe-devtemp.service /etc/systemd/system/memoryframe-wifi-apply.service \
    /etc/systemd/system/memoryframe-wifi-commit.service /etc/systemd/system/memoryframe-wifi-rollback.service \
    /etc/polkit-1/rules.d/50-memoryframe.rules
sudo systemctl daemon-reload
rm -f "$HOME/.config/autostart/memoryframe-kiosk.desktop"
pkill -f deploy/kiosk.sh || true
echo "Memory Frame service and kiosk autostart removed."
sudo rm -f /etc/sudoers.d/memoryframe-poweroff
sudo rm -f /usr/lib/systemd/system-shutdown/memoryframe-safe-screen
sudo rm -rf /usr/lib/memoryframe
