#!/usr/bin/env bash
# Memory Frame kiosk launcher. Started once per desktop login by the XDG
# autostart entry (deploy/memoryframe-kiosk.desktop).
#  - waits until the app answers /healthz (no "connection refused" page)
#  - runs exactly one Chromium kiosk, restarting it if it exits
set -u

URL="${MEMORYFRAME_URL:-http://localhost:5000}"
HEALTH="$URL/healthz"
LOCK="${XDG_RUNTIME_DIR:-/tmp}/memoryframe-kiosk.lock"

# Single instance of this launcher.
exec 9>"$LOCK"
if ! flock -n 9; then
    echo "memoryframe-kiosk: already running" >&2
    exit 0
fi

BROWSER=""
for b in chromium-browser chromium; do
    if command -v "$b" >/dev/null 2>&1; then BROWSER="$b"; break; fi
done
if [ -z "$BROWSER" ]; then
    echo "memoryframe-kiosk: chromium not found" >&2
    exit 1
fi

wait_healthy() {
    until curl -fsS --max-time 2 "$HEALTH" >/dev/null 2>&1; do
        sleep 1
    done
}

# Don't let the screen blank (X11 only; harmless elsewhere).
if [ -n "${DISPLAY:-}" ] && command -v xset >/dev/null 2>&1; then
    xset s off -dpms s noblank 2>/dev/null || true
fi

while true; do
    wait_healthy
    "$BROWSER" \
        --kiosk \
        --noerrdialogs \
        --disable-infobars \
        --disable-session-crashed-bubble \
        --disable-features=Translate \
        --autoplay-policy=no-user-gesture-required \
        --ozone-platform-hint=auto \
        --check-for-update-interval=31536000 \
        "$URL"
    echo "memoryframe-kiosk: browser exited, restarting" >&2
    sleep 2
done
