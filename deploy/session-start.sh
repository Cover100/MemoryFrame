#!/usr/bin/env bash
# #162 S3: the first thing of the desktop session. Started by the marked block in ~/.config/labwc/autostart
# (deploy/labwc-autostart.py, added by install.sh): `.../deploy/session-start.sh &`.
#  1. starts swaybg with Wren's still splash (static/boot/splash-A.png, byte-for-byte) centred on ink #0E1311, FIRST;
#  2. presses the #155 hide-pointer keybind (Alt+Super+H, wtype), after the splash so a slow wtype delays nothing visible;
#  3. for 20 s, every 0.3 s, stops the stock desktop and panel: the lwrespawn wrappers first (so they can't restart
#     them), then pcmanfm-pi / pcmanfm / wf-panel-pi. Only this user's processes with exactly those names; never
#     Chromium, kiosk.sh or labwc.
# Touches /run/mfkiosk/session-up first (memoryframe-bootwatch.service: no E4 once the session is up).
# Does nothing else with /boot/firmware/memoryframe-desktop.txt present (the stock desktop; deploy/boot-splash.sh
# desktop on) or outside a Wayland session. Logs each step with /proc/uptime to /run/mfkiosk/memoryframe-session.log.
# Test hooks: MEMORYFRAME_BOOT_DIR, MEMORYFRAME_KIOSK_TMPFS, MEMORYFRAME_SESSION_LOG, MEMORYFRAME_UPTIME_FILE,
#             MEMORYFRAME_BOOT_ASSETS, MEMORYFRAME_SESSION_KILL_FOR (seconds, default 20).
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
TMPFS="${MEMORYFRAME_KIOSK_TMPFS:-/run/mfkiosk}"
FLAG="${MEMORYFRAME_BOOT_DIR:-/boot/firmware}/memoryframe-desktop.txt"
ASSETS="${MEMORYFRAME_BOOT_ASSETS:-$HERE/../static/boot}"
PNG="$ASSETS/splash-A.png"
KILL_FOR="${MEMORYFRAME_SESSION_KILL_FOR:-20}"
SWAYBG_PIDFILE="${XDG_RUNTIME_DIR:-$TMPFS}/memoryframe-swaybg.pid"      # shared with kiosk.sh
if [ -n "${MEMORYFRAME_SESSION_LOG:-}" ]; then LOG="$MEMORYFRAME_SESSION_LOG"
elif [ -d "$TMPFS" ] && [ -w "$TMPFS" ]; then LOG="$TMPFS/memoryframe-session.log"
else LOG="$HOME/.cache/memoryframe-session.log"; mkdir -p "$HOME/.cache"; fi
exec >>"$LOG" 2>&1 </dev/null
upt() { local u _r; read -r u _r < "${MEMORYFRAME_UPTIME_FILE:-/proc/uptime}" 2>/dev/null || u="?"; printf '%s' "$u"; }
log() { echo "$(date '+%Y-%m-%d %H:%M:%S') memoryframe-session: $* (uptime $(upt))"; }

log "start"
if [ -d "$TMPFS" ] && [ -w "$TMPFS" ]; then : > "$TMPFS/session-up"; fi
if [ -e "$FLAG" ]; then
    log "desktop-mode flag present ($FLAG): desktop and panel left running, no splash"
    exit 0
fi
if [ -z "${WAYLAND_DISPLAY:-}" ] && [ "${XDG_SESSION_TYPE:-}" != wayland ]; then
    log "not a Wayland session: nothing to do"
    exit 0
fi

# 1. The still splash FIRST (the very first thing painted once labwc is up; nothing waits for it). A plain ink root if swaybg or the image is
#    missing: swaybg is an install prerequisite (install.sh); without it the screen stays plain until Chromium paints.
if ! command -v swaybg >/dev/null 2>&1; then
    log "swaybg: not installed: plain background until Chromium paints (sudo apt-get install swaybg)"
elif [ ! -f "$PNG" ]; then
    log "swaybg: $PNG missing: plain background until Chromium paints"
else
    swaybg -c '#0E1311' -i "$PNG" -m center >/dev/null 2>&1 &
    printf '%s\n' "$!" > "$SWAYBG_PIDFILE" 2>/dev/null
    log "swaybg: splash shown (pid $!)"
fi

# 2. #155 pointer (labwc draws it from session start). After the splash, so a slow wtype delays nothing visible.
if command -v wtype >/dev/null 2>&1; then
    if command -v timeout >/dev/null 2>&1; then timeout 5 wtype -M alt -M logo h -m alt -m logo; rc=$?
    else wtype -M alt -M logo h -m alt -m logo; rc=$?; fi
    if [ "$rc" = 0 ]; then log "hide-cursor: pointer hidden"; else log "hide-cursor: wtype failed with status $rc"; fi
else
    log "hide-cursor: skipped (wtype not installed)"
fi

# 3. Stop the desktop and the panel; the respawn wrappers first. pkill -u: this user only; -x: exact names.
me="$(id -u)"
WRAPPERS='lwrespawn( |.* )(/usr/bin/)?(pcmanfm-pi|pcmanfm|wf-panel-pi)( |$)'
seen=""
n=$(( KILL_FOR * 10 / 3 ))
i=0
while [ "$i" -lt "$n" ]; do
    if pkill -u "$me" -f -- "$WRAPPERS"; then
        case "$seen" in *w*) ;; *) seen="${seen}w"; log "stopped the lwrespawn wrappers" ;; esac
    fi
    for p in pcmanfm-pi pcmanfm wf-panel-pi; do
        if pkill -u "$me" -x -- "$p"; then
            case " $seen " in *" $p "*) ;; *) seen="$seen $p "; log "stopped $p" ;; esac
        fi
    done
    i=$((i + 1))
    sleep 0.3
done
log "done"
