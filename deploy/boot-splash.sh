#!/usr/bin/env bash
# #162 boot splash: the parts outside the app and the session. Idempotent; every change is undone by `off` /
# `pi-apps-updater on` (deploy/uninstall.sh runs both). Takes effect at the next boot; never reboots.
#
#   sudo deploy/boot-splash.sh on        quiet boot: cmdline.txt + quiet logo.nologo vt.global_cursor_default=0
#                                        loglevel=3 (only the missing ones), config.txt disable_splash=1 in [all]
#   sudo deploy/boot-splash.sh off       removes exactly what `on` added (recorded in /etc/memoryframe/boot-splash.state):
#                                        boot text is back after the next reboot
#        deploy/boot-splash.sh status
#   sudo deploy/boot-splash.sh desktop on|off
#                                        the developer's way out: memoryframe-desktop.txt on the boot partition makes
#                                        session-start.sh and kiosk.sh do nothing (stock desktop) from the next login
#        deploy/boot-splash.sh pi-apps-updater off|on    (as the desktop user)
#                                        moves ~/.config/autostart/pi-apps-updater.desktop aside (only if its Exec=
#                                        runs pi-apps/updater) as *.disabled-by-memoryframe.<ts>, or moves it back.
#                                        MEMORYFRAME_KEEP_PI_APPS_UPDATER=1 makes `off` do nothing.
#
# The backups: cmdline.txt.memoryframe-bak.<ts> and config.txt.memoryframe-bak.<ts> next to the files. From a PC
# (boot partition is FAT): delete "quiet logo.nologo vt.global_cursor_default=0 loglevel=3" from cmdline.txt to see
# boot text again. Never reads or writes memoryframe-mode.txt or any network profile.
# Test hooks: MEMORYFRAME_BOOT_DIR (default /boot/firmware), MEMORYFRAME_BOOT_STATE, HOME / XDG_CONFIG_HOME.
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
BOOT_DIR="${MEMORYFRAME_BOOT_DIR:-/boot/firmware}"
STATE="${MEMORYFRAME_BOOT_STATE:-/etc/memoryframe/boot-splash.state}"
PY3=/usr/bin/python3; [ -x "$PY3" ] || PY3="$(command -v python3 || echo python3)"
EDIT=("$PY3" "$HERE/boot-splash-edit.py")
FLAG="$BOOT_DIR/memoryframe-desktop.txt"
CONFIG_HOME="${XDG_CONFIG_HOME:-$HOME/.config}"
UPDATER="$CONFIG_HOME/autostart/pi-apps-updater.desktop"
USER_STATE="$CONFIG_HOME/memoryframe/boot-splash.state"     # the moved Pi-Apps file names (desktop user's)
STAMP="$(date +%Y%m%d%H%M%S)"

usage() { sed -n '2,24p' "$0" >&2; exit 2; }

need_root() {
    if [ "$(id -u)" != 0 ] && [ -z "${MEMORYFRAME_BOOT_DIR:-}" ]; then
        echo "boot-splash: run with sudo: $0 $*" >&2; exit 1
    fi
}

pi_apps_off() {
    if [ "${MEMORYFRAME_KEEP_PI_APPS_UPDATER:-}" = 1 ]; then
        echo "boot-splash: Pi-Apps updater kept (MEMORYFRAME_KEEP_PI_APPS_UPDATER=1)"; return 0
    fi
    if [ ! -f "$UPDATER" ] || [ -L "$UPDATER" ]; then
        echo "boot-splash: no Pi-Apps updater autostart entry"; return 0
    fi
    if ! grep -Eq '^[[:space:]]*Exec[[:space:]]*=.*pi-apps/updater' "$UPDATER"; then
        echo "boot-splash: $UPDATER does not run pi-apps/updater: left as is"; return 0
    fi
    local dest="$UPDATER.disabled-by-memoryframe.$STAMP"
    mv -n "$UPDATER" "$dest" || { echo "boot-splash: WARNING: could not move $UPDATER aside" >&2; return 0; }
    mkdir -p "$(dirname "$USER_STATE")"
    printf 'pi_apps_moved=%s\n' "${dest##*/}" >> "$USER_STATE"
    echo "boot-splash: Pi-Apps updater no longer starts at login (moved to ${dest##*/}; undo: $0 pi-apps-updater on)"
}

pi_apps_on() {
    [ -f "$USER_STATE" ] || { echo "boot-splash: no Pi-Apps updater entry of ours to restore"; return 0; }
    local name last="" rest
    while IFS= read -r name; do
        case "$name" in pi_apps_moved=pi-apps-updater.desktop.disabled-by-memoryframe.*) last="${name#pi_apps_moved=}" ;; esac
    done < "$USER_STATE"
    if [ -z "$last" ]; then rm -f "$USER_STATE"; echo "boot-splash: no Pi-Apps updater entry of ours to restore"; return 0; fi
    if [ -e "$UPDATER" ]; then
        echo "boot-splash: $UPDATER exists again (Pi-Apps recreated it): kept; our copy stays as $last"
    elif [ -f "$(dirname "$UPDATER")/$last" ]; then
        mv -n "$(dirname "$UPDATER")/$last" "$UPDATER" && echo "boot-splash: Pi-Apps updater restored ($UPDATER)"
    else
        echo "boot-splash: WARNING: $last is gone: nothing to restore" >&2
    fi
    rest="$(grep -v -F "pi_apps_moved=$last" "$USER_STATE" || true)"
    if [ -n "$rest" ]; then printf '%s\n' "$rest" > "$USER_STATE"; else rm -f "$USER_STATE"; fi
    rmdir "$(dirname "$USER_STATE")" 2>/dev/null || true
}

case "${1:-}" in
    on)
        need_root "$@"
        "${EDIT[@]}" cmdline-on "$BOOT_DIR/cmdline.txt" "$STATE"
        "${EDIT[@]}" config-on "$BOOT_DIR/config.txt" "$STATE" ;;
    off)
        need_root "$@"
        "${EDIT[@]}" cmdline-off "$BOOT_DIR/cmdline.txt" "$STATE"
        "${EDIT[@]}" config-off "$BOOT_DIR/config.txt" "$STATE" ;;
    status)
        echo "cmdline.txt: $(cat "$BOOT_DIR/cmdline.txt" 2>/dev/null || echo missing)"
        echo "state: $(tr '\n' ' ' < "$STATE" 2>/dev/null || echo none)"
        if [ -e "$FLAG" ]; then echo "desktop flag: present (stock desktop, no kiosk)"; else echo "desktop flag: absent"; fi
        if [ -f "$UPDATER" ]; then echo "Pi-Apps updater: starts at login"; else echo "Pi-Apps updater: off or not installed"; fi ;;
    desktop)
        case "${2:-}" in
            on)  need_root "$@"
                 if [ -e "$FLAG" ]; then echo "boot-splash: desktop flag already present"
                 else printf 'Memory Frame: delete this file and reboot to return to the frame (#162).\n' > "$FLAG" \
                      && sync && echo "boot-splash: $FLAG created: the stock desktop shows from the next login/reboot"; fi ;;
            off) need_root "$@"
                 if [ -e "$FLAG" ]; then rm -f "$FLAG" && sync && echo "boot-splash: $FLAG removed: the frame starts from the next reboot"
                 else echo "boot-splash: no desktop flag"; fi ;;
            *) usage ;;
        esac ;;
    pi-apps-updater)
        if [ "$(id -u)" = 0 ]; then echo "boot-splash: run pi-apps-updater as the desktop user, not root" >&2; exit 1; fi
        case "${2:-}" in
            off) pi_apps_off ;;
            on)  pi_apps_on ;;
            *) usage ;;
        esac ;;
    *) usage ;;
esac
