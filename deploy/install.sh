#!/usr/bin/env bash
# Install Memory Frame on a Raspberry Pi 4B (Raspberry Pi OS 64-bit, desktop).
# Run from the repo checkout as the desktop user (not root):  ./deploy/install.sh
# Idempotent: safe to re-run after a git pull.
# #69: the app runs as the system user mfapp from a root-owned copy in /opt/memoryframe; the checkout is
# only the source. An upgrade saves a rollback pack first (deploy/memoryframe-migrate.sh); a failed or
# unhealthy upgrade is rolled back automatically, and memoryframe-rollback.txt on bootfs does it at boot.
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

SRC_DIR="$(cd "$(dirname "$0")/.." && pwd)"     # the git checkout (desktop user's)
APP_DIR="${MEMORYFRAME_OPT_DIR:-/opt/memoryframe}"   # what runs (root-owned)
APP_USER=mfapp                                     # runs the web app (unit, polkit rule, data dir)
DESKTOP_USER="$(id -un)"                           # runs the kiosk browser
[ "$DESKTOP_USER" = root ] && { echo "Run as the desktop user, not root." >&2; exit 1; }
HEALTH_URL="http://127.0.0.1:5000/healthz"
HEALTH_WAIT="${MEMORYFRAME_HEALTH_WAIT:-30}"
MIGRATE=/usr/local/sbin/memoryframe-migrate

# Write a root-owned file atomically: render to a temp file first (a failing sed stops here, set -e), then
# install it next to the target and rename over it. A cut leaves the old or the new file, never half of one.
put_file() {  # src dest mode [group]
    local new; new="$(dirname "$2")/.$(basename "$2").new"
    sudo install -o root -g "${4:-root}" -m "$3" "$1" "$new"
    sudo sync -f "$new"
    sudo mv -f "$new" "$2"
}
render() {  # template dest mode: replaces @USER@ / @APP_DIR@
    local tmp; tmp="$(mktemp)"
    sed -e "s#@USER@#$APP_USER#g" -e "s#@APP_DIR@#$APP_DIR#g" "$1" > "$tmp"
    put_file "$tmp" "$2" "$3"
    rm -f "$tmp"
}
# Bounded wait for the app to answer (#69 addendum): never says "restarted" before it really serves.
wait_healthy() {
    local end=$((SECONDS + HEALTH_WAIT))
    while [ "$SECONDS" -lt "$end" ]; do
        curl -fsS --max-time 2 "$HEALTH_URL" >/dev/null 2>&1 && return 0
        sleep 1
    done
    return 1
}

echo "== Packages"
sudo apt-get update
# polkitd (#62): lets the app start its root units and power off without sudo. Installed here; if it
# still isn't there, stop: without it Developer Wi-Fi, Wi-Fi change and Shut down can't work.
sudo apt-get install -y python3-venv curl polkitd rsync
if ! command -v pkaction >/dev/null 2>&1; then
    echo "ERROR: polkit (polkitd) is not installed and could not be installed: run 'sudo apt-get install polkitd', then re-run install.sh." >&2
    exit 1
fi
command -v chromium-browser >/dev/null || command -v chromium >/dev/null || sudo apt-get install -y chromium-browser

echo "== Service user and rollback pack (#69)"
DATA_DIR=/var/lib/memoryframe
sudo install -o root -g root -m 0755 "$SRC_DIR/deploy/memoryframe-migrate.sh" "$MIGRATE"
sudo install -o root -g root -m 0755 "$SRC_DIR/deploy/memoryframe-cli.sh" /usr/local/sbin/memoryframe-cli
# Upgrade only, once: old unit + rule + data owner + a photo backup, before anything changes.
sudo "$MIGRATE" save-pack
sudo "$MIGRATE" ensure-user

echo "== Code in $APP_DIR and its Python environment (old service keeps running)"
# root-owned copy: no .git, no venv (built below), no photos, no hotspot password
sudo install -d -o root -g root -m 0755 "$APP_DIR"
sudo rsync -a --delete --chown=root:root --chmod=Du=rwx,Dgo=rx,Fu=rw,Fgo=r \
    --exclude=/.git --exclude=/.venv --exclude=/uploads --exclude=/data --exclude=/deploy/hotspot.conf \
    --exclude=__pycache__ --exclude=/VERSION "$SRC_DIR/" "$APP_DIR/"
sudo chmod 0755 "$APP_DIR"/deploy/*.sh
VER_TMP="$(mktemp)"
git -C "$SRC_DIR" rev-parse --short HEAD > "$VER_TMP" 2>/dev/null || : > "$VER_TMP"
put_file "$VER_TMP" "$APP_DIR/VERSION" 0644
rm -f "$VER_TMP"
[ -d "$APP_DIR/.venv" ] || sudo python3 -m venv "$APP_DIR/.venv"
sudo "$APP_DIR/.venv/bin/pip" install -q -r "$APP_DIR/requirements.txt"
sudo "$APP_DIR/.venv/bin/python" -m compileall -q "$APP_DIR/memoryframe" "$APP_DIR/app.py" >/dev/null
mkdir -p "$SRC_DIR/uploads"

echo "== Data directory and one-time photo migration (MF-11)"
# #90: if the app is running, a failure (set -e) or a signal between the stop below and the normal restart
# would leave the frame stopped with no screen. So, only when it was ACTIVE and we stop it, arm an exit trap
# that restarts it once and keeps the original exit status. A service that was not running (fresh install, or
# stopped on purpose) is never started by the trap. The trap is cleared before it acts, so it can't loop.
# The restart comes FIRST and is capped at 60 s; messages follow, best effort: a dropped terminal (SIGHUP,
# closed or broken stderr) must not stop the restart, so HUP/PIPE are ignored and set -e is off in here.
# #69: ON_FAIL_CMD (set once the mfapp migration starts) runs BEFORE the restart, so the restart never starts
# the new unit on a half-migrated data dir. Then a bounded /healthz wait; if unhealthy, ONE more restart
# (skipped when the normal restart already was one of the two). Never daemon-reload here: the rollback
# restores and reloads the old unit itself; otherwise the loaded unit is the safe fallback.
ON_FAIL_CMD=""
NORMAL_RESTARTED=0
restart_if_stopped() {
    local rc=$?
    trap - EXIT ERR
    trap '' HUP PIPE
    set +e
    local restarted=0 healthy=0 rolled=""
    if [ -n "$ON_FAIL_CMD" ]; then
        timeout 120 $ON_FAIL_CMD >/dev/null 2>&1 && rolled=ok || rolled=failed
    fi
    timeout 60 sudo systemctl restart memoryframe.service && restarted=1
    [ "$restarted" = 1 ] && wait_healthy && healthy=1
    if [ "$restarted" = 1 ] && [ "$healthy" = 0 ] && [ "$NORMAL_RESTARTED" = 0 ]; then
        timeout 60 sudo systemctl restart memoryframe.service && wait_healthy && healthy=1
    fi
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
        case "$rolled" in
            ok) echo "The mfapp migration was rolled back (old unit, rule and data owner restored)." ;;
            failed) echo "ERROR: the automatic rollback failed: put memoryframe-rollback.txt (one line: rollback) on bootfs and reboot." ;;
        esac
        if [ "$healthy" = 1 ]; then
            echo "memoryframe.service, which install.sh had stopped, was restarted. The install is incomplete: re-run install.sh."
        elif [ "$restarted" = 1 ]; then
            echo "ERROR: memoryframe.service was restarted but is not answering: run the boot-partition rollback flag (memoryframe-rollback.txt with 'rollback' on bootfs, then reboot)."
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
sync
# #69: from here a failure must undo the owner change too, but only on the run that migrates (an upgrade with
# a pack whose data dir isn't mfapp's yet). A later re-install that fails is never rolled back to pre-mfapp.
if [ "$(stat -c %U "$DATA_DIR" 2>/dev/null)" != "$APP_USER" ] && sudo test -f /etc/memoryframe/rollback/previous-owner; then
    ON_FAIL_CMD="sudo $MIGRATE rollback"
fi
sudo "$MIGRATE" chown-data
# Integrity check of an existing database (a fresh install has none yet; `check` fails on a missing db).
if sudo test -f "$DATA_DIR/memoryframe.db"; then sudo /usr/local/sbin/memoryframe-cli check; fi
# The legacy uploads/ import reads the checkout as mfapp; if it can't (home dir 0750), skip it (done long ago).
LEGACY=/nonexistent
sudo runuser -u "$APP_USER" -- test -r "$SRC_DIR/uploads" -a -x "$SRC_DIR/uploads" 2>/dev/null && LEGACY="$SRC_DIR/uploads"
# Backs up uploads/ to $DATA_DIR/backups/legacy-uploads-<time>.tar (verified),
# then COPIES the photos into $DATA_DIR/originals. uploads/ is never changed.
# Does nothing if the import was already done. Aborts (exit 1) if space is short.
sudo env MEMORYFRAME_LEGACY_UPLOADS="$LEGACY" /usr/local/sbin/memoryframe-cli migrate \
    || echo "WARNING: migration failed (see above). Nothing was changed; the service is restarted anyway. Fix and re-run install.sh."
# MF-15: downscaled display copies for all photos (originals untouched; idempotent).
sudo /usr/local/sbin/memoryframe-cli display-copies \
    || echo "WARNING: some display copies failed; those photos are served as originals."
sudo env MEMORYFRAME_LEGACY_UPLOADS="$LEGACY" /usr/local/sbin/memoryframe-cli status

echo "== Audit and disable existing autostart entries"
"$SRC_DIR/deploy/audit-autostart.sh" --disable

if [ "$EXTRA_HOSTS_SET" = 1 ]; then
    echo "== Extra allowed hosts (MF-13)"
    "$SRC_DIR/deploy/env-file.sh" MEMORYFRAME_EXTRA_HOSTS "$EXTRA_HOSTS"
fi

echo "== systemd service"
# Every unit, helper and rule is written atomically (put_file/render); one daemon-reload after all of them.
render "$SRC_DIR/deploy/memoryframe.service" /etc/systemd/system/memoryframe.service 0644
# MF-36/MF-18: root-owned copy of the network helper (the checkout is writable by the desktop user).
put_file "$SRC_DIR/deploy/netmode.sh" /usr/local/sbin/memoryframe-netmode 0755
# Root-owned hotspot.sh + config for the memoryframe-reset boot path (root never runs checkout files).
put_file "$SRC_DIR/deploy/hotspot.sh" /usr/local/sbin/memoryframe-hotspot 0755
# #99: root-owned copy of the Erase everything helper (also finishes an interrupted reset at boot).
put_file "$SRC_DIR/deploy/factory-reset.sh" /usr/local/sbin/memoryframe-factory-reset 0755
sudo install -d -o root -g root -m 0755 /etc/memoryframe
if [ -f "$SRC_DIR/deploy/hotspot.conf" ]; then
    # #69: root:mfapp 0640: root parses it, the app reads SSID/QR from it; nobody else can.
    put_file "$SRC_DIR/deploy/hotspot.conf" /etc/memoryframe/hotspot.conf 0640 "$APP_USER"
fi
render "$SRC_DIR/deploy/memoryframe-netmode.service" /etc/systemd/system/memoryframe-netmode.service 0644
# #62: root oneshot units for the app's privileged actions + a polkit rule that lets only the app
# user start them (and power off). No sudoers rules: the app service keeps NoNewPrivileges.
# #69: + the boot-flag rollback unit; #68: home-wifi; #99: factory-reset.
for u in devtemp wifi-apply wifi-commit wifi-rollback rollback home-wifi factory-reset; do
    put_file "$SRC_DIR/deploy/units/memoryframe-$u.service" "/etc/systemd/system/memoryframe-$u.service" 0644
done
[ -d /etc/polkit-1/rules.d ] || sudo install -d -o root -g root -m 0755 /etc/polkit-1/rules.d   # never re-perm the distro's dir
render "$SRC_DIR/deploy/memoryframe-polkit.rules" /etc/polkit-1/rules.d/50-memoryframe.rules 0644
# #96: cap the journal and disable core dumps so logs can't fill the SD card (root 0644 drop-ins, applied
# by root here; nothing for mfapp to write).
for d in journald coredump; do
    sudo install -d -o root -g root -m 0755 "/etc/systemd/$d.conf.d"
    put_file "$SRC_DIR/deploy/$d-memoryframe.conf" "/etc/systemd/$d.conf.d/memoryframe.conf" 0644
done
sudo systemctl restart systemd-journald 2>/dev/null || true
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
# #99: runs before the app at every boot; does nothing unless a reset was cut short.
sudo systemctl enable memoryframe-factory-reset.service
sudo systemctl enable memoryframe.service memoryframe-rollback.service
sudo systemctl restart memoryframe.service
# #69: only "restarted" once it answers. If not, exit into the #90 trap (it rolls back first, restarts once).
if ! wait_healthy; then
    echo "ERROR: memoryframe.service is not answering $HEALTH_URL after ${HEALTH_WAIT}s." >&2
    NORMAL_RESTARTED=1
    exit 1
fi
echo "memoryframe.service restarted and answering."
# Restarted normally: disarm the #90 exit trap and give the signals their default behaviour back.
if [ "$RESTART_ON_EXIT" = 1 ]; then
    RESTART_ON_EXIT=0
    trap - EXIT ERR INT TERM HUP PIPE
fi

echo "== Case fan (gpio-fan overlay, GPIO 14, on at 60 C)"
if ! sudo env MEMORYFRAME_BOOT_CONFIG="${MEMORYFRAME_BOOT_CONFIG:-}" bash "$SRC_DIR/deploy/fan-overlay.sh"; then
    echo "WARNING: fan overlay not added" >&2
fi

# MF-49: the fb0 late shutdown hook didn't draw on vc4drmfb; remove it if an older install added it.
sudo rm -f /usr/lib/systemd/system-shutdown/memoryframe-safe-screen
sudo rm -rf /usr/lib/memoryframe

echo "== Kiosk autostart"
mkdir -p "$HOME/.config/autostart"
sed -e "s#@APP_DIR@#$APP_DIR#g" "$SRC_DIR/deploy/memoryframe-kiosk.desktop" \
    > "$HOME/.config/autostart/memoryframe-kiosk.desktop"

echo "== Session type: ${XDG_SESSION_TYPE:-unknown} (record this in the MF-04 PR)"
echo "== Kiosk reload (new JS/CSS)"
# The kiosk page never reloads by itself, so after a deploy it would keep running the old JS. If the kiosk
# Chromium is running (matched by its own profile dir), end it; kiosk.sh's loop relaunches it and it loads
# the new page (asset URLs carry a content hash, see memoryframe/assets.py). Skipped on CI / headless runs.
KIOSK_PROFILE="${MEMORYFRAME_STATE_DIR:-$HOME/.cache/memoryframe}/chromium-profile"
if [ -n "${CI:-}" ] || [ "${MEMORYFRAME_NO_KIOSK_RELOAD:-}" = 1 ]; then
    echo "skipped (CI or MEMORYFRAME_NO_KIOSK_RELOAD=1)"
elif "${PGREP:-pgrep}" -u "$DESKTOP_USER" -f -- "--user-data-dir=$KIOSK_PROFILE" >/dev/null 2>&1; then
    "${PKILL:-pkill}" -u "$DESKTOP_USER" -f -- "--user-data-dir=$KIOSK_PROFILE" || true
    echo "kiosk browser restarted by kiosk.sh with the new assets"
else
    echo "no kiosk running; nothing to reload"
fi

echo "Done. Reboot to verify: sudo reboot"
