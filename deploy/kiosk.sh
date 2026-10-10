#!/usr/bin/env bash
# Memory Frame kiosk launcher. Started once per desktop login by the XDG
# autostart entry (deploy/memoryframe-kiosk.desktop).
#  - waits until the app answers /healthz (no "connection refused" page)
#  - runs exactly one Chromium kiosk, restarting it if it exits
#  - uses a dedicated Chromium profile and never touches the OS keyring
#  - logs to ~/.cache/memoryframe/kiosk.log (launcher + Chromium stderr)
#
# Environment overrides:
#   MEMORYFRAME_URL             default http://localhost:5000
#   MEMORYFRAME_CHROMIUM_FLAGS  extra flags, e.g. "--ozone-platform=wayland" or "--disable-gpu"
#   MEMORYFRAME_STATE_DIR       default ~/.cache/memoryframe
set -u

URL="${MEMORYFRAME_URL:-http://localhost:5000}"
HEALTH="$URL/healthz"
STATE_DIR="${MEMORYFRAME_STATE_DIR:-$HOME/.cache/memoryframe}"
PROFILE_DIR="$STATE_DIR/chromium-profile"
LOG="$STATE_DIR/kiosk.log"
LOCK="${XDG_RUNTIME_DIR:-/tmp}/memoryframe-kiosk.lock"

mkdir -p "$STATE_DIR" "$PROFILE_DIR"

# Keep the log small: rotate once it passes 1 MB.
if [ -f "$LOG" ] && [ "$(stat -c %s "$LOG" 2>/dev/null || echo 0)" -gt 1048576 ]; then
    mv -f "$LOG" "$LOG.1"
fi
# Everything below (including Chromium's stderr) goes to the log.
exec >>"$LOG" 2>&1

log() { echo "$(date '+%Y-%m-%d %H:%M:%S') memoryframe-kiosk: $*"; }

# Single instance of this launcher.
exec 9>"$LOCK"
if ! flock -n 9; then
    log "already running, exiting"
    exit 0
fi

# MEMORYFRAME_BROWSER overrides detection (absolute path or command name).
BROWSER="${MEMORYFRAME_BROWSER:-}"
if [ -n "$BROWSER" ] && ! command -v "$BROWSER" >/dev/null 2>&1; then
    log "MEMORYFRAME_BROWSER=$BROWSER not found"
    exit 1
fi
if [ -z "$BROWSER" ]; then
    for b in chromium-browser chromium; do
        if command -v "$b" >/dev/null 2>&1; then BROWSER="$b"; break; fi
    done
fi
if [ -z "$BROWSER" ]; then
    log "chromium not found"
    exit 1
fi

log "start: browser=$BROWSER url=$URL session=${XDG_SESSION_TYPE:-?} wayland=${WAYLAND_DISPLAY:-} display=${DISPLAY:-}"

wait_healthy() {
    local n=0
    until curl -fsS --max-time 2 "$HEALTH" >/dev/null 2>&1; do
        n=$((n + 1))
        [ $((n % 30)) -eq 1 ] && log "waiting for $HEALTH"
        sleep 1
    done
    log "server healthy; /media has $(curl -fsS --max-time 2 "$URL/media" 2>/dev/null | grep -o '"filename"' | wc -l) item(s)"
}

# After a crash/kill -9 Chromium marks the profile as not cleanly exited and
# may show a "Restore pages?" bubble or session restore. Reset those flags.
mark_clean_exit() {
    local prefs="$PROFILE_DIR/Default/Preferences"
    [ -f "$prefs" ] || return 0
    sed -i -e 's/"exited_cleanly":false/"exited_cleanly":true/g' \
           -e 's/"exit_type":"[A-Za-z]*"/"exit_type":"Normal"/g' "$prefs"
}

# Don't let the screen blank (X11 only; harmless elsewhere).
if [ -n "${DISPLAY:-}" ] && command -v xset >/dev/null 2>&1; then
    xset s off -dpms s noblank 2>/dev/null || true
fi

while true; do
    wait_healthy
    mark_clean_exit
    rm -f "$PROFILE_DIR"/Singleton{Lock,Socket,Cookie}
    # shellcheck disable=SC2086
    "$BROWSER" \
        --kiosk \
        --user-data-dir="$PROFILE_DIR" \
        --password-store=basic \
        --no-first-run \
        --no-default-browser-check \
        --noerrdialogs \
        --disable-infobars \
        --disable-session-crashed-bubble \
        --hide-crash-restore-bubble \
        --disable-features=Translate \
        --autoplay-policy=no-user-gesture-required \
        --ozone-platform-hint=auto \
        --check-for-update-interval=31536000 \
        ${MEMORYFRAME_CHROMIUM_FLAGS:-} \
        "$URL"
    log "browser exited with status $?, restarting in 2 s"
    sleep 2
done
