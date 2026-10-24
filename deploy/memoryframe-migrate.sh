#!/usr/bin/env bash
# #69: move the app to the unprivileged service user mfapp, and undo it. Installed root-owned as
# /usr/local/sbin/memoryframe-migrate; run as root (install.sh via sudo, or memoryframe-rollback.service).
#   save-pack    once, before anything changes: rollback pack in /etc/memoryframe/rollback/ (old unit, polkit
#                rule, previous data owner) + a photo backup tar. Never overwritten on later runs.
#   ensure-user  create the system group/user mfapp (no home, nologin). Idempotent.
#   chown-data   give /var/lib/memoryframe to mfapp (dirs 0750, files 0640). Never deletes anything.
#   rollback [--dry-run]   restore unit + rule from the pack, chown data back, daemon-reload. No restart.
#   boot-flag    the boot path: handle /boot/firmware/memoryframe-rollback.txt (one line: rollback | dry-run).
# Never touches sshd, Pi Connect, the admin user, NetworkManager/netplan, cmdline.txt or photos' content.
set -euo pipefail

if [ "$EUID" = 0 ]; then
    # Root never takes paths or tools from the caller's environment (same rule as netmode.sh).
    export PATH=/usr/sbin:/usr/bin:/sbin:/bin
    unset MFM_ETC MFM_DATA MFM_UNITS MFM_POLKIT MFM_BOOT MFM_BACKUPS
fi
APP_USER=mfapp
ETC="${MFM_ETC:-/etc/memoryframe}"
PACK="$ETC/rollback"
DATA="${MFM_DATA:-/var/lib/memoryframe}"
UNIT="${MFM_UNITS:-/etc/systemd/system}/memoryframe.service"
RULE="${MFM_POLKIT:-/etc/polkit-1/rules.d}/50-memoryframe.rules"
FLAG="${MFM_BOOT:-/boot/firmware}/memoryframe-rollback.txt"
BACKUPS="${MFM_BACKUPS:-/var/backups/memoryframe}"

log() { echo "memoryframe-migrate: $*"; }

# Copy a file so the target is always the old or the new version, never a truncated one.
put() {  # src dest mode
    local tmp; tmp="$(dirname "$2")/.$(basename "$2").new"
    install -m "$3" "$1" "$tmp" && sync -f "$tmp" && mv -f "$tmp" "$2"
}

save_pack() {
    if [ -f "$PACK/previous-owner" ]; then log "rollback pack already saved: kept"; return 0; fi
    if [ ! -f "$UNIT" ]; then log "fresh install: no rollback pack needed"; return 0; fi
    install -d -m 0700 "$PACK"
    # Photos first: a tar of the originals (verified) before any owner change. Abort if space is short.
    if [ -d "$DATA/originals" ]; then
        install -d -m 0700 "$BACKUPS"
        local need free tar
        need=$(du -sk "$DATA/originals" | cut -f1)
        free=$(df -Pk "$BACKUPS" | awk 'NR==2 {print $4}')
        if [ "$free" -lt $((need + 102400)) ]; then
            log "ERROR: not enough space to back up the photos (${need} KB + 100 MB needed)"; exit 1
        fi
        tar="$BACKUPS/photos-before-mfapp-$(date +%Y%m%d-%H%M%S).tar"
        tar -C "$DATA" -cf "$tar.part" originals && tar -tf "$tar.part" >/dev/null && mv "$tar.part" "$tar"
        echo "$tar" > "$PACK/photos-backup"
        log "photos backed up to $tar"
    fi
    cp -p "$UNIT" "$PACK/memoryframe.service"
    [ -f "$RULE" ] && cp -p "$RULE" "$PACK/50-memoryframe.rules"
    # Written last: its presence means the pack is complete.
    stat -c %u:%g "$DATA" > "$PACK/previous-owner.new" && mv -f "$PACK/previous-owner.new" "$PACK/previous-owner"
    log "rollback pack saved in $PACK (previous owner $(cat "$PACK/previous-owner"))"
}

ensure_user() {
    getent group "$APP_USER" >/dev/null || groupadd --system "$APP_USER"
    id -u "$APP_USER" >/dev/null 2>&1 || useradd --system --gid "$APP_USER" --no-create-home \
        --home-dir "$DATA" --shell /usr/sbin/nologin "$APP_USER"
}

chown_data() {
    install -d -m 0750 "$DATA"
    # Only what isn't mfapp's yet, so a second run changes nothing. -h: never follow a symlink out.
    find "$DATA" \( ! -user "$APP_USER" -o ! -group "$APP_USER" \) -exec chown -h "$APP_USER:$APP_USER" {} +
    find "$DATA" -type d ! -perm 0750 -exec chmod 0750 {} +
    find "$DATA" -type f ! -perm 0640 -exec chmod 0640 {} +
}

rollback() {
    local dry="${1:-}"
    if [ ! -f "$PACK/previous-owner" ]; then log "ERROR: no rollback pack in $PACK: nothing to restore"; return 1; fi
    local owner; owner="$(cat "$PACK/previous-owner")"
    case "$owner" in *[!0-9:]*|"") log "ERROR: bad previous-owner '$owner'"; return 1 ;; esac
    if [ "$dry" = --dry-run ]; then
        log "dry run: would restore $UNIT from $PACK/memoryframe.service"
        [ -f "$PACK/50-memoryframe.rules" ] && log "dry run: would restore $RULE"
        log "dry run: would chown -R $owner $DATA, then systemctl daemon-reload and restart memoryframe"
        return 0
    fi
    put "$PACK/memoryframe.service" "$UNIT" 0644
    [ -f "$PACK/50-memoryframe.rules" ] && put "$PACK/50-memoryframe.rules" "$RULE" 0644
    chown -R -h "$owner" "$DATA"
    systemctl daemon-reload
    log "rolled back: unit and rule restored, $DATA owned by $owner again"
}

boot_flag() {
    [ -f "$FLAG" ] || return 0
    local want; want="$(head -n1 "$FLAG" | tr -d '\r' | tr '[:upper:]' '[:lower:]' | xargs)"
    # Rename first, so a failure below can never turn into a boot loop.
    mv -f "$FLAG" "${FLAG%.txt}.done-$(date +%Y%m%d-%H%M%S).txt"
    case "$want" in
        rollback) rollback && systemctl restart --no-block memoryframe.service ;;
        dry-run)  rollback --dry-run ;;
        *) log "flag file says '$want' (expected 'rollback' or 'dry-run'): nothing done" ;;
    esac
}

case "${1:-}" in
    save-pack) save_pack ;;
    ensure-user) ensure_user ;;
    chown-data) chown_data ;;
    rollback) rollback "${2:-}" ;;
    boot-flag) boot_flag ;;
    *) echo "usage: $0 save-pack|ensure-user|chown-data|rollback [--dry-run]|boot-flag" >&2; exit 2 ;;
esac
