#!/usr/bin/env bash
# #97: make sure the root filesystem (/) is mounted noatime. Idempotent: a / line that already has noatime is
# left alone. Otherwise the file is backed up once (FSTAB.memoryframe-97.bak, never overwritten), "relatime",
# "strictatime" and "atime" are dropped from the / options and "noatime" is added; the new file is written
# atomically. Only the / line is touched. ext4 commit= is deliberately not changed; cmdline.txt is not used.
# --apply (install.sh): only when THIS run changed the file, check it with `findmnt --verify`; if that fails, put
# back the copy taken in this run just before the change (temp file next to it, then mv -fT) and exit 3,
# otherwise `mount -o remount,noatime /`. The one-time .bak is history only and is never restored from.
# Usage: fstab-noatime.sh [--apply] [FSTAB]   (default /etc/fstab).
# Exit 0 = unchanged or changed (and verified with --apply), 2 = no / line found, 3 = changed, verify failed, restored.
set -euo pipefail
APPLY=""
[ "${1:-}" = --apply ] && { APPLY=1; shift; }
F="${1:-/etc/fstab}"
D="$(dirname "$F")"
grep -Eq '^[[:space:]]*[^#[:space:]]+[[:space:]]+/[[:space:]]' "$F" || { echo "fstab-noatime: no / entry in $F" >&2; exit 2; }
if grep -Eq '^[[:space:]]*[^#[:space:]]+[[:space:]]+/[[:space:]]+[^[:space:]]+[[:space:]]+([^[:space:]]*,)?noatime(,|[[:space:]]|$)' "$F"; then
    echo "fstab-noatime: / already noatime"; exit 0
fi
new="$(awk 'BEGIN{OFS="\t"}
  /^[[:space:]]*#/ || NF < 4 || $2 != "/" { print; next }
  { n = split($4, o, ","); out = ""; have = 0
    for (i = 1; i <= n; i++) { if (o[i] == "noatime") have = 1
      if (o[i] == "relatime" || o[i] == "strictatime" || o[i] == "atime") continue
      out = out (out == "" ? "" : ",") o[i] }
    if (!have) out = (out == "" || out == "defaults" ? "defaults,noatime" : out ",noatime")
    $4 = out; print }' "$F")"
[ -e "$F.memoryframe-97.bak" ] || cp -p "$F" "$F.memoryframe-97.bak"
prev="$(mktemp "$D/.fstab.memoryframe-97.prev.XXXXXX")"
trap 'rm -f "$prev"' EXIT
cp -p "$F" "$prev"                                   # this run's copy, the only rollback source
tmp="$(mktemp "$D/.fstab.memoryframe-97.new.XXXXXX")"
printf '%s\n' "$new" > "$tmp"
chmod --reference="$F" "$tmp" 2>/dev/null || true
mv -fT "$tmp" "$F"
echo "fstab-noatime: / now noatime (one-time history copy $F.memoryframe-97.bak)"
[ -n "$APPLY" ] || exit 0
if findmnt --verify --tab-file "$F" >/dev/null 2>&1; then
    mount -o remount,noatime / || echo "fstab-noatime: WARNING: remount noatime failed; applies at next boot"
    exit 0
fi
back="$(mktemp "$D/.fstab.memoryframe-97.restore.XXXXXX")"
cp -p "$prev" "$back"
mv -fT "$back" "$F"
echo "fstab-noatime: ERROR: findmnt --verify failed on the edited $F; restored this run's copy" >&2
exit 3
