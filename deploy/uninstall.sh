#!/usr/bin/env bash
# Roll back deploy/install.sh. Disabled entries found by the audit keep their
# *.disabled-by-memoryframe.* / *.bak-memoryframe.* backups; restore manually.
set -u
sudo systemctl disable --now memoryframe.service 2>/dev/null || true
sudo rm -f /etc/systemd/system/memoryframe.service
sudo systemctl disable memoryframe-netmode.service 2>/dev/null || true
sudo rm -f /etc/systemd/system/memoryframe-netmode.service /etc/sudoers.d/memoryframe /usr/local/sbin/memoryframe-netmode /usr/local/sbin/memoryframe-hotspot
sudo systemctl disable memoryframe-factory-reset.service 2>/dev/null || true
sudo rm -f /etc/systemd/system/memoryframe-factory-reset.service /usr/local/sbin/memoryframe-factory-reset
sudo rm -f /etc/systemd/system/memoryframe-devtemp.service /etc/systemd/system/memoryframe-wifi-apply.service \
    /etc/systemd/system/memoryframe-wifi-commit.service /etc/systemd/system/memoryframe-wifi-rollback.service \
    /etc/systemd/system/memoryframe-rollback.service /etc/polkit-1/rules.d/50-memoryframe.rules
# #69: helpers. /opt/memoryframe, the mfapp user, its data and /etc/memoryframe/rollback/ are kept (photos, rollback).
sudo systemctl disable memoryframe-rollback.service 2>/dev/null || true
sudo rm -f /usr/local/sbin/memoryframe-migrate /usr/local/sbin/memoryframe-cli
sudo rm -f /etc/memoryframe/hotspot.conf   # holds the hotspot password
# #162: the quiet-boot tokens and config.txt line we added (exactly those; recorded in /etc/memoryframe/
# boot-splash.state), the Plymouth theme (previous theme back, initramfs rebuilt), the desktop flag, and the E4 boot watch. Boot text and the stock desktop are back after a reboot.
sudo bash "$(dirname "$0")/boot-splash.sh" off || echo "WARNING: run 'sudo deploy/boot-splash.sh off' by hand"
# (`off` also removes the boot handover: the lightdm drop-in, the root copy of the handoff script and the Chromium policy file.)
sudo bash "$(dirname "$0")/boot-splash.sh" desktop off || true
sudo systemctl disable memoryframe-bootwatch.service 2>/dev/null || true
sudo rm -f /etc/systemd/system/memoryframe-bootwatch.service /usr/local/sbin/memoryframe-bootwatch
sudo rmdir /etc/memoryframe 2>/dev/null || true
sudo systemctl daemon-reload
rm -f "$HOME/.config/autostart/memoryframe-kiosk.desktop"
# #155: the marked labwc hide-pointer keybind (everything else in rc.xml is kept), then reload labwc.
LABWC_RC="${XDG_CONFIG_HOME:-$HOME/.config}/labwc/rc.xml"
if [ -f "$LABWC_RC" ] && grep -qF "memoryframe-managed hide-cursor" "$LABWC_RC"; then
    python3 "$(dirname "$0")/labwc-hide-cursor.py" remove "$LABWC_RC" || echo "WARNING: remove the hide-cursor block from $LABWC_RC by hand"
    LABWC_PS="$(pgrep -a -u "$(id -u)" -x labwc 2>/dev/null || true)"
    [ -n "$LABWC_PS" ] && { LABWC_PID="${LABWC_PS%% *}" timeout 10 labwc --reconfigure || true; }
fi
# #162: the marked session-start block in labwc's user autostart (the file goes if it was ours and is otherwise
# empty), and the Pi-Apps updater entry back where it was (byte-identical; only if the live name is free).
python3 "$(dirname "$0")/labwc-autostart.py" remove "${XDG_CONFIG_HOME:-$HOME/.config}/labwc/autostart" \
    || echo "WARNING: remove the memoryframe-managed lines from ~/.config/labwc/autostart by hand"
bash "$(dirname "$0")/boot-splash.sh" pi-apps-updater on || true
pkill -f deploy/kiosk.sh || true
echo "Memory Frame service and kiosk autostart removed. (swaybg stays installed: sudo apt-get remove swaybg)"
sudo rm -f /etc/sudoers.d/memoryframe-poweroff
sudo rm -f /usr/lib/systemd/system-shutdown/memoryframe-safe-screen
sudo rm -rf /usr/lib/memoryframe
