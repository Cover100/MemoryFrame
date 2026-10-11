#!/usr/bin/env bash
# Set or clear one KEY=VALUE in the service's EnvironmentFile (/etc/default/memoryframe).
#   deploy/env-file.sh MEMORYFRAME_EXTRA_HOSTS 192.168.50.124,pi.home   # set/replace
#   deploy/env-file.sh MEMORYFRAME_MAX_MEGAPIXELS 48                     # pixel cap for uploads/import
#   deploy/env-file.sh MEMORYFRAME_DISPLAY_SIZE 1000x750                 # display copy size (WxH)
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
    MEMORYFRAME_MAX_MEGAPIXELS)
        [[ -z "$value" || "$value" =~ ^[1-9][0-9]{0,3}$ ]] || { echo "Invalid megapixels (1-9999): $value" >&2; exit 2; } ;;
    MEMORYFRAME_DISPLAY_SIZE)
        [[ -z "$value" || "$value" =~ ^[1-9][0-9]{1,4}x[1-9][0-9]{1,4}$ ]] || { echo "Invalid display size (WxH, e.g. 800x600): $value" >&2; exit 2; } ;;
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
