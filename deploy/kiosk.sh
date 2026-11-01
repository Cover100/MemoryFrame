#!/usr/bin/env bash
# Memory Frame kiosk launcher. Started once per desktop login by the XDG
# autostart entry (deploy/memoryframe-kiosk.desktop).
#  - waits until the app answers /healthz (no "connection refused" page)
#  - runs exactly one Chromium kiosk, restarting it if it exits
#  - uses a dedicated Chromium profile and never touches the OS keyring
#  - logs launcher + Chromium stdout/stderr (rotated at 1 MB) to the tmpfs (/run/mfkiosk/memoryframe-kiosk.log, #97),
#    or to ~/.cache/memoryframe-kiosk.log when MEMORYFRAME_STATE_DIR is set or the tmpfs is missing
#
# Environment overrides:
#   MEMORYFRAME_URL             default http://localhost:5000
#   MEMORYFRAME_CHROMIUM_FLAGS  extra flags, e.g. "--ozone-platform=wayland" or "--disable-gpu"
#   MEMORYFRAME_STATE_DIR       Chromium profile + cache parent, default /run/mfkiosk (#97: the RAM-only tmpfs
#                               from run-mfkiosk.mount, 192M, 0700, kiosk user); ~/.cache/memoryframe if it is missing
#   MEMORYFRAME_KIOSK_LOG       default: see above
#   MEMORYFRAME_KIOSK_DEBUG=1   adds DevTools on 127.0.0.1:9222 only + verbose Chromium logging
set -u

URL="${MEMORYFRAME_URL:-http://localhost:5000}"
HEALTH="$URL/healthz"
KIOSK_TMPFS="${MEMORYFRAME_KIOSK_TMPFS:-/run/mfkiosk}"
STATE_FALLBACK=""
if [ -n "${MEMORYFRAME_STATE_DIR:-}" ]; then STATE_DIR="$MEMORYFRAME_STATE_DIR"
elif [ -d "$KIOSK_TMPFS" ] && [ -w "$KIOSK_TMPFS" ]; then STATE_DIR="$KIOSK_TMPFS"
else STATE_DIR="$HOME/.cache/memoryframe"; STATE_FALLBACK=1; fi
PROFILE_DIR="$STATE_DIR/chromium-profile"
CACHE_DIR="$STATE_DIR/cache"
# #97: on the tmpfs the log lives there too (no card writes, journald stays at 0); lost on reboot.
if [ -z "${MEMORYFRAME_STATE_DIR:-}" ] && [ -z "$STATE_FALLBACK" ]; then LOG_DEFAULT="$STATE_DIR/memoryframe-kiosk.log"
else LOG_DEFAULT="$HOME/.cache/memoryframe-kiosk.log"; fi
LOG="${MEMORYFRAME_KIOSK_LOG:-$LOG_DEFAULT}"
# Sage (#113): never /tmp; the user's own state dir if there is no runtime dir.
LOCK="${XDG_RUNTIME_DIR:-$STATE_DIR}/memoryframe-kiosk.lock"
# #113: while a factory reset runs (marker present) the kiosk neither relaunches Chromium nor polls the app.
RESET_MARKER="${MEMORYFRAME_DATA_DIR:-/var/lib/memoryframe}/.factory-reset"
resetting() { [ -e "$RESET_MARKER" ] || [ -L "$RESET_MARKER" ]; }

mkdir -p "$STATE_DIR" "$PROFILE_DIR" "$CACHE_DIR" "$(dirname "$LOG")"

# Keep the log small: rotate once it passes 1 MB (one .1 kept). #96: also checked before every
# browser launch, so a Chromium crash loop can't grow it past about 2 x 1 MB.
rotate_log() {
    if [ -f "$LOG" ] && [ "$(stat -c %s "$LOG" 2>/dev/null || echo 0)" -gt 1048576 ]; then
        mv -f "$LOG" "$LOG.1"
        exec >>"$LOG" 2>&1
    fi
}
rotate_log
# Everything below (including Chromium's stderr) goes to the log.
exec >>"$LOG" 2>&1

log() { echo "$(date '+%Y-%m-%d %H:%M:%S') memoryframe-kiosk: $*"; }

# Single instance of this launcher. Only the launcher itself may hold fd 9: the browser and the liveness
# watcher are started with it closed (9>&-), so a Chromium helper (zygote, GPU, renderer...) that outlives
# the launcher can never keep the lock and block the next launcher (#97, seen on the Pi).
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

# #97: the pre-tmpfs profile on the card (~/.cache/memoryframe/chromium-profile, ~200M of Chromium cache and
# state the frame doesn't use) is removed once we run from the tmpfs. Safe here: this launcher holds the
# single-instance lock, so no Chromium of ours is using it. Only that one directory; nothing else is touched.
LEGACY_PROFILE="$HOME/.cache/memoryframe/chromium-profile"
if [ -z "${MEMORYFRAME_STATE_DIR:-}" ] && [ -z "$STATE_FALLBACK" ] && [ -d "$LEGACY_PROFILE" ] && [ ! -L "$LEGACY_PROFILE" ]; then
    rm -rf --one-file-system "$LEGACY_PROFILE" && log "removed the old on-card profile $LEGACY_PROFILE"
fi
[ -n "$STATE_FALLBACK" ] && log "WARNING: $KIOSK_TMPFS missing or not writable; profile on the SD card ($STATE_DIR)"
log "start: browser=$BROWSER url=$URL session=${XDG_SESSION_TYPE:-?} wayland=${WAYLAND_DISPLAY:-} display=${DISPLAY:-}"

wait_healthy() {
    local n=0
    while resetting; do
        [ $((n % 30)) -eq 0 ] && log "factory reset in progress; waiting"
        n=$((n + 1)); sleep 1
    done
    n=0
    until ! resetting && curl -fsS --max-time 2 "$HEALTH" >/dev/null 2>&1; do
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
# Sage (#111): never /tmp (shared, predictable name); the user's own state dir if there is no runtime dir.
KILLS_FILE="${XDG_RUNTIME_DIR:-$STATE_DIR}/memoryframe-kiosk.kills"
BACKOFF_BASE="${MEMORYFRAME_KIOSK_BACKOFF:-2}"
BACKOFF_MAX=60

# #122: a stopped main browser process (state T: SIGSTOP or traced) leaves a frozen screen while the
# renderer keeps the heartbeat going (Hale's spike), so the watcher also relaunches when the browser pid has
# been in state T for more than STOPPED_MAX s IN A ROW (checked on the same LIVE_EVERY poll; any other state
# resets the timer). Same kill limits and relaunch path as a stale heartbeat. v1 residual risk (accepted):
# a real deadlock of the main process with a healthy renderer is not detected.
STOPPED_MAX="${MEMORYFRAME_STOPPED_MAX:-60}"
proc_state() {  # one-letter state from /proc/PID/stat; comm may hold spaces and ')', so take the text
    local st      # after the LAST ')' ("<state> <ppid> ...")
    st="$(cat "/proc/$1/stat" 2>/dev/null)" || return 1
    st="${st##*) }"
    printf '%s\n' "${st%% *}"
}

kill_for_liveness() {  # $1 = browser pid, $2 = reason for the log. Returns 0 if it killed the browser.
    local now recent=0 t
    now="$(date +%s)"
    if [ -f "$KILLS_FILE" ]; then
        while read -r t; do [ $((now - t)) -lt "$LIVE_WINDOW" ] && recent=$((recent + 1)); done < "$KILLS_FILE"
    fi
    if [ "$recent" -ge "$LIVE_KILLS" ]; then
        log "liveness: $2, but $recent kills in 10 min; not killing"
        return 1
    fi
    echo "$now" >> "$KILLS_FILE"
    log "liveness: $2; killing browser $1"
    kill "$1" 2>/dev/null
    kill -CONT "$1" 2>/dev/null     # a stopped process only acts on the TERM once it is continued
    return 0
}

watch_liveness() {  # $1 = browser pid
    local pid="$1" body age st stopped_since=""
    while kill -0 "$pid" 2>/dev/null; do
        sleep "$LIVE_EVERY"
        resetting && { stopped_since=""; continue; }
        st="$(proc_state "$pid")" || st=""
        if [ "$st" = T ]; then
            [ -n "$stopped_since" ] || stopped_since="$(date +%s)"
            if [ $(( $(date +%s) - stopped_since )) -gt "$STOPPED_MAX" ]; then
                kill_for_liveness "$pid" "browser stopped (state T) for over ${STOPPED_MAX} s" && return 0
                stopped_since=""                 # rate-limited: start a new 60 s window
            fi
            continue
        fi
        stopped_since=""
        body="$(curl -fsS --max-time 5 "$URL/frame/liveness" 2>/dev/null)" || continue
        case "$body" in *'"shutting_down":true'*|*'"shutting_down": true'*) continue ;; esac
        age="$(printf '%s' "$body" | sed -n 's/.*"kiosk_age_s": *\([0-9]*\).*/\1/p')"
        [ -n "$age" ] && [ "$age" -gt "$LIVE_MAX_AGE" ] || continue
        curl -fsS --max-time 5 "$HEALTH" >/dev/null 2>&1 || continue
        kill_for_liveness "$pid" "no heartbeat for ${age} s" && return 0
    done
}

# #97: Chromium honours only the last --disable-features, so ours and any from MEMORYFRAME_CHROMIUM_FLAGS
# are merged into one (no duplicates), and the extra flags are passed without their --disable-features.
DISABLE_FEATURES="Translate,OptimizationHints,MediaRouter"
EXTRA_FLAGS=()
# shellcheck disable=SC2086
for f in ${MEMORYFRAME_CHROMIUM_FLAGS:-}; do
    case "$f" in
        --disable-features=*)
            IFS=, read -r -a _feats <<< "${f#--disable-features=}"
            for x in "${_feats[@]}"; do
                [ -n "$x" ] || continue
                case ",$DISABLE_FEATURES," in *",$x,"*) ;; *) DISABLE_FEATURES="$DISABLE_FEATURES,$x" ;; esac
            done ;;
        *) EXTRA_FLAGS+=("$f") ;;
    esac
done

delay="$BACKOFF_BASE"
while true; do
    rotate_log
    wait_healthy
    resetting && continue
    mark_clean_exit
    rm -f "$PROFILE_DIR"/Singleton{Lock,Socket,Cookie}
    # #97: profile and cache on the tmpfs, caches capped at 32 MiB, no background/component/crash-report writes.
    set -- --kiosk --user-data-dir="$PROFILE_DIR" --disk-cache-dir="$CACHE_DIR" \
        --disk-cache-size=33554432 --media-cache-size=33554432 --password-store=basic --no-first-run \
        --no-default-browser-check --noerrdialogs --disable-infobars \
        --disable-session-crashed-bubble --hide-crash-restore-bubble \
        --disable-background-networking --disable-component-update --disable-breakpad \
        --disable-features="$DISABLE_FEATURES" --autoplay-policy=no-user-gesture-required \
        --check-for-update-interval=31536000 \
        "${PLATFORM_FLAGS[@]}" "${DEBUG_FLAGS[@]}" "${EXTRA_FLAGS[@]}" "$URL"
    log "launch: $BROWSER $*"
    started="$(date +%s)"
    "$BROWSER" "$@" 9>&- &
    bpid=$!
    watch_liveness "$bpid" 9>&- &
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
