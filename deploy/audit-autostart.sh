#!/usr/bin/env bash
# Audit every common autostart location for anything that starts
# Memory Frame (app.py / Flask) or Chromium, so that after a reboot exactly
# one app instance and one browser run.
#
#   deploy/audit-autostart.sh            # report only
#   deploy/audit-autostart.sh --disable  # also disable what it finds (backups kept)
#
# Ignores the entries installed by deploy/install.sh.
set -u
DISABLE=0
[ "${1:-}" = "--disable" ] && DISABLE=1
TARGET_USER="${SUDO_USER:-$USER}"
HOME_DIR="$(getent passwd "$TARGET_USER" | cut -d: -f6)"
PATTERN='app\.py|flask|chromium|memoryframe|MemoryFrame|localhost:5000'
OURS='memoryframe\.service$|memoryframe-kiosk\.desktop$'
STAMP="$(date +%Y%m%d%H%M%S)"
FOUND=0

note() { echo "  $*"; }
hit() { FOUND=1; echo "FOUND: $*"; }

backup_disable() {  # $1 = file
    if [ "$DISABLE" = 1 ]; then
        sudo mv "$1" "$1.disabled-by-memoryframe.$STAMP" && note "disabled -> $1.disabled-by-memoryframe.$STAMP"
    fi
}

echo "== XDG / desktop autostart files"
for f in "$HOME_DIR"/.config/autostart/*.desktop /etc/xdg/autostart/*.desktop; do
    [ -f "$f" ] || continue
    [[ "$f" =~ $OURS ]] && continue
    if grep -Eqi "$PATTERN" "$f"; then hit "$f"; backup_disable "$f"; fi
done

echo "== Session autostart scripts (LXDE / labwc / wayfire)"
for f in "$HOME_DIR"/.config/lxsession/*/autostart /etc/xdg/lxsession/*/autostart \
         "$HOME_DIR"/.config/labwc/autostart /etc/xdg/labwc/autostart \
         "$HOME_DIR"/.config/wayfire.ini "$HOME_DIR"/.xinitrc "$HOME_DIR"/.xsessionrc \
         "$HOME_DIR"/.profile "$HOME_DIR"/.bash_profile "$HOME_DIR"/.bashrc; do
    [ -f "$f" ] || continue
    if grep -Eni "$PATTERN" "$f" | grep -v memoryframe-kiosk; then
        hit "$f (lines above)"
        if [ "$DISABLE" = 1 ]; then
            sudo cp -p "$f" "$f.bak-memoryframe.$STAMP"
            # comment out matching lines, keep everything else
            sudo sed -i -E "/memoryframe-kiosk/!{/($PATTERN)/I s/^/# disabled by memoryframe $STAMP: /}" "$f"
            note "matching lines commented out (backup $f.bak-memoryframe.$STAMP)"
        fi
    fi
done

echo "== crontab @reboot"
for u in "$TARGET_USER" root; do
    if sudo crontab -l -u "$u" 2>/dev/null | grep -Ei "$PATTERN"; then
        hit "crontab of $u (lines above)"
        if [ "$DISABLE" = 1 ]; then
            sudo crontab -l -u "$u" > "/tmp/crontab-$u.bak-memoryframe.$STAMP"
            sudo crontab -l -u "$u" | sed -E "/($PATTERN)/I s/^/# disabled by memoryframe: /" | sudo crontab -u "$u" -
            note "commented out (backup /tmp/crontab-$u.bak-memoryframe.$STAMP)"
        fi
    fi
done
for f in /etc/crontab /etc/cron.d/*; do
    [ -f "$f" ] || continue
    if grep -Eqi "$PATTERN" "$f"; then hit "$f (edit manually)"; fi
done

echo "== /etc/rc.local"
if [ -f /etc/rc.local ] && grep -Eni "$PATTERN" /etc/rc.local; then
    hit "/etc/rc.local (lines above)"
    if [ "$DISABLE" = 1 ]; then
        sudo cp -p /etc/rc.local "/etc/rc.local.bak-memoryframe.$STAMP"
        sudo sed -i -E "/($PATTERN)/I s/^/# disabled by memoryframe: /" /etc/rc.local
        note "commented out (backup /etc/rc.local.bak-memoryframe.$STAMP)"
    fi
fi

echo "== systemd units (system and user)"
for f in /etc/systemd/system/*.service "$HOME_DIR"/.config/systemd/user/*.service; do
    [ -f "$f" ] || continue
    [[ "$f" =~ $OURS ]] && continue
    if grep -Eqi "$PATTERN" "$f"; then
        hit "$f"
        if [ "$DISABLE" = 1 ]; then
            unit="$(basename "$f")"
            if [[ "$f" == "$HOME_DIR"/* ]]; then
                sudo -u "$TARGET_USER" XDG_RUNTIME_DIR="/run/user/$(id -u "$TARGET_USER")" systemctl --user disable --now "$unit" || true
            else
                sudo systemctl disable --now "$unit" || true
            fi
            note "disabled $unit"
        fi
    fi
done

echo
if [ "$FOUND" = 0 ]; then
    echo "No other autostart entries for Memory Frame or Chromium found."
elif [ "$DISABLE" = 0 ]; then
    echo "Re-run with --disable to disable the entries above."
fi
