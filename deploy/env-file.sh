#!/usr/bin/env bash
# Set or clear one KEY=VALUE in the service's EnvironmentFile (/etc/default/memoryframe).
#   deploy/env-file.sh MEMORYFRAME_EXTRA_HOSTS 192.168.50.124,pi.home   # set/replace
#   deploy/env-file.sh MEMORYFRAME_MAX_MEGAPIXELS 100                    # pixel cap for uploads/import (default 150)
#   deploy/env-file.sh MEMORYFRAME_MAX_NONJPEG_MEGAPIXELS 50                # lower cap for HEIC/PNG/WebP/AVIF/GIF (default 50; every cap is 1-150)
#   deploy/env-file.sh MEMORYFRAME_DISPLAY_SIZE 1000x600                 # display copy size (WxH)
#   deploy/env-file.sh MEMORYFRAME_EXTRA_HOSTS ""                         # remove the line
#   deploy/env-file.sh MEMORYFRAME_SITE_URL http://memoryframe.local      # pairing/site URL override
# Other lines are kept. ROOT and SUDO are overridable for tests.
set -euo pipefail
ROOT="${ROOT:-}"
SUDO="${SUDO-sudo}"
FILE="$ROOT/etc/default/memoryframe"
key="${1:?usage: env-file.sh KEY VALUE}"
value="${2-}"

case "$key" in
    MEMORYFRAME_EXTRA_HOSTS)
        [[ "$value" =~ ^[A-Za-z0-9.:,_-]*$ ]] || { echo "Invalid host list: $value" >&2; exit 2; } ;;
    MEMORYFRAME_SITE_URL)
        [[ "$value" =~ ^(https?://[A-Za-z0-9.:_-]+)?$ ]] || { echo "Invalid site URL: $value" >&2; exit 2; } ;;
    MEMORYFRAME_MAX_MEGAPIXELS|MEMORYFRAME_MAX_NONJPEG_MEGAPIXELS)
        [[ -z "$value" || "$value" =~ ^([1-9][0-9]?|1[0-4][0-9]|150)$ ]] || { echo "Invalid megapixels (1-150): $value" >&2; exit 2; } ;;
    MEMORYFRAME_DISPLAY_SIZE)
        if [ -n "$value" ]; then
            [[ "$value" =~ ^([1-9][0-9]{2,3})x([1-9][0-9]{2,3})$ ]] \
                && (( BASH_REMATCH[1] >= 320 && BASH_REMATCH[1] <= 4000 && BASH_REMATCH[2] >= 320 && BASH_REMATCH[2] <= 4000 )) \
                || { echo "Invalid display size (WxH, each side 320-4000, e.g. 1000x600): $value" >&2; exit 2; }
        fi ;;
    *) echo "Unsupported key: $key" >&2; exit 2 ;;
esac

tmp="$(mktemp)"
trap 'rm -f "$tmp"' EXIT
if [ -f "$FILE" ]; then grep -v "^$key=" "$FILE" > "$tmp" || true; fi
[ -s "$tmp" ] || echo "# Memory Frame service settings (read by memoryframe.service). Managed by deploy/env-file.sh." > "$tmp"
[ -n "$value" ] && echo "$key=$value" >> "$tmp"
$SUDO install -D -m 644 "$tmp" "$FILE"
echo "$FILE:"
grep -v '^#' "$FILE" || echo "(no settings)"
