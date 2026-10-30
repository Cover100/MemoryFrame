#!/usr/bin/env bash
# Install Memory Frame on a Raspberry Pi 4B (Raspberry Pi OS 64-bit, desktop).
# Run from the repo checkout as the desktop user (not root):  ./deploy/install.sh
# Idempotent: safe to re-run after a git pull.
# Options:
#   --extra-hosts LIST   extra Host names/IPs the app answers (comma list, e.g. 192.168.50.124);
#                        "" removes them. Also taken from $MEMORYFRAME_EXTRA_HOSTS. Omit to keep.
set -euo pipefail

EXTRA_HOSTS_SET=0
EXTRA_HOSTS="${MEMORYFRAME_EXTRA_HOSTS-}"
[ -n "${MEMORYFRAME_EXTRA_HOSTS+x}" ] && EXTRA_HOSTS_SET=1
while [ $# -gt 0 ]; do
    case "$1" in
        --extra-hosts) EXTRA_HOSTS="${2-}"; EXTRA_HOSTS_SET=1; shift 2 || shift ;;
        --extra-hosts=*) EXTRA_HOSTS="${1#*=}"; EXTRA_HOSTS_SET=1; shift ;;
        *) echo "Unknown option: $1" >&2; exit 2 ;;
    esac
done

APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
APP_USER="$(id -un)"
[ "$APP_USER" = root ] && { echo "Run as the desktop user, not root." >&2; exit 1; }

echo "== Packages"
sudo apt-get update
# polkitd (#62): lets the app start its root units and power off without sudo. Installed here; if it
# still isn't there, stop: without it Developer Wi-Fi, Wi-Fi change and Shut down can't work.
sudo apt-get install -y python3-venv curl polkitd
if ! command -v pkaction >/dev/null 2>&1; then
    echo "ERROR: polkit (polkitd) is not installed and could not be installed: run 'sudo apt-get install polkitd', then re-run install.sh." >&2
    exit 1
fi
command -v chromium-browser >/dev/null || command -v chromium >/dev/null || sudo apt-get install -y chromium-browser
# ffmpeg (#102): videos are converted on the frame. Not fatal: without it uploads say "videos are coming later".
sudo apt-get install -y ffmpeg || echo "WARNING: ffmpeg could not be installed: videos cannot be added until it is (sudo apt-get install ffmpeg)." >&2

echo "== Python environment"
[ -d "$APP_DIR/.venv" ] || python3 -m venv "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/pip" install -r "$APP_DIR/requirements.txt"
mkdir -p "$APP_DIR/uploads"

echo "== Data directory and one-time photo migration (MF-11)"
DATA_DIR=/var/lib/memoryframe
sudo install -d -o "$APP_USER" -g "$(id -gn)" -m 750 "$DATA_DIR"
# #90: if the app is running, a failure (set -e) or a signal between the stop below and the normal restart
# would leave the frame stopped with no screen. So, only when it was ACTIVE and we stop it, arm an exit trap
# that restarts it once and keeps the original exit status. A service that was not running (fresh install, or
# stopped on purpose) is never started by the trap. The trap is cleared before it acts, so it can't loop.
# The restart comes FIRST and is capped at 60 s; messages follow, best effort: a dropped terminal (SIGHUP,
# closed or broken stderr) must not stop the restart, so HUP/PIPE are ignored and set -e is off in here.
restart_if_stopped() {
    local rc=$?
    trap - EXIT ERR
    trap '' HUP PIPE
    set +e
    local restarted=0
    timeout 60 sudo systemctl restart memoryframe.service && restarted=1
    {
        # "interrupted" only when one of our signal traps fired, not for a step that itself exited 129/130/143
        case "$INTERRUPTED$rc" in
            1*) echo "ERROR: install.sh was interrupted by a signal (exit $rc)." ;;
            00) ;;
            # FAIL_AT is only trusted if it was recorded for this same exit status (no stale line or command)
            *) if [ "${FAIL_RC:-}" = "$rc" ] && [ -n "${FAIL_AT:-}" ]; then
                   echo "ERROR: install.sh failed (exit $rc) at $FAIL_AT."
               else
                   echo "ERROR: install.sh failed (exit $rc) at an unknown step."
               fi ;;
        esac
        if [ "$restarted" = 1 ]; then
            echo "memoryframe.service, which install.sh had stopped, was restarted. The install is incomplete: re-run install.sh."
        else
            echo "ERROR: could not restart memoryframe.service: run 'sudo systemctl restart memoryframe.service'."
        fi
    } >&2 2>/dev/null
    exit "$rc"
}
RESTART_ON_EXIT=0
if systemctl is-active --quiet memoryframe.service 2>/dev/null; then
    RESTART_ON_EXIT=1
    FAIL_RC=""; FAIL_AT=""; INTERRUPTED=0
    trap 'FAIL_RC=$?; FAIL_AT="line $LINENO: $BASH_COMMAND"' ERR
    trap restart_if_stopped EXIT
    # Signals must run the EXIT trap too (bash runs it on `exit`): Ctrl-C / SIGHUP / SIGTERM mid-install.
    trap 'INTERRUPTED=1; exit 130' INT
    trap 'INTERRUPTED=1; exit 143' TERM
    trap 'INTERRUPTED=1; exit 129' HUP
    # bash prints "Terminated"/"Hangup" for a killed step to stderr; if that pipe is gone the default SIGPIPE kills bash before the EXIT trap
    trap '' PIPE
fi
# Stop the running app first so the migration has the database to itself.
sudo systemctl stop memoryframe.service 2>/dev/null || true
# Backs up uploads/ to $DATA_DIR/backups/legacy-uploads-<time>.tar (verified),
# then COPIES the photos into $DATA_DIR/originals. uploads/ is never changed.
# Does nothing if the import was already done. Aborts (exit 1) if space is short.
MEMORYFRAME_DATA_DIR="$DATA_DIR" MEMORYFRAME_LEGACY_UPLOADS="$APP_DIR/uploads" \
    "$APP_DIR/.venv/bin/python" -m memoryframe migrate \
    || echo "WARNING: migration failed (see above). Nothing was changed; the service is restarted anyway. Fix and re-run install.sh."
# MF-15: downscaled display copies for all photos (originals untouched; idempotent).
MEMORYFRAME_DATA_DIR="$DATA_DIR" "$APP_DIR/.venv/bin/python" -m memoryframe display-copies \
    || echo "WARNING: some display copies failed; those photos are served as originals."
MEMORYFRAME_DATA_DIR="$DATA_DIR" MEMORYFRAME_LEGACY_UPLOADS="$APP_DIR/uploads" \
    "$APP_DIR/.venv/bin/python" -m memoryframe status

echo "== Audit and disable existing autostart entries"
"$APP_DIR/deploy/audit-autostart.sh" --disable

if [ "$EXTRA_HOSTS_SET" = 1 ]; then
    echo "== Extra allowed hosts (MF-13)"
    "$APP_DIR/deploy/env-file.sh" MEMORYFRAME_EXTRA_HOSTS "$EXTRA_HOSTS"
fi

echo "== systemd service"
sed -e "s#@USER@#$APP_USER#g" -e "s#@APP_DIR@#$APP_DIR#g" \
    "$APP_DIR/deploy/memoryframe.service" | sudo tee /etc/systemd/system/memoryframe.service >/dev/null
# MF-36/MF-18: root-owned copy of the network helper (the checkout is writable by the app user).
sudo install -o root -g root -m 0755 "$APP_DIR/deploy/netmode.sh" /usr/local/sbin/memoryframe-netmode
# Root-owned hotspot.sh + config for the memoryframe-reset boot path (root never runs checkout files).
sudo install -o root -g root -m 0755 "$APP_DIR/deploy/hotspot.sh" /usr/local/sbin/memoryframe-hotspot
if [ -f "$APP_DIR/deploy/hotspot.conf" ]; then
    sudo install -d -o root -g root -m 0755 /etc/memoryframe
    sudo install -o root -g root -m 0600 "$APP_DIR/deploy/hotspot.conf" /etc/memoryframe/hotspot.conf
fi
sed -e "s#@APP_DIR@#$APP_DIR#g" \
    "$APP_DIR/deploy/memoryframe-netmode.service" | sudo tee /etc/systemd/system/memoryframe-netmode.service >/dev/null
# #62: root oneshot units for the app's privileged actions + a polkit rule that lets only the app
# user start them (and power off). No sudoers rules: the app service keeps NoNewPrivileges.
for u in devtemp wifi-apply wifi-commit wifi-rollback; do
    sudo install -o root -g root -m 0644 "$APP_DIR/deploy/units/memoryframe-$u.service" "/etc/systemd/system/memoryframe-$u.service"
done
POLKIT_TMP="$(mktemp)"
sed -e "s#@USER@#$APP_USER#g" "$APP_DIR/deploy/memoryframe-polkit.rules" > "$POLKIT_TMP"
[ -d /etc/polkit-1/rules.d ] || sudo install -d -o root -g root -m 0755 /etc/polkit-1/rules.d   # never re-perm the distro's dir
sudo install -o root -g root -m 0644 "$POLKIT_TMP" /etc/polkit-1/rules.d/50-memoryframe.rules
rm -f "$POLKIT_TMP"
# Old sudoers rules from before #62 (unused now).
sudo rm -f /etc/sudoers.d/memoryframe /etc/sudoers.d/memoryframe-poweroff
sudo systemctl daemon-reload
# MF-51 deploy guard: boot now defaults to product (hotspot). A Pi that is on a home Wi-Fi
# client connection and has no mode file is a dev Pi: write "dev" so its next reboot keeps
# home Wi-Fi (and Pi Connect). An existing mode file is never changed.
MODE_FILE="${MEMORYFRAME_BOOT_DIR:-/boot/firmware}/memoryframe-mode.txt"
CUR_WIFI="$("${NMCLI:-nmcli}" -g GENERAL.CONNECTION device show "${MEMORYFRAME_IFACE:-wlan0}" 2>/dev/null || true)"
if [ ! -e "$MODE_FILE" ] && [ -n "$CUR_WIFI" ] && [ "$CUR_WIFI" != "MemoryFrame-hotspot" ]; then
    echo "MF-51: on home Wi-Fi ($CUR_WIFI) with no mode file: writing 'dev' to $MODE_FILE so reboots keep home Wi-Fi"
    printf 'dev\n' | sudo tee "$MODE_FILE" >/dev/null
fi
# MF-36: applied at the NEXT boot (enabling does not switch the network now).
sudo systemctl enable memoryframe-netmode.service
sudo systemctl enable memoryframe.service
sudo systemctl restart memoryframe.service
# Restarted normally: disarm the #90 exit trap and give the signals their default behaviour back.
if [ "$RESTART_ON_EXIT" = 1 ]; then
    RESTART_ON_EXIT=0
    trap - EXIT ERR INT TERM HUP PIPE
fi

echo "== Case fan (gpio-fan overlay, GPIO 14, on at 60 C)"
if ! sudo env MEMORYFRAME_BOOT_CONFIG="${MEMORYFRAME_BOOT_CONFIG:-}" bash "$APP_DIR/deploy/fan-overlay.sh"; then
    echo "WARNING: fan overlay not added" >&2
fi

# MF-49: the fb0 late shutdown hook didn't draw on vc4drmfb; remove it if an older install added it.
sudo rm -f /usr/lib/systemd/system-shutdown/memoryframe-safe-screen
sudo rm -rf /usr/lib/memoryframe

echo "== Kiosk autostart"
chmod +x "$APP_DIR/deploy/kiosk.sh"
mkdir -p "$HOME/.config/autostart"
sed -e "s#@APP_DIR@#$APP_DIR#g" "$APP_DIR/deploy/memoryframe-kiosk.desktop" \
    > "$HOME/.config/autostart/memoryframe-kiosk.desktop"

echo "== Session type: ${XDG_SESSION_TYPE:-unknown} (record this in the MF-04 PR)"
echo "== Kiosk reload (new JS/CSS)"
# The kiosk page never reloads by itself, so after a deploy it would keep running the old JS. If the kiosk
# Chromium is running (matched by its own profile dir), end it; kiosk.sh's loop relaunches it and it loads
# the new page (asset URLs carry a content hash, see memoryframe/assets.py). Skipped on CI / headless runs.
KIOSK_PROFILE="${MEMORYFRAME_STATE_DIR:-$HOME/.cache/memoryframe}/chromium-profile"
if [ -n "${CI:-}" ] || [ "${MEMORYFRAME_NO_KIOSK_RELOAD:-}" = 1 ]; then
    echo "skipped (CI or MEMORYFRAME_NO_KIOSK_RELOAD=1)"
elif "${PGREP:-pgrep}" -u "$APP_USER" -f -- "--user-data-dir=$KIOSK_PROFILE" >/dev/null 2>&1; then
    "${PKILL:-pkill}" -u "$APP_USER" -f -- "--user-data-dir=$KIOSK_PROFILE" || true
    echo "kiosk browser restarted by kiosk.sh with the new assets"
else
    echo "no kiosk running; nothing to reload"
fi

echo "Done. Reboot to verify: sudo reboot"
