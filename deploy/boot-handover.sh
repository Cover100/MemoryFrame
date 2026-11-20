#!/usr/bin/env bash
# #162 boot handover: the three system files that close the black gap and the grey/blue flash. Idempotent (a file already as we
# would write it is left alone); every change is undone by `off` (deploy/boot-splash.sh off and deploy/uninstall.sh run it).
# Takes effect at the next boot; never reboots, never restarts lightdm. refs #162.
#
#   sudo deploy/boot-handover.sh on|off|status
#
#  1. /usr/local/sbin/memoryframe-plymouth-handoff            root copy of deploy/plymouth-handoff.sh
#  2. /etc/systemd/system/lightdm.service.d/memoryframe-handoff.conf
#                                                             ExecStartPre=-<1>: before lightdm starts, the Plymouth dot is eased
#                                                             to rest and Plymouth quits with --retain-splash, so the splash stays up
#                                                             through the login instead of a black screen (lightdm would otherwise
#                                                             run a plain `plymouth quit`). The leading "-" means a failure never
#                                                             stops lightdm.
#  3. /etc/chromium/policies/managed/memoryframe-boot.json    Chromium managed policy: browser theme colour #0E1311 and a dark
#                                                             colour scheme, so Chromium's own first frame is ink, not light grey.
# `off` removes exactly these (a file somebody edited since is kept and named), removes the drop-in directory if it is empty, and
# reloads systemd. It does not touch cmdline.txt, config.txt, memoryframe-mode.txt, netplan, the Plymouth theme or any package
# (swaybg stays installed: `sudo apt-get remove swaybg`).
# Test hooks: MEMORYFRAME_SBIN, MEMORYFRAME_SYSTEMD_DIR, MEMORYFRAME_CHROMIUM_POLICY_DIR (all three or none: no root check and no
# systemctl), MEMORYFRAME_NO_SYSTEMCTL=1.
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
SBIN="${MEMORYFRAME_SBIN:-/usr/local/sbin}"
SYSD="${MEMORYFRAME_SYSTEMD_DIR:-/etc/systemd/system}"
POLICY="${MEMORYFRAME_CHROMIUM_POLICY_DIR:-/etc/chromium/policies/managed}"
HANDOFF="$SBIN/memoryframe-plymouth-handoff"
DROPDIR="$SYSD/lightdm.service.d"
DROPIN="$DROPDIR/memoryframe-handoff.conf"
POLICY_FILE="$POLICY/memoryframe-boot.json"
MARK="memoryframe-managed boot-handover"
HOOKED=""; { [ -n "${MEMORYFRAME_SBIN:-}" ] || [ -n "${MEMORYFRAME_SYSTEMD_DIR:-}" ] || [ -n "${MEMORYFRAME_CHROMIUM_POLICY_DIR:-}" ]; } && HOOKED=1
CHANGED=""
# Under the other boot-splash tests (MEMORYFRAME_BOOT_DIR set) without our own hooks: never touch the real /etc.
if [ -z "$HOOKED" ] && [ -n "${MEMORYFRAME_BOOT_DIR:-}" ] && [ "${1:-}" != status ]; then
    echo "boot-handover: test mode (MEMORYFRAME_BOOT_DIR set, no handover hooks): skipped"; exit 0
fi
if [ -n "$HOOKED" ] && { [ -z "${MEMORYFRAME_SBIN:-}" ] || [ -z "${MEMORYFRAME_SYSTEMD_DIR:-}" ] || [ -z "${MEMORYFRAME_CHROMIUM_POLICY_DIR:-}" ]; }; then
    echo "boot-handover: set all three test hooks (MEMORYFRAME_SBIN, MEMORYFRAME_SYSTEMD_DIR, MEMORYFRAME_CHROMIUM_POLICY_DIR) or none" >&2; exit 2
fi

usage() { sed -n '2,24p' "$0" >&2; exit 2; }
need_root() {
    if [ "$(id -u)" != 0 ] && [ -z "$HOOKED" ]; then echo "boot-handover: run with sudo: $0 $*" >&2; exit 1; fi
}

dropin_text() {
    printf '# %s (#162): before lightdm starts, ease the Plymouth dot to rest and quit Plymouth with --retain-splash.\n' "$MARK"
    printf '# The "-" means a failure never stops lightdm. Undo: sudo deploy/boot-handover.sh off (or boot-splash.sh off, uninstall.sh).\n'
    printf '[Service]\nExecStartPre=-%s\n' "$HANDOFF"
}
policy_text() {
    printf '{\n  "BrowserThemeColor": "#0E1311",\n  "BrowserColorScheme": "dark"\n}\n'
}

# put_text NAME DEST MODE: DEST gets the text on stdin unless it already holds exactly that.
put_text() {
    local name="$1" dest="$2" mode="$3" tmp
    mkdir -p "$(dirname "$dest")" || { echo "boot-handover: cannot create $(dirname "$dest")" >&2; return 1; }
    tmp="$(mktemp "$dest.XXXXXX")" || return 1
    cat > "$tmp"
    chmod "$mode" "$tmp"
    if [ -f "$dest" ] && cmp -s "$tmp" "$dest"; then rm -f "$tmp"; echo "boot-handover: $name already set up ($dest)"; return 0; fi
    mv -f "$tmp" "$dest" || { rm -f "$tmp"; return 1; }
    CHANGED=1; echo "boot-handover: $name written ($dest)"
}

reload() {
    [ -n "$CHANGED" ] || return 0
    if [ -n "$HOOKED" ] || [ "${MEMORYFRAME_NO_SYSTEMCTL:-}" = 1 ] || ! command -v systemctl >/dev/null 2>&1; then return 0; fi
    systemctl daemon-reload 2>/dev/null || echo "boot-handover: WARNING: systemctl daemon-reload failed (it applies at the next boot)" >&2
}

# drop_if_ours FILE TEXT_FUNCTION: remove FILE if it is exactly what we write (or, for the drop-in, carries our mark).
drop_if_ours() {
    local f="$1" fn="$2"
    [ -e "$f" ] || { echo "boot-handover: $f not present"; return 0; }
    if "$fn" | cmp -s - "$f" || grep -qF "$MARK" "$f" 2>/dev/null; then
        rm -f "$f" && CHANGED=1 && echo "boot-handover: removed $f"
    else
        echo "boot-handover: $f was edited since: kept (delete it by hand to undo)"
    fi
}

swaybg_note() {
    if command -v swaybg >/dev/null 2>&1; then echo "boot-handover: swaybg: installed"
    else echo "boot-handover: WARNING: swaybg is not installed: no still splash between Plymouth and Chromium (sudo apt-get install swaybg)" >&2; fi
}

case "${1:-}" in
    on)
        need_root "$@"
        rc=0
        [ -f "$HERE/plymouth-handoff.sh" ] || { echo "boot-handover: $HERE/plymouth-handoff.sh missing" >&2; exit 1; }
        put_text "handoff script" "$HANDOFF" 0755 < "$HERE/plymouth-handoff.sh" || rc=1
        dropin_text | put_text "lightdm drop-in" "$DROPIN" 0644 || rc=1
        policy_text | put_text "Chromium policy" "$POLICY_FILE" 0644 || rc=1
        reload
        swaybg_note
        exit "$rc" ;;
    off)
        need_root "$@"
        drop_if_ours "$DROPIN" dropin_text
        rmdir "$DROPDIR" 2>/dev/null || true
        drop_if_ours "$POLICY_FILE" policy_text
        if [ -e "$HANDOFF" ]; then
            if cmp -s "$HERE/plymouth-handoff.sh" "$HANDOFF" || grep -qF "memoryframe-plymouth-handoff" "$HANDOFF"; then
                rm -f "$HANDOFF" && CHANGED=1 && echo "boot-handover: removed $HANDOFF"
            else echo "boot-handover: $HANDOFF is not ours: kept"; fi
        else echo "boot-handover: $HANDOFF not present"; fi
        reload ;;
    status)
        for f in "$HANDOFF" "$DROPIN" "$POLICY_FILE"; do
            if [ -e "$f" ]; then echo "$f: present"; else echo "$f: absent"; fi
        done
        swaybg_note ;;
    *) usage ;;
esac
