#!/usr/bin/env bash
# #97: restart the kiosk LAUNCHER (not only Chromium) after a deploy, so the new kiosk.sh (new profile path on
# the /run/mfkiosk tmpfs, new flags) takes effect without a re-login or reboot. Run by install.sh as the
# desktop user. The launcher is started by XDG autostart in the graphical session, so the new one is started
# with that session's environment, copied from the running launcher (/proc/<pid>/environ, same user).
# Usage: kiosk-restart.sh KIOSK_SH   (the path the autostart entry runs, e.g. /opt/memoryframe/deploy/kiosk.sh)
# No launcher running: nothing is started (the next login does it). Exit 0 always; problems are WARNINGs.
set -u
K="${1:?usage: kiosk-restart.sh KIOSK_SH}"
PGREP="${PGREP:-pgrep}"
TMPFS="${MEMORYFRAME_KIOSK_TMPFS:-/run/mfkiosk}"
WAIT="${MEMORYFRAME_KIOSK_RESTART_WAIT:-30}"
me="$(id -un)"
re_k="$(printf '%s' "$K" | sed 's/[][\.*^$|+?(){}]/\\&/g')"
launchers() { "$PGREP" -u "$me" -f -- "^(/usr/bin/|/bin/)?bash $re_k\$"; }

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

# Stop the old loop (and its liveness subshell, same command line), then its Chromium (old or new profile).
# shellcheck disable=SC2086
kill -TERM $pids 2>/dev/null
for _ in $(seq 1 50); do [ -z "$(launchers)" ] && break; sleep 0.2; done
# shellcheck disable=SC2046
[ -z "$(launchers)" ] || kill -KILL $(launchers) 2>/dev/null
for prof in "$HOME/.cache/memoryframe/chromium-profile" "$TMPFS/chromium-profile"; do
    pkill -u "$me" -f -- "--user-data-dir=$prof( |\$)" 2>/dev/null || true
done

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
echo "WARNING: kiosk browser not seen on $TMPFS/chromium-profile after ${WAIT} s (check ~/.cache or $TMPFS/memoryframe-kiosk.log)"
exit 0
