#!/usr/bin/env bash
# #99: "Erase everything" from the frame's hold menu (user decision A1, 24 Oct 2026).
# Run as root by memoryframe-factory-reset.service: on demand (the app starts it via polkit after writing
# the request file) and at every boot (Before=memoryframe.service), where it rolls an interrupted reset
# FORWARD. It never rolls back: the user confirmed.
#
# Erases from the data dir: originals display thumbs tmp backups memoryframe.db(-wal,-shm)
# frame_message.txt (photos, the DB with media, pairings, sessions, settings, a pending Wi-Fi change,
# and the frame message). Keeps everything else: the hotspot name and password (so the printed card
# stays valid), /boot/firmware files, /etc/memoryframe, /opt, the app user, units, polkit, ssh, admin.
# Changing the hotspot name or password is a PC script only, never this helper.
#
# Steps (each power-cut safe; boot resumes from the marker):
#   1 marker  $DATA/.factory-reset ("erase <id>"), fsync
#   2 stop the app (on demand only; at boot it has not started)
#   3 mkdir $DATA/.erasing-<id>, mv the targets in (same filesystem: atomic renames), sync
#   4 rm -rf every .erasing-*, fstrim where supported, recreate the empty layout (data dir owner, 0750)
#   5 remove the marker LAST, sync, start the app (--no-block: this unit is ordered before it)
# Files are unlinked and trimmed, not forensically wiped (see docs/runbooks/factory-reset.md).
#
# Test hooks (ignored as root): MEMORYFRAME_DATA_DIR, MEMORYFRAME_RESET_REQ, SYSTEMCTL, FSTRIM, SYNC.
set -euo pipefail

if [ "$EUID" = 0 ] || [ "${MEMORYFRAME_AS_ROOT_TEST:-}" = 1 ]; then
    export PATH=/usr/sbin:/usr/bin:/sbin:/bin
    for _v in $(compgen -e); do
        case "$_v" in MEMORYFRAME_*|SYSTEMCTL|FSTRIM|SYNC) unset "$_v" ;; esac
    done
    unset _v
fi

DATA="${MEMORYFRAME_DATA_DIR:-/var/lib/memoryframe}"
REQ="${MEMORYFRAME_RESET_REQ:-/run/memoryframe-app/factory-reset}"
SYSTEMCTL="${SYSTEMCTL:-systemctl}"
FSTRIM="${FSTRIM:-fstrim}"
SYNC="${SYNC:-sync}"
MARKER="$DATA/.factory-reset"
TARGETS="originals display thumbs tmp backups memoryframe.db memoryframe.db-wal memoryframe.db-shm frame_message.txt"
LAYOUT="originals display thumbs tmp backups"

log() {
    echo "factory-reset: $*" >&2
    logger -t memoryframe-factory-reset -- "$*" 2>/dev/null || true
}

# The request the app leaves in its own 0700 runtime dir: a regular file (no symlink, FIFO or device),
# owned by that dir's (non-root) owner, exactly "erase".
read_request() {
    local dir owner body
    [ -e "$REQ" ] || [ -L "$REQ" ] || return 1
    dir="$(dirname "$REQ")"
    if [ -L "$REQ" ] || [ ! -f "$REQ" ]; then log "refused: request is not a regular file"; rm -f "$REQ"; return 2; fi
    owner="$(stat -c %u "$dir" 2>/dev/null)" || { rm -f "$REQ"; return 2; }
    if [ "$owner" = 0 ] || [ "$(stat -c %u "$REQ" 2>/dev/null)" != "$owner" ]; then
        log "refused: request not owned by the app user"; rm -f "$REQ"; return 2
    fi
    body="$(dd if="$REQ" iflag=nofollow,nonblock bs=64 count=1 status=none 2>/dev/null || true)"
    rm -f "$REQ"
    body="${body//$'\r'/}"; body="${body%$'\n'}"
    if [ "$body" != erase ]; then log "refused: unknown request"; return 2; fi
    return 0
}

valid_id() { [[ "$1" =~ ^[0-9]{1,20}$ ]]; }

erase() {   # $1 = id; $2 = on-demand|boot
    local id="$1" t owner group
    [ -d "$DATA" ] && [ ! -L "$DATA" ] || { log "data dir missing"; exit 1; }
    if [ "$2" = on-demand ]; then
        "$SYSTEMCTL" stop memoryframe.service || log "could not stop the app"
    fi
    mkdir -p "$DATA/.erasing-$id"
    for t in $TARGETS; do
        if [ -e "$DATA/$t" ] || [ -L "$DATA/$t" ]; then mv -f -- "$DATA/$t" "$DATA/.erasing-$id/"; fi
    done
    "$SYNC" || true
    for t in "$DATA"/.erasing-*; do
        [ -e "$t" ] || continue
        rm -rf --one-file-system -- "$t"
    done
    "$FSTRIM" "$DATA" >/dev/null 2>&1 || log "fstrim not supported here (files are unlinked only)"
    owner="$(stat -c %u "$DATA")"; group="$(stat -c %g "$DATA")"
    for t in $LAYOUT; do
        install -d -o "$owner" -g "$group" -m 0750 "$DATA/$t" 2>/dev/null || mkdir -p -m 0750 "$DATA/$t"
    done
    rm -f "$MARKER"
    "$SYNC" || true
    log "reset finished"
    "$SYSTEMCTL" --no-block start memoryframe.service || log "could not start the app"
}

marker_id() {
    local line
    line="$(dd if="$MARKER" iflag=nofollow,nonblock bs=64 count=1 status=none 2>/dev/null || true)"
    line="${line%$'\n'}"
    case "$line" in "erase "*) echo "${line#erase }" ;; *) echo "" ;; esac
}

rc=0; read_request || rc=$?
if [ "$rc" = 2 ]; then exit 2; fi
if [ "$rc" = 0 ]; then
    if [ -L "$MARKER" ]; then rm -f "$MARKER"; fi
    id="$(date +%s)"
    ( umask 077; printf 'erase %s\n' "$id" > "$MARKER.tmp"; mv -f "$MARKER.tmp" "$MARKER" )
    "$SYNC" || true
    log "erase everything requested"
    erase "$id" on-demand
    exit 0
fi
# No request: boot (or a stray start). Resume an interrupted reset, else nothing to do.
if [ -e "$MARKER" ] || [ -L "$MARKER" ]; then
    id="$(marker_id)"
    valid_id "$id" || id=0
    log "boot: interrupted reset found, finishing it"
    erase "$id" boot
elif compgen -G "$DATA/.erasing-*" >/dev/null; then
    rm -rf --one-file-system -- "$DATA"/.erasing-*      # marker already gone: only leftovers
fi
exit 0
