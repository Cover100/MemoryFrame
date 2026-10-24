#!/usr/bin/env bash
# #69: run `python -m memoryframe <command>` as mfapp, so no root-owned files appear in the data dir.
# Installed as /usr/local/sbin/memoryframe-cli. E.g.: sudo memoryframe-cli check   (exit 1 unless "ok")
set -euo pipefail
[ "$EUID" = 0 ] || exec sudo "$0" "$@"
export PATH=/usr/sbin:/usr/bin:/sbin:/bin
cd /var/lib/memoryframe
exec runuser -u mfapp -- env MEMORYFRAME_DATA_DIR=/var/lib/memoryframe \
    MEMORYFRAME_LEGACY_UPLOADS="${MEMORYFRAME_LEGACY_UPLOADS:-/nonexistent}" \
    /opt/memoryframe/.venv/bin/python -m memoryframe "$@"
