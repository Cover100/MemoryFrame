#!/usr/bin/env bash
# #97: read-only SD-card write snapshot for the wear audit (docs/runbooks/sd-wear-audit.md).
# Usage: sudo deploy/sd-wear-capture.sh LABEL [OUTDIR]   e.g. S1-t0, S1-t1. Changes nothing on the system.
set -u
LABEL="${1:?usage: sd-wear-capture.sh LABEL [OUTDIR]}"
OUT="${2:-/root/sd-wear}"
DEV="${MEMORYFRAME_SD_DEV:-mmcblk0}"
OWNER_UID="${MEMORYFRAME_SD_WEAR_OWNER_UID:-0}"   # tests only; on the Pi the directory must be root's
# The label becomes a file name: no slashes, no "..", no spaces.
if ! [[ "$LABEL" =~ ^[A-Za-z0-9._-]+$ ]] || [ "$LABEL" = . ] || [ "$LABEL" = .. ]; then
    echo "ERROR: LABEL must match ^[A-Za-z0-9._-]+\$ (got '$LABEL')" >&2; exit 2
fi
# Never write through a symlink or into a directory someone else controls (this runs under sudo).
if [ ! -e "$OUT" ] && [ ! -L "$OUT" ]; then
    (umask 077; mkdir -p -- "$OUT") || { echo "ERROR: cannot create $OUT" >&2; exit 2; }
    chmod 0700 -- "$OUT"
fi
if [ -L "$OUT" ] || [ ! -d "$OUT" ]; then
    echo "ERROR: $OUT is not a real directory (symlink or not a directory); refusing" >&2; exit 2
fi
if [ "$(stat -c %u -- "$OUT")" != "$OWNER_UID" ]; then
    echo "ERROR: $OUT is not owned by uid $OWNER_UID; refusing" >&2; exit 2
fi
F="$OUT/$LABEL.txt"
pidio() {  # name pid
    [ -r "/proc/$2/io" ] && printf '%s %s %s\n' "$1" "$2" "$(awk '/^write_bytes/{print $2}' "/proc/$2/io")"
}
{
    echo "label=$LABEL"
    echo "date=$(date -Is)"
    echo "uptime_s=$(cut -d' ' -f1 /proc/uptime)"
    echo "card_bytes_written=$(awk '{print $7*512}' "/sys/block/$DEV/stat")"
    echo "## process write_bytes (name pid bytes)"
    pidio memoryframe "$(systemctl show -p MainPID --value memoryframe 2>/dev/null)"
    pidio systemd-journald "$(pgrep -xo systemd-journal || true)"
    pidio NetworkManager "$(pgrep -xo NetworkManager || true)"
    for p in $(pgrep chromium || true); do pidio chromium "$p"; done
    echo "## mounts"
    # findmnt takes at most one source and one target, so several paths printed nothing: one call per path.
    for m in / /boot/firmware /tmp /var/log /run/mfkiosk; do
        findmnt -no TARGET,FSTYPE,OPTIONS,SIZE,USED --mountpoint "$m" 2>/dev/null || echo "$m (not a mount point)"
    done
    echo "## swap"
    swapon --show 2>&1
    echo "## journal"
    journalctl --disk-usage 2>&1
    ls -d /var/log/journal 2>&1
    echo "## db"
    ls -l /var/lib/memoryframe/memoryframe.db* 2>&1
    echo "## sizes"
    du -xsh /home/memoryframe/.cache/memoryframe /home/memoryframe/.cache/memoryframe-kiosk.log /var/log/journal 2>&1
} > "$F"
echo "wrote $F"
