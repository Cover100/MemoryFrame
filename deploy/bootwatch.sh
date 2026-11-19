#!/usr/bin/env bash
# #162 E4: the boot never reached the desktop session. Installed as /usr/local/sbin/memoryframe-bootwatch and run
# once per boot by memoryframe-bootwatch.service (root, Type=simple: it never delays the boot). It sleeps until 90 s
# of uptime (/proc/uptime, monotonic); if by then there is no labwc process and no /run/mfkiosk/session-up (written
# by session-start.sh), it shows Wren's E4:
#  - Plymouth still up and the Memory Frame theme active (PR B): `plymouth display-message --text=memoryframe-e4`
#    (the theme's message callback swaps in the E4 image);
#  - otherwise (Plymouth gone, or the stock theme, which would print the raw text): Plymouth is told to quit, and
#    two plain ASCII lines go to tty1, which is brought to the front (chvt 1).
# No reboot, no retry; it exits. Nothing else is read or written.
# Test hooks: MEMORYFRAME_UPTIME_FILE, MEMORYFRAME_BOOTWATCH_AT (s), MEMORYFRAME_KIOSK_TMPFS, MEMORYFRAME_TTY,
#             MEMORYFRAME_BOOTWATCH_SLEEP (s per check, default 1).
set -u
UPTIME_FILE="${MEMORYFRAME_UPTIME_FILE:-/proc/uptime}"
AT="${MEMORYFRAME_BOOTWATCH_AT:-90}"
STEP="${MEMORYFRAME_BOOTWATCH_SLEEP:-1}"
SESSION_UP="${MEMORYFRAME_KIOSK_TMPFS:-/run/mfkiosk}/session-up"
TTY="${MEMORYFRAME_TTY:-/dev/tty1}"
LINE1="The frame couldn't start. Error E4"
LINE2="Unplug for 10 seconds, plug back in."

up() { local u _r; read -r u _r < "$UPTIME_FILE" 2>/dev/null || u=0; printf '%s' "${u%%.*}"; }
session_up() { [ -e "$SESSION_UP" ] || pgrep -x labwc >/dev/null 2>&1; }

while [ "$(up)" -lt "$AT" ]; do
    session_up && { echo "bootwatch: session is up at $(up) s"; exit 0; }
    sleep "$STEP"
done
if session_up; then
    echo "bootwatch: session is up at $(up) s"
    exit 0
fi
echo "bootwatch: E4: no labwc and no session-up after $(up) s of uptime"
if command -v plymouth >/dev/null 2>&1 && plymouth --ping 2>/dev/null; then
    theme="$(plymouth-set-default-theme 2>/dev/null || true)"
    if [ "$theme" = memoryframe ]; then
        plymouth display-message --text=memoryframe-e4 && { echo "bootwatch: E4 shown by Plymouth"; exit 0; }
        echo "bootwatch: plymouth display-message failed; using tty1"
    else
        echo "bootwatch: Plymouth theme is '${theme:-?}' (no E4 image): quitting it for the tty1 text"
    fi
    plymouth quit 2>/dev/null || true
fi
if printf '\033[2J\033[H\n\n  %s\n  %s\n' "$LINE1" "$LINE2" > "$TTY" 2>/dev/null; then
    command -v chvt >/dev/null 2>&1 && chvt 1 2>/dev/null
    echo "bootwatch: E4 written to $TTY"
else
    echo "bootwatch: could not write to $TTY"
fi
exit 0
