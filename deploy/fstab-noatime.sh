#!/usr/bin/env bash
# #97: make sure the root filesystem (/) is mounted noatime. Idempotent: a / line that already has noatime is
# left alone. Otherwise the file is backed up once (FSTAB.memoryframe-97.bak, never overwritten), "relatime",
# "strictatime" and "atime" are dropped from the / options and "noatime" is added; the new file is written
# atomically. Only the / line is touched. ext4 commit= is deliberately not changed; cmdline.txt is not used.
# Usage: fstab-noatime.sh [FSTAB]   (default /etc/fstab). Exit 0 = ok/changed, 2 = no / line found.
set -euo pipefail
F="${1:-/etc/fstab}"
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
printf '%s\n' "$new" > "$F.memoryframe-97.new"
chmod --reference="$F" "$F.memoryframe-97.new" 2>/dev/null || true
mv -f "$F.memoryframe-97.new" "$F"
echo "fstab-noatime: / now noatime (backup $F.memoryframe-97.bak)"
