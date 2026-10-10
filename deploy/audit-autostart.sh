#!/usr/bin/env bash
# Audit every common autostart location for anything that starts
# Memory Frame (app.py / Flask) or Chromium, so that after a reboot exactly
# one app instance and one browser run.
#
#   deploy/audit-autostart.sh            # report only
#   deploy/audit-autostart.sh --disable  # also disable what it finds (backups kept)
#
# Ignores the entries installed by deploy/install.sh and any line marked
# "memoryframe-managed".
#
# Matching is deliberately narrow and CASE-SENSITIVE: only commands that launch
# the Memory Frame app or a kiosk browser are flagged. A file merely containing
# the home directory (e.g. /home/memoryframe/...) is NOT a match. For .desktop
# files only the Exec= line is checked.
#
# Test hooks (used by tests/test_audit_autostart.py):
#   AUDIT_HOME  home directory to scan     AUDIT_ROOT  prefix for /etc paths
#   SUDO        privilege command (""=none) CRONTAB     crontab command
set -u
DISABLE=0
[ "${1:-}" = "--disable" ] && DISABLE=1
TARGET_USER="${SUDO_USER:-$USER}"
HOME_DIR="${AUDIT_HOME:-$(getent passwd "$TARGET_USER" | cut -d: -f6)}"
ROOT="${AUDIT_ROOT:-}"
SUDO="${SUDO-sudo}"
CRONTAB="${CRONTAB:-crontab}"
# What counts as "launches our app or a kiosk":
#   .../MemoryFrame/app.py, or cd .../MemoryFrame && python app.py
#   http://localhost:5000 / 127.0.0.1:5000
#   chromium / chromium-browser with --kiosk
#   our own deploy/kiosk.sh (a duplicate launcher)
PATTERN='MemoryFrame/app\.py|MemoryFrame[^;&|]*(;|&&)[^;&|]*app\.py|localhost:5000|127\.0\.0\.1:5000|chromium(-browser)?[^#]*--kiosk|deploy/kiosk\.sh'
IGNORE='memoryframe-kiosk|memoryframe-managed'
OURS='memoryframe\.service$|memoryframe-kiosk\.desktop$'
STAMP="$(date +%Y%m%d%H%M%S)"
FOUND=0

note() { echo "  $*"; }
hit() { FOUND=1; echo "FOUND: $*"; }

backup_disable() {  # $1 = file
    if [ "$DISABLE" = 1 ]; then
        $SUDO mv "$1" "$1.disabled-by-memoryframe.$STAMP" && note "disabled -> $1.disabled-by-memoryframe.$STAMP"
    fi
}

echo "== XDG / desktop autostart files"
for f in "$HOME_DIR"/.config/autostart/*.desktop "$ROOT"/etc/xdg/autostart/*.desktop; do
    [ -f "$f" ] || continue
    [[ "$f" =~ $OURS ]] && continue
    if grep -E '^[[:space:]]*Exec=' "$f" | grep -v -E "$IGNORE" | grep -Eq "$PATTERN"; then hit "$f"; backup_disable "$f"; fi
done

echo "== Session autostart scripts (LXDE / labwc / wayfire)"
for f in "$HOME_DIR"/.config/lxsession/*/autostart "$ROOT"/etc/xdg/lxsession/*/autostart \
         "$HOME_DIR"/.config/labwc/autostart "$ROOT"/etc/xdg/labwc/autostart \
         "$HOME_DIR"/.config/wayfire.ini "$HOME_DIR"/.xinitrc "$HOME_DIR"/.xsessionrc \
         "$HOME_DIR"/.profile "$HOME_DIR"/.bash_profile "$HOME_DIR"/.bashrc; do
    [ -f "$f" ] || continue
    if grep -En "$PATTERN" "$f" | grep -v -E "$IGNORE"; then
        hit "$f (lines above)"
        if [ "$DISABLE" = 1 ]; then
            $SUDO cp -p "$f" "$f.bak-memoryframe.$STAMP"
            # comment out matching lines, keep everything else
            $SUDO sed -i -E "/$IGNORE/!{\%($PATTERN)% s/^/# disabled by memoryframe $STAMP: /}" "$f"
            note "matching lines commented out (backup $f.bak-memoryframe.$STAMP)"
        fi
    fi
done

echo "== crontab @reboot"
for u in "$TARGET_USER" root; do
    if $SUDO "$CRONTAB" -l -u "$u" 2>/dev/null | grep -v -E "$IGNORE" | grep -E "$PATTERN"; then
        hit "crontab of $u (lines above)"
        if [ "$DISABLE" = 1 ]; then
            BK="$HOME_DIR/crontab-$u.bak-memoryframe.$STAMP"
            $SUDO "$CRONTAB" -l -u "$u" > "$BK"
            $SUDO "$CRONTAB" -l -u "$u" | sed -E "/$IGNORE/!{\%($PATTERN)% s/^/# disabled by memoryframe: /}" | $SUDO "$CRONTAB" -u "$u" -
            note "commented out (backup $BK)"
        fi
    fi
done
for f in "$ROOT"/etc/crontab "$ROOT"/etc/cron.d/*; do
    [ -f "$f" ] || continue
    if grep -v -E "$IGNORE" "$f" | grep -Eq "$PATTERN"; then hit "$f (edit manually)"; fi
done

echo "== /etc/rc.local"
RCLOCAL="$ROOT/etc/rc.local"
if [ -f "$RCLOCAL" ] && grep -En "$PATTERN" "$RCLOCAL" | grep -v -E "$IGNORE"; then
    hit "/etc/rc.local (lines above)"
    if [ "$DISABLE" = 1 ]; then
        $SUDO cp -p "$RCLOCAL" "$RCLOCAL.bak-memoryframe.$STAMP"
        $SUDO sed -i -E "/$IGNORE/!{\%($PATTERN)% s/^/# disabled by memoryframe: /}" "$RCLOCAL"
        note "commented out (backup $RCLOCAL.bak-memoryframe.$STAMP)"
    fi
fi

echo "== systemd units (system and user)"
for f in "$ROOT"/etc/systemd/system/*.service "$HOME_DIR"/.config/systemd/user/*.service; do
    [ -f "$f" ] || continue
    [[ "$f" =~ $OURS ]] && continue
    if grep -E '^[[:space:]]*Exec' "$f" | grep -v -E "$IGNORE" | grep -Eq "$PATTERN"; then
        hit "$f"
        if [ "$DISABLE" = 1 ]; then
            unit="$(basename "$f")"
            if [[ "$f" == "$HOME_DIR"/* ]]; then
                $SUDO -u "$TARGET_USER" XDG_RUNTIME_DIR="/run/user/$(id -u "$TARGET_USER")" systemctl --user disable --now "$unit" || true
            else
                $SUDO systemctl disable --now "$unit" || true
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
