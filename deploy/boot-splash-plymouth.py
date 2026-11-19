#!/usr/bin/env python3
"""#162 Change 2: the Memory Frame Plymouth theme. Run as root by `deploy/boot-splash.sh plymouth on|off|status`
(install.sh runs `on`, uninstall.sh runs `off` through `boot-splash.sh off`). Never reboots.

on:  installs deploy/plymouth/memoryframe/ to /usr/share/plymouth/themes/memoryframe (0644), makes it the default
     theme (plymouth-set-default-theme) and rebuilds the running kernel's initramfs (update-initramfs -u -k <ver>),
     which the firmware loads at boot (config.txt auto_initramfs=1: initramfs8 for -v8, initramfs_2712 for -2712).
     Hash guard: the theme files' sha256, the theme already active and listed in the initramfs = nothing to do (no
     SD write on a normal deploy, #97). Before a rebuild: sha256 of the initramfs, a timestamped copy next to it
     (<name>.memoryframe-bak.<ts>, re-hashed), a copy of /etc/plymouth/plymouthd.conf, the module list. After it:
     the initramfs must have changed, list themes/memoryframe/memoryframe.script and script.so, be within 30 % of
     the old size and keep every module. Any failure puts the old initramfs back (hash checked), the previous theme
     and plymouthd.conf back, and exits 1. Refusals (exit 0, nothing written, a plain line): Plymouth not
     installed, an unknown kernel flavour, no initramfs file, no auto_initramfs=1 in config.txt, less than 3x the
     initramfs size free on the boot partition, or a theme someone else chose since our last run.
off: the previous theme and plymouthd.conf back, the initramfs rebuilt and checked the same way (no memoryframe
     theme in it), then the theme dir and our backups go. If that rebuild fails, the backup is copied back (only for
     the same kernel; hash checked) and the state is kept, exit 1.
status: one line per fact.

cmdline.txt is not touched: Hale's line already has `splash` (Plymouth on); without it Plymouth does not show and we
only say so. Never reads or writes the #94 boot mode file or any network profile.
State: /etc/memoryframe/boot-splash.state (shared with boot-splash-edit.py; plymouth_* keys).
Test hooks: MEMORYFRAME_BOOT_DIR, MEMORYFRAME_BOOT_STATE, MEMORYFRAME_PLYMOUTH_DIR (themes root),
MEMORYFRAME_PLYMOUTH_CONF, MEMORYFRAME_KERNEL (uname -r), MEMORYFRAME_BOOT_FREE (bytes free), PLYMOUTH_SET,
UPDATE_INITRAMFS, LSINITRAMFS."""
import hashlib
import importlib.util
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
THEME = "memoryframe"
SRC = HERE / "plymouth" / THEME
FILES = ("memoryframe.plymouth", "memoryframe.script", "lockup.png", "error.png")
FLAVOURS = (("-v8", "initramfs8"), ("-2712", "initramfs_2712"), ("-v7l", "initramfs7l"), ("-v7", "initramfs7"))
SIZE_SLACK = 0.30
FREE_FACTOR = 3

_spec = importlib.util.spec_from_file_location("bse", HERE / "boot-splash-edit.py")
_bse = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_bse)
read_state, write_state = _bse.read_state, _bse.write_state

BOOT = Path(os.environ.get("MEMORYFRAME_BOOT_DIR") or "/boot/firmware")
STATE = os.environ.get("MEMORYFRAME_BOOT_STATE") or "/etc/memoryframe/boot-splash.state"
THEMES = Path(os.environ.get("MEMORYFRAME_PLYMOUTH_DIR") or "/usr/share/plymouth/themes")
CONF = Path(os.environ.get("MEMORYFRAME_PLYMOUTH_CONF") or "/etc/plymouth/plymouthd.conf")
PLYMOUTH_SET = os.environ.get("PLYMOUTH_SET") or "plymouth-set-default-theme"
UPDATE_INITRAMFS = os.environ.get("UPDATE_INITRAMFS") or "update-initramfs"
LSINITRAMFS = os.environ.get("LSINITRAMFS") or "lsinitramfs"
STAMP = time.strftime("%Y%m%d%H%M%S")
MODULE_RE = re.compile(r"/([^/]+)\.ko(?:\.(?:zst|xz|gz))?$")


class Fail(Exception):
    """The run tried something and it failed: exit 1 after rolling back."""


def say(msg):
    print(f"boot-splash: plymouth: {msg}", flush=True)


def warn(msg):
    print(f"boot-splash: plymouth: WARNING: {msg}", file=sys.stderr, flush=True)


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def theme_sha(root):
    h = hashlib.sha256()
    for name in FILES:
        h.update(name.encode() + b"\0" + (root / name).read_bytes() + b"\0")
    return h.hexdigest()


def run(argv):
    try:
        return subprocess.run(argv, capture_output=True, text=True, timeout=900)
    except (OSError, subprocess.TimeoutExpired) as e:
        return subprocess.CompletedProcess(argv, 127, "", str(e))


def current_theme():
    r = run([PLYMOUTH_SET])
    return r.stdout.strip() if r.returncode == 0 else None


def set_theme(name):
    r = run([PLYMOUTH_SET, name])
    if r.returncode != 0 or current_theme() != name:
        raise Fail(f"plymouth-set-default-theme {name} failed: {(r.stderr or r.stdout).strip()[:200]}")


def listing(initrd):
    r = run([LSINITRAMFS, str(initrd)])
    if r.returncode != 0:
        raise Fail(f"lsinitramfs {initrd.name} failed")
    return r.stdout.splitlines()


def modules(lines):
    return {m.group(1) for m in map(MODULE_RE.search, lines) if m}


def has_theme(lines, theme):
    return any(l.endswith(f"plymouth/themes/{theme}/{theme}.script") or
               l.endswith(f"plymouth/themes/{theme}/{theme}.plymouth") for l in lines)


def fsync_dir(d):
    try:
        fd = os.open(d, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError:
        pass


def copy_synced(src, dst):
    """src -> dst via a temp file in dst's directory, fsync, rename (the boot partition is FAT)."""
    tmp = dst.with_name("." + dst.name + ".new")
    with open(src, "rb") as i, open(tmp, "wb") as o:
        shutil.copyfileobj(i, o, 1 << 20)
        o.flush()
        os.fsync(o.fileno())
    os.replace(tmp, dst)
    fsync_dir(dst.parent)


def fresh(path):
    """path, or path-1, path-2...: a backup never overwrites another one (two runs in the same second)."""
    n, p = 0, path
    while p.exists():
        n += 1
        p = path.with_name(f"{path.name}-{n}")
    return p


def initrd_for(kernel):
    for suffix, name in FLAVOURS:
        if kernel.endswith(suffix):
            return BOOT / name
    return None


def auto_initramfs():
    try:
        text = (BOOT / "config.txt").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return any(re.fullmatch(r"\s*auto_initramfs\s*=\s*1\s*", l) for l in text.splitlines())


def has_splash():
    try:
        return "splash" in (BOOT / "cmdline.txt").read_text(encoding="utf-8", errors="replace").split()
    except OSError:
        return False


def free_bytes():
    if os.environ.get("MEMORYFRAME_BOOT_FREE"):
        return int(os.environ["MEMORYFRAME_BOOT_FREE"])
    return shutil.disk_usage(BOOT).free


def verify(initrd, before_sha, before_size, mods, theme, absent=None):
    after = sha256(initrd)
    if after == before_sha:
        raise Fail(f"{initrd.name} did not change: the rebuild did nothing")
    size = initrd.stat().st_size
    if abs(size - before_size) > SIZE_SLACK * before_size:
        raise Fail(f"{initrd.name} is {size} bytes, was {before_size} (more than 30 % off)")
    lines = listing(initrd)
    if theme and not has_theme(lines, theme):
        raise Fail(f"{initrd.name} does not list the {theme} theme")
    if absent and has_theme(lines, absent):
        raise Fail(f"{initrd.name} still lists the {absent} theme")
    if theme == THEME and not any(l.endswith("plymouth/script.so") for l in lines):
        raise Fail(f"{initrd.name} has no plymouth script.so")
    lost = sorted(mods - modules(lines))
    if lost:
        raise Fail(f"{initrd.name} lost modules: {', '.join(lost[:8])}")
    return after


def restore_conf(conf_bak):
    if conf_bak == "none":
        if CONF.exists():
            CONF.unlink()
    elif conf_bak:
        b = CONF.with_name(conf_bak)
        if b.exists() and (not CONF.exists() or CONF.read_bytes() != b.read_bytes()):
            shutil.copy2(b, CONF)


def put_back(initrd, bak, want_sha):
    copy_synced(bak, initrd)
    if sha256(initrd) != want_sha:
        warn(f"{initrd.name} does not match its backup after the copy: from a PC copy {bak.name} over {initrd.name}")
        return False
    say(f"{initrd.name} restored from {bak.name}")
    return True


def install_theme(dest):
    tmp = Path(tempfile.mkdtemp(prefix=".memoryframe.", dir=dest.parent))
    for name in FILES:
        shutil.copyfile(SRC / name, tmp / name)
        os.chmod(tmp / name, 0o644)
    os.chmod(tmp, 0o755)
    if dest.exists():
        shutil.rmtree(dest)
    os.replace(tmp, dest)


def preflight(st, for_off=False):
    """None when everything is in place, else a refusal reason."""
    if not shutil.which(PLYMOUTH_SET) or not shutil.which(UPDATE_INITRAMFS) or not shutil.which(LSINITRAMFS):
        return "Plymouth or initramfs-tools is not installed"
    if os.geteuid() != 0 and not os.environ.get("MEMORYFRAME_PLYMOUTH_DIR"):
        return "not root"
    kernel = os.environ.get("MEMORYFRAME_KERNEL") or os.uname().release
    initrd = initrd_for(kernel)
    if initrd is None:
        return f"unknown kernel flavour {kernel!r} (no initramfs name for it)"
    if not initrd.is_file():
        return f"no {initrd} (nothing for the firmware to load)"
    if not for_off and not auto_initramfs():
        return "config.txt has no auto_initramfs=1: the firmware would not load the rebuilt initramfs"
    if free_bytes() < FREE_FACTOR * initrd.stat().st_size:
        return f"less than {FREE_FACTOR}x the size of {initrd.name} free on {BOOT}"
    return None


def cmd_on():
    st = read_state(STATE)
    if os.environ.get("MEMORYFRAME_NO_PLYMOUTH") == "1":
        say("skipped (MEMORYFRAME_NO_PLYMOUTH=1)")
        return 0
    for name in FILES:
        if not (SRC / name).is_file():
            warn(f"theme file missing: {SRC / name}")
            return 1
    why = preflight(st)
    if why:
        say(f"skipped: {why}; the boot shows the current Plymouth theme")
        return 0
    if not has_splash():
        say("note: cmdline.txt has no `splash`, so Plymouth does not show at boot (not added)")
    kernel = os.environ.get("MEMORYFRAME_KERNEL") or os.uname().release
    initrd = initrd_for(kernel)
    want = theme_sha(SRC)
    cur = current_theme()
    dest = THEMES / THEME
    ours = "plymouth_theme_sha" in st
    if cur is None:
        say("skipped: plymouth-set-default-theme does not answer")
        return 0
    if ours and cur != THEME:
        say(f"skipped: the theme is now {cur!r}, not ours (someone changed it): left alone; "
            "`sudo deploy/boot-splash.sh plymouth off` forgets ours")
        return 0
    if not ours and (cur == THEME or dest.exists()):
        say(f"skipped: a {THEME} theme is already there and was not installed by this script: left alone")
        return 0
    if ours and st.get("plymouth_theme_sha") == want and dest.is_dir() and theme_sha(dest) == want:
        if has_theme(listing(initrd), THEME):
            say(f"already set up ({THEME} theme in {initrd.name}): no rebuild")
            return 0
        say(f"{initrd.name} no longer lists the theme (kernel update?): rebuilding")

    before_sha = sha256(initrd)
    before_size = initrd.stat().st_size
    mods = modules(listing(initrd))
    bak = fresh(initrd.with_name(f"{initrd.name}.memoryframe-bak.{STAMP}"))
    copy_synced(initrd, bak)
    if sha256(bak) != before_sha:
        bak.unlink()
        warn(f"the backup of {initrd.name} does not match it: nothing changed")
        return 1
    say(f"backup {bak.name} (sha256 {before_sha[:12]})")
    prev = st.get("plymouth_prev_theme") or cur
    conf_bak = st.get("plymouth_conf_bak")
    new_conf_bak = None
    if not conf_bak:
        if CONF.exists():
            new_conf_bak = fresh(CONF.with_name(f"{CONF.name}.memoryframe-bak.{STAMP}")).name
            shutil.copy2(CONF, CONF.with_name(new_conf_bak))
        else:
            new_conf_bak = "none"
    had_dir = dest.exists()
    old_dir = None
    if had_dir:
        old_dir = Path(tempfile.mkdtemp(prefix=".memoryframe-old.", dir=THEMES))
        shutil.rmtree(old_dir)
        shutil.copytree(dest, old_dir)
    try:
        install_theme(dest)
        set_theme(THEME)
        r = run([UPDATE_INITRAMFS, "-u", "-k", kernel])
        if r.returncode != 0:
            raise Fail(f"update-initramfs failed: {(r.stderr or r.stdout).strip()[-300:]}")
        after_sha = verify(initrd, before_sha, before_size, mods, THEME)
    except (Fail, OSError) as e:
        warn(f"{e}; rolling back")
        ok = put_back(initrd, bak, before_sha)
        shutil.rmtree(dest, ignore_errors=True)        # the theme files as they were before this run
        if old_dir:
            os.replace(old_dir, dest)
        try:
            set_theme(cur)                             # the theme active before this run (prev on a first run)
        except Fail as e2:
            warn(str(e2))
            ok = False
        if new_conf_bak:                               # first run: plymouthd.conf byte-identical (or absent) again
            restore_conf(new_conf_bak)
            if new_conf_bak != "none":
                CONF.with_name(new_conf_bak).unlink(missing_ok=True)
        if ok:
            bak.unlink(missing_ok=True)
            say(f"rolled back: theme {cur!r}, {initrd.name} as before")
        return 1
    if old_dir:
        shutil.rmtree(old_dir, ignore_errors=True)
    first_bak = st.get("plymouth_initramfs_bak")
    if first_bak and first_bak != bak.name and (BOOT / first_bak).exists():
        bak.unlink(missing_ok=True)                    # the pre-Memory-Frame backup stays the one to fall back to
    else:
        st["plymouth_initramfs_bak"] = bak.name
        st["plymouth_sha_before"] = before_sha
    st.update(plymouth_theme_sha=want, plymouth_prev_theme=prev, plymouth_initramfs=initrd.name,
              plymouth_kernel=kernel, plymouth_sha_after=after_sha)
    if new_conf_bak:
        st["plymouth_conf_bak"] = new_conf_bak
    write_state(STATE, st)
    say(f"{THEME} theme set; {initrd.name} rebuilt (sha256 {after_sha[:12]}); applies at the next boot. "
        f"Undo: sudo deploy/boot-splash.sh plymouth off")
    return 0


def forget(st):
    for k in [k for k in st if k.startswith("plymouth_")]:
        del st[k]
    write_state(STATE, st)


def cmd_off():
    st = read_state(STATE)
    if "plymouth_theme_sha" not in st:
        say("no Memory Frame theme of ours to undo")
        return 0
    why = preflight(st, for_off=True)
    if why:
        warn(f"cannot undo now: {why}; the state is kept")
        return 1
    kernel = os.environ.get("MEMORYFRAME_KERNEL") or os.uname().release
    initrd = initrd_for(kernel)
    prev = st.get("plymouth_prev_theme") or "pix"
    bak = BOOT / st["plymouth_initramfs_bak"] if st.get("plymouth_initramfs_bak") else None
    cur = current_theme()
    dest = THEMES / THEME
    if cur is None:
        warn("plymouth-set-default-theme does not answer: nothing changed; the state is kept")
        return 1
    if cur != THEME:
        say(f"the theme is {cur!r}, not ours: no rebuild; removing our theme files and backups")
    else:
        if sha256(initrd) != st.get("plymouth_sha_after"):
            say(f"{initrd.name} changed since our build (kernel update?): rebuilding it anyway")
        before_sha = sha256(initrd)
        before_size = initrd.stat().st_size
        mods = modules(listing(initrd))
        try:
            set_theme(prev)
            restore_conf(st.get("plymouth_conf_bak"))
            r = run([UPDATE_INITRAMFS, "-u", "-k", kernel])
            if r.returncode != 0:
                raise Fail(f"update-initramfs failed: {(r.stderr or r.stdout).strip()[-300:]}")
            verify(initrd, before_sha, before_size, mods, None, absent=THEME)
        except (Fail, OSError) as e:
            warn(f"{e}")
            if bak and bak.exists() and st.get("plymouth_kernel") == kernel:
                put_back(initrd, bak, st.get("plymouth_sha_before"))
            else:
                warn(f"no backup for kernel {kernel}: {initrd.name} left as is; run "
                     f"`sudo plymouth-set-default-theme -R {prev}` by hand")
            warn("state kept; run `sudo deploy/boot-splash.sh plymouth off` again")
            return 1
        say(f"theme {prev!r} back; {initrd.name} rebuilt without the Memory Frame theme")
    shutil.rmtree(dest, ignore_errors=True)
    if bak:
        bak.unlink(missing_ok=True)
    cb = st.get("plymouth_conf_bak")
    if cb and cb != "none":
        CONF.with_name(cb).unlink(missing_ok=True)
    forget(st)
    return 0


def cmd_status():
    st = read_state(STATE)
    print(f"plymouth theme: {current_theme() or 'unknown'}"
          f"{' (ours since ' + st['plymouth_initramfs'] + ' build)' if 'plymouth_theme_sha' in st else ''}")
    print(f"cmdline splash: {'yes' if has_splash() else 'no (Plymouth does not show)'}")
    return 0


def main(argv):
    cmds = {"on": cmd_on, "off": cmd_off, "status": cmd_status}
    if len(argv) != 2 or argv[1] not in cmds:
        print(__doc__, file=sys.stderr)
        return 2
    return cmds[argv[1]]()


if __name__ == "__main__":
    sys.exit(main(sys.argv))
