#!/usr/bin/env bash
# #97: read-only SD-card write snapshot for the wear audit (docs/runbooks/sd-wear-audit.md).
# Usage: sudo deploy/sd-wear-capture.sh LABEL [OUTDIR]   e.g. S1-t0, S1-t1. Changes nothing on the system.
set -u
LABEL="${1:?usage: sd-wear-capture.sh LABEL [OUTDIR]}"
OUT="${2:-/tmp/sd-wear}"
DEV="${MEMORYFRAME_SD_DEV:-mmcblk0}"
mkdir -p "$OUT"
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
    findmnt -no TARGET,FSTYPE,OPTIONS / /boot/firmware /tmp /var/log 2>&1
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
