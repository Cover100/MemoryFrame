#!/bin/sh
# #162 boot handover: run by systemd as ExecStartPre of lightdm.service (the drop-in deploy/boot-handover.sh installs), i.e. at the
# moment lightdm would quit Plymouth itself (a plain `plymouth quit`, which blanks the screen: the ~5 s black gap before labwc
# and swaybg draw). Installed as /usr/local/sbin/memoryframe-plymouth-handoff.
#   1. `plymouth display-message --text=memoryframe-settle`: the theme eases the pulsing dot back to rest in about 0.5 s;
#   2. waits SETTLE_WAIT (0.7 s) for that;
#   3. `plymouth quit --retain-splash`: Plymouth exits but leaves its last frame (the lockup, dot at rest) on the screen, so
#      lightdm finds Plymouth already gone (it only quits an active one) and the login, labwc and swaybg start under the same image.
# Always exits 0 and is bounded by `timeout`: it can never stop lightdm from starting. Does nothing (lightdm quits Plymouth as
# before) when Plymouth isn't running or its theme isn't the memoryframe one.
# Test hooks: MEMORYFRAME_PLYMOUTH (the plymouth client), MEMORYFRAME_PLYMOUTH_SET (plymouth-set-default-theme),
#             MEMORYFRAME_SETTLE_WAIT (seconds), MEMORYFRAME_UPTIME_FILE.
PLYMOUTH="${MEMORYFRAME_PLYMOUTH:-plymouth}"
PLYMOUTH_SET="${MEMORYFRAME_PLYMOUTH_SET:-plymouth-set-default-theme}"
WAIT="${MEMORYFRAME_SETTLE_WAIT:-0.7}"
upt() { read -r u _r < "${MEMORYFRAME_UPTIME_FILE:-/proc/uptime}" 2>/dev/null || u="?"; printf '%s' "$u"; }
say() { echo "memoryframe-plymouth-handoff: $* (uptime $(upt))"; }
t() { if command -v timeout >/dev/null 2>&1; then timeout "$@"; else shift; "$@"; fi; }

command -v "$PLYMOUTH" >/dev/null 2>&1 || { say "no plymouth client: nothing to do"; exit 0; }
t 3 "$PLYMOUTH" --ping >/dev/null 2>&1 || { say "plymouth is not running: nothing to do"; exit 0; }
theme="$("$PLYMOUTH_SET" 2>/dev/null || true)"
if [ "$theme" != memoryframe ]; then say "theme is '${theme:-?}', not memoryframe: lightdm quits plymouth as usual"; exit 0; fi
t 3 "$PLYMOUTH" display-message --text=memoryframe-settle >/dev/null 2>&1 && say "settle sent" || say "settle not sent"
sleep "$WAIT" 2>/dev/null || sleep 1
if t 5 "$PLYMOUTH" quit --retain-splash >/dev/null 2>&1; then say "plymouth quit, splash retained"
else say "quit --retain-splash failed: lightdm will quit plymouth"; fi
exit 0
