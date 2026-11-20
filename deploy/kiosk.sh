#!/usr/bin/env bash
# Memory Frame kiosk launcher. Started once per desktop login by the XDG
# autostart entry (deploy/memoryframe-kiosk.desktop).
#  - waits until the app answers /healthz (no "connection refused" page)
#  - runs exactly one Chromium kiosk, restarting it if it exits
#  - uses a dedicated Chromium profile and never touches the OS keyring
#  - logs launcher + Chromium stdout/stderr (rotated at 1 MB) to the tmpfs (/run/mfkiosk/memoryframe-kiosk.log, #97),
#    or to ~/.cache/memoryframe-kiosk.log when MEMORYFRAME_STATE_DIR is set or the tmpfs is missing
#
# #162 boot splash (MEMORYFRAME_SPLASH, default on): Chromium starts at once (no healthz wait) on the local page
# static/boot/splash.html?next=<URL>, which hands over to the app (/?boot=1) when /healthz answers. A watcher (watch_boot)
# switches to Wren's error screens: E1 app up but the page never heartbeats (30 s), E2 app never answers (150 s) or
# memoryframe.service failed / restarting with NRestarts>=3 (two polls), E3 Chromium exits 3 times in 120 s (static
# swaybg PNG from the 6th exit, Chromium is still retried every 60 s). A page heartbeat clears the error state.
# /boot/firmware/memoryframe-desktop.txt (deploy/boot-splash.sh desktop on): the launcher exits at once (stock desktop).
#
# Environment overrides:
#   MEMORYFRAME_URL             default http://localhost:5000
#   MEMORYFRAME_CHROMIUM_FLAGS  extra flags, e.g. "--ozone-platform=wayland" or "--disable-gpu"
#   MEMORYFRAME_STATE_DIR       Chromium profile + cache parent, default /run/mfkiosk (#97: the RAM-only tmpfs
#                               from run-mfkiosk.mount, 192M, 0700, kiosk user); ~/.cache/memoryframe if it is missing
#   MEMORYFRAME_KIOSK_LOG       default: see above
#   MEMORYFRAME_KIOSK_DEBUG=1   adds DevTools on 127.0.0.1:9222 only + verbose Chromium logging
#   MEMORYFRAME_HIDE_CURSOR_DELAYS  #155: whole seconds after each Chromium launch at which the pointer is hidden
#                               again (default "2 8": at +2 s and +8 s)
#   MEMORYFRAME_SPLASH=0        #162: no splash page and no error screens (the pre-#162 flow: wait for /healthz)
#   MEMORYFRAME_BOOT_DIR        #162: boot partition holding memoryframe-desktop.txt, default /boot/firmware
#   MEMORYFRAME_BOOT_ASSETS     #162: directory of splash.html / error.html / *.png, default <this dir>/../static/boot
#   MEMORYFRAME_E1_AFTER, MEMORYFRAME_E2_AFTER, MEMORYFRAME_BOOT_POLL, MEMORYFRAME_E3_WINDOW  #162 timings in s
#                               (defaults 30, 150, 5, 120; tests shorten them)
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
# #162: monotonic seconds since boot for the log (the wall clock is skewed until NTP), to match a film of the boot.
upt() { local u _r; read -r u _r < "${MEMORYFRAME_UPTIME_FILE:-/proc/uptime}" 2>/dev/null || u="?"; printf '%s' "$u"; }

# #162: the developer's way out to the stock desktop: with the flag file on the boot partition no kiosk starts.
DESKTOP_FLAG="${MEMORYFRAME_BOOT_DIR:-/boot/firmware}/memoryframe-desktop.txt"
if [ -e "$DESKTOP_FLAG" ]; then
    log "desktop-mode flag present ($DESKTOP_FLAG): kiosk not started; delete the file and reboot to return to the frame"
    exit 0
fi

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
log "start: browser=$BROWSER url=$URL session=${XDG_SESSION_TYPE:-?} wayland=${WAYLAND_DISPLAY:-} display=${DISPLAY:-} uptime=$(upt)"

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

# #155: labwc draws its mouse pointer from session start, and the page's cursor:none only applies after a
# pointer event (a touch), so the pointer sat on screen until the first tap. install.sh adds a labwc keybind
# (Alt+Super+H: WarpCursor + HideCursor, a marked block in ~/.config/labwc/rc.xml); we press it with wtype
# at launcher start (the desktop shows while we wait for /healthz) and again after every Chromium launch.
# Best effort: in the background with fd 9 closed, capped at 5 s, logged; no wtype or no Wayland -> skipped.
# A real mouse still shows the pointer again as soon as it moves (labwc).
HIDE_CURSOR_DELAYS="${MEMORYFRAME_HIDE_CURSOR_DELAYS:-2 8}"
HIDE_CURSOR=""
if [ -z "${WAYLAND_DISPLAY:-}" ] && [ "${XDG_SESSION_TYPE:-}" != wayland ]; then
    log "hide-cursor: skipped (not a Wayland session)"
elif ! command -v wtype >/dev/null 2>&1; then
    log "hide-cursor: skipped (wtype not installed; the pointer shows until the first touch)"
else
    HIDE_CURSOR=1
fi
hide_cursor() {  # $1 = when (for the log), $2.. = delays in seconds
    [ -n "$HIDE_CURSOR" ] || return 0
    local when="$1"; shift
    (
        rc=0 prev=0
        for d in "$@"; do                      # whole seconds after the start, e.g. "2 8" = at +2 s and +8 s
            sleep "$((d - prev))"; prev="$d"
            if command -v timeout >/dev/null 2>&1; then
                timeout 5 wtype -M alt -M logo h -m alt -m logo </dev/null >/dev/null 2>&1; rc=$?
            else
                wtype -M alt -M logo h -m alt -m logo </dev/null >/dev/null 2>&1; rc=$?
            fi
            if [ "$rc" = 0 ]; then log "hide-cursor: pointer hidden ($when, +${d}s)"
            else log "hide-cursor: wtype failed with status $rc ($when, +${d}s)"; fi
        done
    ) 9>&- &
}
hide_cursor "launcher start" 0

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
# been in state T for STOPPED_MAX s or more IN A ROW (checked on the same LIVE_EVERY poll; any other state
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
    : > "$LIVENESS_KILLED"          # #162: not a crash, so it never counts toward E3
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
            # Timed in ms from the poll that first saw T, so with the defaults (20 s poll, 60 s) the relaunch
            # comes 60-80 s after the stop: >= STOPPED_MAX, at most one poll later.
            [ -n "$stopped_since" ] || stopped_since="$(date +%s%3N)"
            if [ $(( $(date +%s%3N) - stopped_since )) -ge $(( STOPPED_MAX * 1000 )) ]; then
                kill_for_liveness "$pid" "browser stopped (state T) for ${STOPPED_MAX} s or more" && return 0
                stopped_since=""                 # rate-limited: start a new 60 s window
            fi
            continue
        fi
        stopped_since=""
        [ -n "$(boot_state)" ] && continue      # #162: an error screen never heartbeats; watch_boot owns that state
        body="$(curl -fsS --max-time 5 "$URL/frame/liveness" 2>/dev/null)" || continue
        case "$body" in *'"shutting_down":true'*|*'"shutting_down": true'*) continue ;; esac
        age="$(printf '%s' "$body" | sed -n 's/.*"kiosk_age_s": *\([0-9]*\).*/\1/p')"
        [ -n "$age" ] && [ "$age" -gt "$LIVE_MAX_AGE" ] || continue
        curl -fsS --max-time 5 "$HEALTH" >/dev/null 2>&1 || continue
        kill_for_liveness "$pid" "no heartbeat for ${age} s" && return 0
    done
}

# ---------------- #162: boot splash and error screens ----------------
KIOSK_DIR="$(cd "$(dirname "$0")" && pwd)"
BOOT_ASSETS="$(cd "${MEMORYFRAME_BOOT_ASSETS:-$KIOSK_DIR/../static/boot}" 2>/dev/null && pwd)"
SPLASH=0
if [ "${MEMORYFRAME_SPLASH:-1}" = 0 ]; then
    log "splash: off (MEMORYFRAME_SPLASH=0): waiting for the app before each launch"
elif [ -z "$BOOT_ASSETS" ] || [ ! -f "$BOOT_ASSETS/splash.html" ]; then
    log "splash: off (no splash.html): waiting for the app before each launch"
else
    SPLASH=1
fi
E1_AFTER="${MEMORYFRAME_E1_AFTER:-30}"        # app healthy, page never heartbeated (kiosk_age_s null)
E2_AFTER="${MEMORYFRAME_E2_AFTER:-150}"       # app never answered since the launcher started (netmode's 150 s)
BOOT_POLL="${MEMORYFRAME_BOOT_POLL:-5}"
E3_WINDOW="${MEMORYFRAME_E3_WINDOW:-120}"
E3_EXITS=3                                    # exits in E3_WINDOW -> the E3 screen
E3_STATIC=6                                   # exits in a row -> swaybg error-e3.png (Chromium can't show anything)
HB_FRESH=15                                   # a heartbeat at most this old counts as "the page is alive"
BOOT_STATE="$STATE_DIR/kiosk-boot-state"      # "", E1, E2 or E3: what the next launch shows
EXITS_FILE="$STATE_DIR/kiosk-exits"           # times of unexpected browser exits (E3)
E3_STATIC_ON="$STATE_DIR/kiosk-e3-static"
BROWSER_PID="$STATE_DIR/kiosk-browser.pid"
RELAUNCH="$STATE_DIR/kiosk-relaunch"          # watch_boot closed the browser on purpose: relaunch at once, no backoff
LIVENESS_KILLED="$STATE_DIR/kiosk-liveness-killed"
SWAYBG_PIDFILE="${XDG_RUNTIME_DIR:-$KIOSK_TMPFS}/memoryframe-swaybg.pid"   # shared with session-start.sh
rm -f "$BOOT_STATE" "$EXITS_FILE" "$E3_STATIC_ON" "$BROWSER_PID" "$RELAUNCH" "$LIVENESS_KILLED"
SWAYBG=""
if [ "$SPLASH" = 1 ] && { [ -n "${WAYLAND_DISPLAY:-}" ] || [ "${XDG_SESSION_TYPE:-}" = wayland ]; } \
   && command -v swaybg >/dev/null 2>&1; then
    SWAYBG=1
fi

boot_state() { cat "$BOOT_STATE" 2>/dev/null; }
set_boot_state() { printf '%s\n' "$1" > "$BOOT_STATE.new" && mv -f "$BOOT_STATE.new" "$BOOT_STATE"; }
app_up() { curl -fsS --max-time 2 "$HEALTH" >/dev/null 2>&1; }

# What Chromium opens. The error URLs carry only the code (and, for the file:// page, the local app URL).
launch_url() {
    if [ "$SPLASH" != 1 ]; then printf '%s' "$URL"; return; fi
    case "$(boot_state)" in
        E1) printf '%s' "$URL/frame/error?code=E1" ;;
        E2) printf 'file://%s/error.html?e=E2&next=%s' "$BOOT_ASSETS" "$URL" ;;
        E3) if app_up; then printf '%s' "$URL/frame/error?code=E3"          # the app serves it, hold included
            else printf 'file://%s/error.html?e=E3&next=%s' "$BOOT_ASSETS" "$URL"; fi ;;
        *)  printf 'file://%s/splash.html?next=%s' "$BOOT_ASSETS" "$URL" ;;
    esac
}

# The background image under Chromium (session-start.sh starts the splash one). The new swaybg starts before the
# old one is stopped, so the screen never shows the desktop in between. Only a pid whose name is swaybg is signalled.
swaybg_show() {  # $1 = png
    [ -n "$SWAYBG" ] && [ -f "$1" ] || return 0
    local old new
    old="$(cat "$SWAYBG_PIDFILE" 2>/dev/null)"
    swaybg -c '#0E1311' -i "$1" -m center </dev/null >/dev/null 2>&1 9>&- &
    new=$!
    printf '%s\n' "$new" > "$SWAYBG_PIDFILE"
    log "swaybg: showing ${1##*/} (pid $new)"
    if [ -n "$old" ] && [ "$old" != "$new" ] && [ "$(cat "/proc/$old/comm" 2>/dev/null)" = swaybg ]; then
        (sleep 1; kill "$old" 2>/dev/null) 9>&- &
    fi
}

# watch_boot closes the browser so the main loop relaunches it on the URL for the new state.
relaunch_for() {  # $1 = reason (log)
    local pid
    pid="$(cat "$BROWSER_PID" 2>/dev/null)"
    [ -n "$pid" ] || return 0
    : > "$RELAUNCH"
    log "$1; closing browser $pid to show it"
    kill "$pid" 2>/dev/null
    kill -CONT "$pid" 2>/dev/null
}

clear_boot_state() {  # a page heartbeat: the frame works again
    local st; st="$(boot_state)"
    rm -f "$BOOT_STATE" "$EXITS_FILE"
    if [ -e "$E3_STATIC_ON" ]; then
        rm -f "$E3_STATIC_ON"
        swaybg_show "$BOOT_ASSETS/splash-A.png"
    fi
    log "recovered from $st: page heartbeat; uptime=$(upt)"
}

# One watcher per launcher (not per browser). Timers count from the launcher start (= splash start).
watch_boot() {
    local t0 now healthy_since="" ever_healthy="" seen_hb="" fired_at=0 bad_prev=0 bad st want why body age out act nr
    t0="$(date +%s)"
    while true; do
        sleep "$BOOT_POLL"
        now="$(date +%s)"
        if resetting; then                       # #113: the factory reset wins; start over afterwards
            t0="$now"; healthy_since=""; bad_prev=0
            continue
        fi
        st="$(boot_state)"; want=""; why=""
        if app_up; then
            if [ -z "$healthy_since" ]; then
                healthy_since="$now"
                [ -n "$ever_healthy" ] || log "server healthy; uptime=$(upt); /media has $(curl -fsS --max-time 2 "$URL/media" 2>/dev/null | grep -o '"filename"' | wc -l) item(s)"
                ever_healthy=1
            fi
            body="$(curl -fsS --max-time 5 "$URL/frame/liveness" 2>/dev/null)" || body=""
            age="$(printf '%s' "$body" | sed -n 's/.*"kiosk_age_s": *\([0-9][0-9]*\).*/\1/p')"
            if [ -n "$age" ] && [ "$age" -le "$HB_FRESH" ]; then
                [ -n "$seen_hb" ] || log "first page heartbeat; uptime=$(upt)"
                seen_hb=1
                [ -n "$st" ] && clear_boot_state
                bad_prev=0
                continue
            fi
            case "$body" in
                *'"kiosk_age_s":null'*|*'"kiosk_age_s": null'*)
                    if [ $((now - healthy_since)) -ge "$E1_AFTER" ]; then
                        case "$st" in
                            ""|E2) want=E1 ;;
                            # still nothing after the E1 page's own retry: show E1 again (not more often)
                            E1) [ $((now - fired_at)) -ge $((E1_AFTER * 2)) ] && want=E1 ;;
                        esac
                        why="the app answers but the page sent no heartbeat for $((now - healthy_since)) s"
                    fi ;;
            esac
        else
            healthy_since=""
            if [ -z "$ever_healthy" ] && [ -z "$st" ] && [ $((now - t0)) -ge "$E2_AFTER" ]; then
                want=E2; why="the app did not answer for $((now - t0)) s"
            fi
        fi
        # Fast path: the unit itself says it is broken (two polls in a row). Restart=always never stays "failed"
        # for long, so a restart loop (NRestarts >= 3 while not active) counts too.
        bad=0
        if command -v systemctl >/dev/null 2>&1; then
            out="$(systemctl show -p ActiveState,NRestarts memoryframe.service 2>/dev/null)"
            act="$(printf '%s\n' "$out" | sed -n 's/^ActiveState=//p')"
            nr="$(printf '%s\n' "$out" | sed -n 's/^NRestarts=\([0-9][0-9]*\)$/\1/p')"
            if [ "$act" = failed ] || { [ "${nr:-0}" -ge 3 ] && [ "$act" != active ]; }; then bad=1; fi
        fi
        if [ "$bad" = 1 ] && [ "$bad_prev" = 1 ] && [ "$st" != E2 ] && [ "$st" != E3 ]; then
            want=E2; why="memoryframe.service is $act (NRestarts=${nr:-?})"
        fi
        bad_prev="$bad"
        if [ -n "$want" ]; then
            set_boot_state "$want"; fired_at="$now"
            relaunch_for "error $want: $why; uptime=$(upt)"
        fi
    done
}

# Called by the main loop after an unexpected browser exit (splash mode only).
count_exit() {
    local now n=0 total=0 t
    now="$(date +%s)"
    echo "$now" >> "$EXITS_FILE"
    while read -r t; do
        total=$((total + 1))
        [ $((now - t)) -lt "$E3_WINDOW" ] && n=$((n + 1))
    done < "$EXITS_FILE"
    if [ "$n" -ge "$E3_EXITS" ] && [ "$(boot_state)" != E3 ]; then
        set_boot_state E3
        log "error E3: $n browser exits in ${E3_WINDOW} s; uptime=$(upt)"
    fi
    if [ "$total" -ge "$E3_STATIC" ] && [ ! -e "$E3_STATIC_ON" ]; then
        : > "$E3_STATIC_ON"
        log "error E3: $total browser exits in a row: static error screen; Chromium is still retried"
        swaybg_show "$BOOT_ASSETS/error-e3.png"
    fi
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

if [ "$SPLASH" = 1 ]; then
    watch_boot 9>&- &
    BOOT_WATCH=$!
    trap 'kill "$BOOT_WATCH" 2>/dev/null' EXIT
fi

wait_no_reset() {  # #113 (splash mode): only the factory-reset wait; the splash page waits for the app itself
    local n=0
    while resetting; do
        [ $((n % 30)) -eq 0 ] && log "factory reset in progress; waiting"
        n=$((n + 1)); sleep 1
    done
}

delay="$BACKOFF_BASE"
while true; do
    rotate_log
    if [ "$SPLASH" = 1 ]; then wait_no_reset; else wait_healthy; fi
    resetting && continue
    mark_clean_exit
    rm -f "$PROFILE_DIR"/Singleton{Lock,Socket,Cookie} "$RELAUNCH" "$LIVENESS_KILLED"
    # #97: profile and cache on the tmpfs, caches capped at 32 MiB, no background/component/crash-report writes.
    set -- --kiosk --user-data-dir="$PROFILE_DIR" --disk-cache-dir="$CACHE_DIR" \
        --disk-cache-size=33554432 --media-cache-size=33554432 --password-store=basic --no-first-run \
        --no-default-browser-check --noerrdialogs --disable-infobars \
        --force-dark-mode \
        --disable-session-crashed-bubble --hide-crash-restore-bubble \
        --disable-background-networking --disable-component-update --disable-breakpad \
        --disable-features="$DISABLE_FEATURES" --autoplay-policy=no-user-gesture-required \
        --check-for-update-interval=31536000 \
        "${PLATFORM_FLAGS[@]}" "${DEBUG_FLAGS[@]}" "${EXTRA_FLAGS[@]}" "$(launch_url)"
    log "launch: $BROWSER $*"
    log "launch uptime=$(upt)"
    started="$(date +%s)"
    "$BROWSER" "$@" 9>&- &
    bpid=$!
    printf '%s\n' "$bpid" > "$BROWSER_PID"
    watch_liveness "$bpid" 9>&- &
    wpid=$!
    # shellcheck disable=SC2086
    hide_cursor "after launch" $HIDE_CURSOR_DELAYS
    wait "$bpid"
    rc=$?
    kill "$wpid" 2>/dev/null
    rm -f "$BROWSER_PID"
    if [ -e "$RELAUNCH" ]; then                 # #162: closed by watch_boot for an error screen: not a crash
        log "browser closed (status $rc) to show $(boot_state || true); relaunching"
        delay="$BACKOFF_BASE"
        continue
    fi
    # #162: unexpected exits count toward E3 (a liveness kill is not a crash)
    if [ "$SPLASH" = 1 ] && [ ! -e "$LIVENESS_KILLED" ]; then count_exit; fi
    # #95: crash backoff 2, 4, 8 ... 60 s; reset once the browser ran longer than 60 s.
    [ $(( $(date +%s) - started )) -gt 60 ] && delay="$BACKOFF_BASE"
    log "browser exited with status $rc, restarting in $delay s"
    sleep "$delay"
    delay=$((delay * 2)); [ "$delay" -gt "$BACKOFF_MAX" ] && delay="$BACKOFF_MAX"
done
