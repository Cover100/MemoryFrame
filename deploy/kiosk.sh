#!/usr/bin/env bash
# Memory Frame kiosk launcher. Started once per desktop login by the XDG
# autostart entry (deploy/memoryframe-kiosk.desktop).
#  - waits until the app answers /healthz (no "connection refused" page)
#  - runs exactly one Chromium kiosk, restarting it if it exits
#  - uses a dedicated Chromium profile and never touches the OS keyring
#  - logs to ~/.cache/memoryframe-kiosk.log (launcher + Chromium stdout/stderr, rotated at 1 MB)
#
# Environment overrides:
#   MEMORYFRAME_URL             default http://localhost:5000
#   MEMORYFRAME_CHROMIUM_FLAGS  extra flags, e.g. "--ozone-platform=wayland" or "--disable-gpu"
#   MEMORYFRAME_STATE_DIR       Chromium profile parent, default ~/.cache/memoryframe
#   MEMORYFRAME_KIOSK_LOG       default ~/.cache/memoryframe-kiosk.log
#   MEMORYFRAME_KIOSK_DEBUG=1   adds DevTools on 127.0.0.1:9222 only + verbose Chromium logging
set -u

URL="${MEMORYFRAME_URL:-http://localhost:5000}"
HEALTH="$URL/healthz"
STATE_DIR="${MEMORYFRAME_STATE_DIR:-$HOME/.cache/memoryframe}"
PROFILE_DIR="$STATE_DIR/chromium-profile"
LOG="${MEMORYFRAME_KIOSK_LOG:-$HOME/.cache/memoryframe-kiosk.log}"
LOCK="${XDG_RUNTIME_DIR:-/tmp}/memoryframe-kiosk.lock"

mkdir -p "$STATE_DIR" "$PROFILE_DIR" "$(dirname "$LOG")"

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

# Wayland (labwc on Raspberry Pi OS trixie): ask for the Wayland backend
# explicitly instead of relying only on the auto hint.
PLATFORM_FLAGS=(--ozone-platform-hint=auto)
# labwc on Raspberry Pi OS does not always export WAYLAND_DISPLAY to autostart
# programs, so XDG_SESSION_TYPE=wayland also counts.
if [ -n "${WAYLAND_DISPLAY:-}" ] || [ "${XDG_SESSION_TYPE:-}" = wayland ]; then
    PLATFORM_FLAGS=(--ozone-platform=wayland --enable-features=UseOzonePlatform)
fi

# Opt-in debugging: DevTools bound to localhost only, Chromium logs to stderr (-> log file).
DEBUG_FLAGS=()
if [ "${MEMORYFRAME_KIOSK_DEBUG:-0}" = 1 ]; then
    DEBUG_FLAGS=(--remote-debugging-port=9222 --remote-debugging-address=127.0.0.1
                 --remote-allow-origins=http://127.0.0.1:9222 --enable-logging=stderr --v=0)
    log "DEBUG mode: DevTools on 127.0.0.1:9222"
fi

# Don't let the screen blank (X11 only; harmless elsewhere).
if [ -n "${DISPLAY:-}" ] && command -v xset >/dev/null 2>&1; then
    xset s off -dpms s noblank 2>/dev/null || true
fi

# #95: liveness. The page polls /pairing/state every 2 s; if it stops for LIVE_MAX_AGE s while the app is
# healthy and not shutting down, the frozen Chromium is killed and relaunched. At most LIVE_KILLS kills per
# LIVE_WINDOW s, then it only logs (a page bug that never heartbeats must not loop).
LIVE_EVERY="${MEMORYFRAME_LIVENESS_EVERY:-20}"
LIVE_MAX_AGE="${MEMORYFRAME_LIVENESS_MAX_AGE:-120}"
LIVE_KILLS=3
LIVE_WINDOW=600
KILLS_FILE="${XDG_RUNTIME_DIR:-/tmp}/memoryframe-kiosk.kills"
BACKOFF_BASE="${MEMORYFRAME_KIOSK_BACKOFF:-2}"
BACKOFF_MAX=60

watch_liveness() {  # $1 = browser pid
    local pid="$1" body age now recent
    while kill -0 "$pid" 2>/dev/null; do
        sleep "$LIVE_EVERY"
        body="$(curl -fsS --max-time 5 "$URL/frame/liveness" 2>/dev/null)" || continue
        case "$body" in *'"shutting_down":true'*|*'"shutting_down": true'*) continue ;; esac
        age="$(printf '%s' "$body" | sed -n 's/.*"kiosk_age_s": *\([0-9]*\).*/\1/p')"
        [ -n "$age" ] && [ "$age" -gt "$LIVE_MAX_AGE" ] || continue
        curl -fsS --max-time 5 "$HEALTH" >/dev/null 2>&1 || continue
        now="$(date +%s)"
        recent=0
        if [ -f "$KILLS_FILE" ]; then
            while read -r t; do [ $((now - t)) -lt "$LIVE_WINDOW" ] && recent=$((recent + 1)); done < "$KILLS_FILE"
        fi
        if [ "$recent" -ge "$LIVE_KILLS" ]; then
            log "liveness: no heartbeat for ${age} s, but $recent kills in 10 min; not killing"
            continue
        fi
        echo "$now" >> "$KILLS_FILE"
        log "liveness: no heartbeat for ${age} s; killing browser $pid"
        kill "$pid" 2>/dev/null
        return 0
    done
}

delay="$BACKOFF_BASE"
while true; do
    wait_healthy
    mark_clean_exit
    rm -f "$PROFILE_DIR"/Singleton{Lock,Socket,Cookie}
    # shellcheck disable=SC2086
    set -- --kiosk --user-data-dir="$PROFILE_DIR" --password-store=basic --no-first-run \
        --no-default-browser-check --noerrdialogs --disable-infobars \
        --disable-session-crashed-bubble --hide-crash-restore-bubble \
        --disable-features=Translate --autoplay-policy=no-user-gesture-required \
        --check-for-update-interval=31536000 \
        "${PLATFORM_FLAGS[@]}" "${DEBUG_FLAGS[@]}" ${MEMORYFRAME_CHROMIUM_FLAGS:-} "$URL"
    log "launch: $BROWSER $*"
    started="$(date +%s)"
    "$BROWSER" "$@" 9>&- &
    bpid=$!
    watch_liveness "$bpid" &
    wpid=$!
    wait "$bpid"
    rc=$?
    kill "$wpid" 2>/dev/null
    # #95: crash backoff 2, 4, 8 ... 60 s; reset once the browser ran longer than 60 s.
    [ $(( $(date +%s) - started )) -gt 60 ] && delay="$BACKOFF_BASE"
    log "browser exited with status $rc, restarting in $delay s"
    sleep "$delay"
    delay=$((delay * 2)); [ "$delay" -gt "$BACKOFF_MAX" ] && delay="$BACKOFF_MAX"
done
