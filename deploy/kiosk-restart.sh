#!/usr/bin/env bash
# #97: restart the kiosk LAUNCHER (not only Chromium) after a deploy, so the new kiosk.sh (new profile path on
# the /run/mfkiosk tmpfs, new flags) takes effect without a re-login or reboot. Run by install.sh as the
# desktop user. The launcher is started by XDG autostart in the graphical session, so the new one is started
# with that session's environment, copied from the running launcher (/proc/<pid>/environ, same user).
# Usage: kiosk-restart.sh KIOSK_SH   (the path the autostart entry runs, e.g. /opt/memoryframe/deploy/kiosk.sh)
# Exit 0: no launcher was running (the next login starts one), or the browser is back on the tmpfs profile.
# Exit 1: the old kiosk could not be stopped, or no browser on the tmpfs profile within the wait.
set -u
K="${1:?usage: kiosk-restart.sh KIOSK_SH}"
PGREP="${PGREP:-pgrep}"
TMPFS="${MEMORYFRAME_KIOSK_TMPFS:-/run/mfkiosk}"
WAIT="${MEMORYFRAME_KIOSK_RESTART_WAIT:-30}"        # for the new browser
STOP_WAIT="${MEMORYFRAME_KIOSK_STOP_WAIT:-15}"      # for the old kiosk to be fully gone (TERM, then KILL)
me="$(id -un)"
uid="$(id -u)"
re_k="$(printf '%s' "$K" | sed 's/[][\.*^$|+?(){}]/\\&/g')"
launchers() { "$PGREP" -u "$me" -f -- "^(/usr/bin/|/bin/)?bash $re_k\$"; }
profile_procs() {  # Chromium main processes on either kiosk profile (old card one or the tmpfs one)
    local prof
    for prof in "$HOME/.cache/memoryframe/chromium-profile" "$TMPFS/chromium-profile"; do
        "$PGREP" -u "$me" -f -- "--user-data-dir=$prof( |\$)"
    done
}
descendants() {  # all processes of ours below the given pids (Chromium helpers carry no --user-data-dir)
    local all todo="$*" out="" p c
    all="$(ps -u "$uid" -o pid=,ppid= 2>/dev/null)"
    while [ -n "${todo// /}" ]; do
        p="${todo%% *}"; todo="${todo#"$p"}"; todo="${todo# }"
        for c in $(printf '%s\n' "$all" | awk -v p="$p" '$2 == p {print $1}'); do
            out="$out $c"; todo="$todo $c"
        done
    done
    echo "$out"
}
lock_holders() {  # our processes with the launcher's lock file open (the lock outlives whoever holds fd 9)
    [ -n "$LOCK" ] || return 0
    # Compare each fd's target with the lock path literally (find -lname would treat [ * ? as a glob).
    find /proc/[0-9]*/fd -maxdepth 1 -user "$uid" -type l -printf '%h\t%l\n' 2>/dev/null |
        LOCK="$LOCK" awk -F'\t' '$2 == ENVIRON["LOCK"] { split($1, a, "/"); print a[3] }' | sort -u
}
proc_state() {  # one-letter state from /proc/PID/stat; comm (field 2) may hold spaces and ')', so take
    local st      # the text after the LAST ')' ("<state> <ppid> ...")
    st="$(cat "/proc/$1/stat" 2>/dev/null)" || return 1
    st="${st##*) }"
    printf '%s\n' "${st%% *}"
}
alive() {  # still running (a zombie has exited; it only waits for its parent to reap it)
    local p st out=""
    for p in "$@"; do
        st="$(proc_state "$p")" || continue
        [ -n "$st" ] && [ "$st" != Z ] && [ "$st" != X ] && out="$out $p"
    done
    echo "$out"
}
# Tests source this file with KIOSK_RESTART_LIB=1 to use the helpers above without restarting anything.
if [ "${KIOSK_RESTART_LIB:-}" = 1 ]; then return 0 2>/dev/null || exit 0; fi

pids="$(launchers | tr '\n' ' ')"
if [ -z "${pids// /}" ]; then
    echo "kiosk: no launcher running; the next login starts the new one"
    exit 0
fi
first="${pids%% *}"
vars=()
while IFS= read -r -d '' kv; do
    case "${kv%%=*}" in
        WAYLAND_DISPLAY|DISPLAY|XAUTHORITY|XDG_RUNTIME_DIR|XDG_SESSION_TYPE|XDG_SESSION_ID|XDG_CURRENT_DESKTOP|\
        DBUS_SESSION_BUS_ADDRESS|HOME|USER|LOGNAME|LANG|LC_ALL|PATH|SHELL|MEMORYFRAME_*) vars+=("$kv") ;;
    esac
done < "/proc/$first/environ"
LOCK="$(readlink "/proc/$first/fd/9" 2>/dev/null || true)"   # the launcher's single-instance lock
case "$LOCK" in /*) ;; *) LOCK="" ;; esac

# Everything the old kiosk started, taken BEFORE killing (helpers get re-parented once their parent dies):
# the launcher loop, its liveness subshell, Chromium and every Chromium helper process.
victims="$pids $(profile_procs | tr '\n' ' ')"
victims="$victims $(descendants $victims)"
# shellcheck disable=SC2086
kill -TERM $victims 2>/dev/null
stopped=0
half=$((STOP_WAIT * 5 / 2))
for i in $(seq 1 $((STOP_WAIT * 5))); do
    left="$(alive $victims $(launchers) $(profile_procs) $(lock_holders) | tr ' ' '\n' | sort -u | tr '\n' ' ')"
    if [ -z "${left// /}" ] && { [ -z "$LOCK" ] || [ ! -e "$LOCK" ] || flock -n "$LOCK" true; }; then
        stopped=1; break
    fi
    if [ "$i" -ge "$half" ] && [ -n "${left// /}" ]; then
        # shellcheck disable=SC2086
        kill -KILL $left 2>/dev/null
    fi
    sleep 0.2
done
if [ "$stopped" != 1 ]; then
    echo "ERROR: kiosk: old launcher/browser still running or lock ${LOCK:-?} still held after ${STOP_WAIT} s" \
         "(pids:${left:-}); new launcher NOT started" >&2
    exit 1
fi

env -i "${vars[@]}" setsid -f "$K" </dev/null >/dev/null 2>&1
echo "kiosk: launcher restarted in the desktop session"

# Confirm the browser came back on the tmpfs profile.
for _ in $(seq 1 $((WAIT * 5))); do
    if "$PGREP" -u "$me" -f -- "--user-data-dir=$TMPFS/chromium-profile" >/dev/null 2>&1; then
        echo "kiosk: browser running with --user-data-dir=$TMPFS/chromium-profile"
        exit 0
    fi
    sleep 0.2
done
echo "ERROR: kiosk browser not running on $TMPFS/chromium-profile ${WAIT} s after the restart" \
     "(see $TMPFS/memoryframe-kiosk.log)" >&2
exit 1
