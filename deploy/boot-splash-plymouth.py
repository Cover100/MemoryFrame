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
     theme in it), then the theme dir and our backups go. It also rebuilds when the theme is already the previous
     one but the initramfs still lists ours (a retried off). Like `on`, it backs up the initramfs and plymouthd.conf
     first; if the rebuild fails both are put back (our theme stays), the state is kept, exit 1.
status: one line per fact.

Transactions (#168 review M1/S1/N5): before the first change a *pending* record goes into the state file (backup
name and path, the initramfs sha256 and kernel it belongs to, the conf backup, the theme before the run, the old
theme dir); an unwritable state dir aborts before anything changes. SIGINT, SIGTERM and SIGHUP raise inside the run,
so the rollback runs (signals are ignored while it does). The rollback tries every step (initramfs, theme files,
theme, plymouthd.conf), never raises, keeps the backups if anything is left, and prints what is left and how to fix
it from a PC. A pending record found by the next `on` or `off` is rolled back first. Every initramfs write of ours
(backups and restores) goes to a .new file, fsync, rename: a cut leaves the old or the new file, never a short one;
leftover .new files and temp dirs are removed. A backup is restored only for the kernel it was taken from.
plymouthd.conf is restored byte for byte on `off` only if it is still as `on` left it (later edits are kept; the
theme line is set either way).

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
import signal
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


class Interrupted(Exception):
    """SIGINT, SIGTERM or SIGHUP during a run: roll back, exit 130."""


SIGNALS = (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)


def _raise(signum, frame):
    raise Interrupted(signal.Signals(signum).name)


def catch_signals():
    for s_ in SIGNALS:
        signal.signal(s_, _raise)


def hold_signals():
    """While the rollback or the final state write runs: a second Ctrl-C or a hang-up must not cut it short."""
    for s_ in SIGNALS:
        signal.signal(s_, signal.SIG_IGN)


def _out(stream, line):
    try:
        print(line, file=stream, flush=True)
    except (OSError, ValueError):           # the ssh session is gone (SIGHUP): keep going without output
        pass


def say(msg):
    _out(sys.stdout, f"boot-splash: plymouth: {msg}")


def warn(msg):
    _out(sys.stderr, f"boot-splash: plymouth: WARNING: {msg}")


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
    """src -> dst via .<dst>.new in dst's directory, fsync, rename (the boot partition is FAT): dst is the old file or
    the complete new one, never a short one. The .new file goes on any failure or signal."""
    tmp = dst.with_name("." + dst.name + ".new")
    try:
        with open(src, "rb") as i, open(tmp, "wb") as o:
            shutil.copyfileobj(i, o, 1 << 20)
            o.flush()
            os.fsync(o.fileno())
        os.replace(tmp, dst)
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise
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



# ---------------- state ----------------

RUN_KEYS = ("plymouth_pending", "plymouth_run_initramfs", "plymouth_run_kernel", "plymouth_run_sha",
            "plymouth_run_bak", "plymouth_run_bak_path", "plymouth_run_prev", "plymouth_run_old_dir",
            "plymouth_run_conf_bak", "plymouth_run_first")


def kernel_now():
    return os.environ.get("MEMORYFRAME_KERNEL") or os.uname().release


def ours(st):
    return "plymouth_theme_sha" in st


def save(st):
    """write_state, and raise OSError if it could not be written (the caller decides what that means)."""
    write_state(STATE, st)


def drop_run(st):
    for k in RUN_KEYS:
        st.pop(k, None)


def forget(st):
    for k in [k for k in st if k.startswith("plymouth_")]:
        del st[k]


def unlink(path):
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def clean_leftovers(st):
    """.new files of an interrupted copy, temp theme dirs, old theme dirs no pending run refers to."""
    keep = st.get("plymouth_run_old_dir")
    pats = [(BOOT, ".initramfs*.new"), (CONF.parent, f".{CONF.name}*.new")]
    for d, pat in pats:
        try:
            for p in d.glob(pat):
                unlink(p)
                say(f"removed the leftover {p.name}")
        except OSError:
            pass
    try:
        for p in THEMES.glob(f".{THEME}*"):
            if p.is_dir() and p.name != keep:
                shutil.rmtree(p, ignore_errors=True)
    except OSError:
        pass


# ---------------- conf ----------------

def restore_conf_exact(name):
    """plymouthd.conf back to the bytes of backup `name` ("none": it did not exist). Atomic."""
    if name == "none":
        unlink(CONF)
    elif name:
        b = CONF.with_name(name)
        if not b.is_file():
            raise Fail(f"the plymouthd.conf backup {name} is missing")
        if not CONF.exists() or CONF.read_bytes() != b.read_bytes():
            copy_synced(b, CONF)


def conf_sha():
    return sha256(CONF) if CONF.exists() else "none"


def backup_conf():
    if not CONF.exists():
        return "none"
    b = fresh(CONF.with_name(f"{CONF.name}.memoryframe-bak.{STAMP}"))
    copy_synced(CONF, b)
    return b.name


# ---------------- rollback ----------------

def rollback(st, why):
    """Undo the pending run in `st` (the run_* keys). Tries every step, never raises. Returns True when everything
    is as it was before the run (then the run's backups and keys go), else False (backups and keys stay)."""
    hold_signals()
    warn(f"{why}; rolling back")
    left = []
    kernel = kernel_now()
    initrd = BOOT / st.get("plymouth_run_initramfs", "initramfs8")
    want = st.get("plymouth_run_sha", "")
    bak = BOOT / st.get("plymouth_run_bak", "")
    bak_kernel = st.get("plymouth_run_kernel", "")
    # 1. the initramfs
    try:
        if initrd.is_file() and sha256(initrd) == want:
            pass                                             # never changed
        elif bak_kernel != kernel:
            left.append(f"{initrd.name}: the backup {bak.name} is for kernel {bak_kernel}, not {kernel}: not copied")
        elif not (st.get("plymouth_run_bak") and bak.is_file() and sha256(bak) == want):
            left.append(f"{initrd.name}: no intact backup to put back ({bak.name or 'none'})")
        else:
            copy_synced(bak, initrd)
            if sha256(initrd) != want:
                left.append(f"{initrd.name} does not match its backup {bak.name} after the copy")
            else:
                say(f"{initrd.name} restored from {bak.name}")
    except (OSError, Fail) as e:
        left.append(f"{initrd.name} not restored: {e}")
    # 2. the theme files
    old = st.get("plymouth_run_old_dir", "keep")
    dest = THEMES / THEME
    try:
        if old == "none":
            if dest.exists():
                shutil.rmtree(dest)
        elif old != "keep":
            o = THEMES / old
            if o.is_dir():                                   # we had moved the old files aside: put them back
                if dest.exists():
                    shutil.rmtree(dest)
                os.replace(o, dest)
    except OSError as e:
        left.append(f"theme files in {dest}: {e}")
    # 3. the theme
    prev = st.get("plymouth_run_prev") or st.get("plymouth_prev_theme") or "pix"
    try:
        if current_theme() != prev:
            set_theme(prev)
    except (Fail, OSError) as e:
        left.append(f"default theme: {e}")
    # 4. plymouthd.conf: the run's own backup, byte for byte. It is taken before the run changes the conf, so a
    #    missing backup means the conf was never changed.
    cb = st.get("plymouth_run_conf_bak")
    try:
        if cb == "none" or (cb and CONF.with_name(cb).is_file()):
            restore_conf_exact(cb)
    except (Fail, OSError) as e:
        left.append(f"plymouthd.conf: {e}")
    if left:
        for l in left:
            warn("left: " + l)
        if st.get("plymouth_run_bak") and bak.is_file():
            warn(f"if the frame does not boot: from a PC, copy {bak.name} over {initrd.name} on the boot partition "
                 f"(it is the {initrd.name} from before this run, kernel {bak_kernel})")
        elif st.get("plymouth_initramfs_bak") and (BOOT / st["plymouth_initramfs_bak"]).is_file():
            warn(f"if the frame does not boot: from a PC, copy {st['plymouth_initramfs_bak']} over {initrd.name} "
                 f"(the one from before Memory Frame, kernel {st.get('plymouth_bak_kernel', '?')})")
        else:
            warn(f"if the frame does not boot: from a PC, put # before auto_initramfs=1 in config.txt "
                 f"(see README, boot splash recovery)")
        warn("the backups and the pending record are kept; run `sudo deploy/boot-splash.sh plymouth off` again")
        try:
            save(st)
        except OSError as e:
            warn(f"the state file could not be written: {e}")
        return False
    # all back: the run's own backups go (never the first, pre-Memory-Frame one)
    if st.get("plymouth_run_bak") and st.get("plymouth_run_bak") != st.get("plymouth_initramfs_bak"):
        unlink(bak)
    if cb and cb != "none" and cb != st.get("plymouth_conf_bak"):
        unlink(CONF.with_name(cb))
    first = st.get("plymouth_run_first") == "1"
    drop_run(st)
    if first:
        forget(st)
    try:
        save(st)
    except OSError as e:
        warn(f"the state file could not be written: {e}")
        return False
    say(f"rolled back: theme {prev!r}, {initrd.name} as before")
    return True


def recover(st):
    """A pending record from an interrupted run (power cut, kill -9): roll it back before anything else."""
    if "plymouth_pending" not in st:
        return True
    say(f"an interrupted `plymouth {st['plymouth_pending']}` was found: undoing it first")
    ok = rollback(st, "interrupted run")
    catch_signals()
    return ok


def begin(st, kind, initrd, kernel, prev, old_dir, conf_bak_planned, first):
    """Record the run before the first change. OSError = the state dir is not writable: nothing changed."""
    before = sha256(initrd)
    bak = fresh(initrd.with_name(f"{initrd.name}.memoryframe-bak.{STAMP}"))
    st.update(plymouth_pending=kind, plymouth_run_initramfs=initrd.name, plymouth_run_kernel=kernel,
              plymouth_run_sha=before, plymouth_run_bak=bak.name, plymouth_run_bak_path=str(bak),
              plymouth_run_prev=prev, plymouth_run_old_dir=old_dir, plymouth_run_conf_bak=conf_bak_planned,
              plymouth_run_first="1" if first else "0")
    save(st)
    return before, bak


def make_backup(initrd, bak, before):
    copy_synced(initrd, bak)
    if sha256(bak) != before:
        raise Fail(f"the backup of {initrd.name} does not match it")
    say(f"backup {bak.name} (sha256 {before[:12]})")


def install_theme(st, dest):
    """New files in a temp dir; the old dir (if any) is renamed aside under the name the pending record holds."""
    tmp = Path(tempfile.mkdtemp(prefix=f".{THEME}.new.", dir=THEMES))
    for name in FILES:
        shutil.copyfile(SRC / name, tmp / name)
        os.chmod(tmp / name, 0o644)
    os.chmod(tmp, 0o755)
    old = st.get("plymouth_run_old_dir")
    if dest.exists() and old not in ("none", "keep"):
        os.replace(dest, THEMES / old)
    elif dest.exists():
        shutil.rmtree(dest)
    os.replace(tmp, dest)


def rebuild(kernel):
    r = run([UPDATE_INITRAMFS, "-u", "-k", kernel])
    if r.returncode != 0:
        raise Fail(f"update-initramfs failed: {(r.stderr or r.stdout).strip()[-300:]}")


# ---------------- preflight ----------------

def preflight(for_off=False):
    """None when everything is in place, else a refusal reason."""
    if not shutil.which(PLYMOUTH_SET) or not shutil.which(UPDATE_INITRAMFS) or not shutil.which(LSINITRAMFS):
        return "Plymouth or initramfs-tools is not installed"
    if os.geteuid() != 0 and not os.environ.get("MEMORYFRAME_PLYMOUTH_DIR"):
        return "not root"
    kernel = kernel_now()
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


# ---------------- on ----------------

def pending_refused(why):
    """A refusal while a pending record exists (#168 delta: kill -9, then e.g. a kernel or config change): nothing is
    touched, but say so and exit 1 instead of a quiet skip, with the PC hint. None when there is no pending record."""
    try:
        st = read_state(STATE)
    except OSError:
        return None
    if "plymouth_pending" not in st:
        return None
    warn(f"an interrupted `plymouth {st['plymouth_pending']}` is pending but cannot be undone now: {why}; nothing "
         "changed, the state and backups are kept. Fix that, then run `sudo deploy/boot-splash.sh plymouth off`")
    bak, name = st.get("plymouth_run_bak"), st.get("plymouth_run_initramfs") or "initramfs8"
    if bak and (BOOT / bak).is_file():
        warn(f"if the frame does not boot: from a PC, copy {bak} over {name} on the boot partition "
             f"(kernel {st.get('plymouth_run_kernel', '?')})")
    else:
        warn("if the frame does not boot: from a PC, put # before auto_initramfs=1 in config.txt "
             "(see README, boot splash recovery)")
    return 1


def cmd_on():
    if os.environ.get("MEMORYFRAME_NO_PLYMOUTH") == "1":
        say("skipped (MEMORYFRAME_NO_PLYMOUTH=1)")
        return 0
    for name in FILES:
        if not (SRC / name).is_file():
            warn(f"theme file missing: {SRC / name}")
            return 1
    why = preflight()
    if why:
        if pending_refused(why):
            return 1
        say(f"skipped: {why}; the boot shows the current Plymouth theme")
        return 0
    st = read_state(STATE)
    if not recover(st):
        return 1
    clean_leftovers(st)
    if not has_splash():
        say("note: cmdline.txt has no `splash`, so Plymouth does not show at boot (not added)")
    kernel = kernel_now()
    initrd = initrd_for(kernel)
    want = theme_sha(SRC)
    cur = current_theme()
    dest = THEMES / THEME
    if cur is None:
        say("skipped: plymouth-set-default-theme does not answer")
        return 0
    if ours(st) and cur != THEME:
        say(f"skipped: the theme is now {cur!r}, not ours (someone changed it): left alone; "
            "`sudo deploy/boot-splash.sh plymouth off` forgets ours")
        return 0
    if not ours(st) and (cur == THEME or dest.exists()):
        say(f"skipped: a {THEME} theme is already there and was not installed by this script: left alone")
        return 0
    if ours(st) and st.get("plymouth_theme_sha") == want and dest.is_dir() and theme_sha(dest) == want:
        if has_theme(listing(initrd), THEME):
            say(f"already set up ({THEME} theme in {initrd.name}): no rebuild")
            return 0
        say(f"{initrd.name} no longer lists the theme (kernel update?): rebuilding")

    first = not ours(st)
    before_size = initrd.stat().st_size
    mods = modules(listing(initrd))
    old_dir = f".{THEME}-old.{STAMP}.{os.getpid()}" if dest.exists() else "none"
    conf_planned = "" if not first else ("none" if not CONF.exists()
                                         else fresh(CONF.with_name(f"{CONF.name}.memoryframe-bak.{STAMP}")).name)
    try:
        before, bak = begin(st, "on", initrd, kernel, cur, old_dir, conf_planned, first)
    except OSError as e:
        warn(f"the state file {STATE} cannot be written ({e}): nothing changed")
        return 1
    try:
        make_backup(initrd, bak, before)
        if conf_planned and conf_planned != "none":
            copy_synced(CONF, CONF.with_name(conf_planned))
        install_theme(st, dest)
        set_theme(THEME)
        rebuild(kernel)
        after_sha = verify(initrd, before, before_size, mods, THEME)
        hold_signals()
        done = dict(st)
        if first:
            done.update(plymouth_initramfs_bak=bak.name, plymouth_bak_path=str(bak), plymouth_sha_before=before,
                        plymouth_bak_kernel=kernel, plymouth_conf_bak=conf_planned, plymouth_prev_theme=cur)
        done.update(plymouth_theme_sha=want, plymouth_initramfs=initrd.name, plymouth_kernel=kernel,
                    plymouth_sha_after=after_sha, plymouth_conf_sha_after=conf_sha())
        drop_run(done)
        save(done)                                       # the run is complete only once this is on disk
    except (Fail, OSError, Interrupted) as e:
        rollback(st, str(e) if not isinstance(e, Interrupted) else f"interrupted ({e})")
        return 130 if isinstance(e, Interrupted) else 1
    if not first:
        unlink(bak)                                      # the pre-Memory-Frame backup stays the one to fall back to
    old = THEMES / old_dir
    if old_dir != "none" and old.exists():
        shutil.rmtree(old, ignore_errors=True)
    say(f"{THEME} theme set; {initrd.name} rebuilt (sha256 {after_sha[:12]}); applies at the next boot. "
        f"Undo: sudo deploy/boot-splash.sh plymouth off")
    return 0


# ---------------- off ----------------

def cmd_off():
    st = read_state(STATE)
    if not ours(st) and "plymouth_pending" not in st:
        say("no Memory Frame theme of ours to undo")
        return 0
    why = preflight(for_off=True)
    if why:
        if not pending_refused(why):
            warn(f"cannot undo now: {why}; the state is kept")
        return 1
    if not recover(st):
        return 1
    clean_leftovers(st)
    if not ours(st):
        say("no Memory Frame theme of ours left to undo")
        return 0
    kernel = kernel_now()
    initrd = initrd_for(kernel)
    prev = st.get("plymouth_prev_theme") or "pix"
    dest = THEMES / THEME
    cur = current_theme()
    if cur is None:
        warn("plymouth-set-default-theme does not answer: nothing changed; the state is kept")
        return 1
    lines = listing(initrd)
    if cur != THEME and not has_theme(lines, THEME):
        say(f"the theme is {cur!r}, not ours, and {initrd.name} has none of ours: no rebuild; "
            "removing our theme files and backups")
    else:
        if cur != THEME:
            say(f"the theme is already {cur!r} but {initrd.name} still lists ours (a retried off?): rebuilding")
        target = prev if cur == THEME else cur
        if sha256(initrd) != st.get("plymouth_sha_after"):
            say(f"{initrd.name} changed since our build (kernel update?): rebuilding it anyway")
        before_size = initrd.stat().st_size
        mods = modules(lines)
        conf_planned = "none" if not CONF.exists() else fresh(CONF.with_name(f"{CONF.name}.memoryframe-bak.{STAMP}")).name
        try:
            before, bak = begin(st, "off", initrd, kernel, cur, "keep", conf_planned, False)
        except OSError as e:
            warn(f"the state file {STATE} cannot be written ({e}): nothing changed")
            return 1
        try:
            make_backup(initrd, bak, before)
            if conf_planned != "none":
                copy_synced(CONF, CONF.with_name(conf_planned))
            untouched = conf_sha() == st.get("plymouth_conf_sha_after")
            set_theme(target)
            if target == prev and untouched:
                restore_conf_exact(st.get("plymouth_conf_bak"))      # untouched since `on`: byte for byte
            elif target == prev:
                say("plymouthd.conf was edited after `on`: kept (only the theme line is back)")
            rebuild(kernel)
            verify(initrd, before, before_size, mods, None, absent=THEME)
        except (Fail, OSError, Interrupted) as e:
            rollback(st, str(e) if not isinstance(e, Interrupted) else f"interrupted ({e})")
            warn("state kept; run `sudo deploy/boot-splash.sh plymouth off` again")
            return 130 if isinstance(e, Interrupted) else 1
        hold_signals()
        unlink(bak)
        if conf_planned != "none":
            unlink(CONF.with_name(conf_planned))
        say(f"theme {target!r} back; {initrd.name} rebuilt without the Memory Frame theme")
    hold_signals()
    shutil.rmtree(dest, ignore_errors=True)
    if st.get("plymouth_initramfs_bak"):
        unlink(BOOT / st["plymouth_initramfs_bak"])
    cb = st.get("plymouth_conf_bak")
    if cb and cb != "none":
        unlink(CONF.with_name(cb))
    drop_run(st)
    forget(st)
    try:
        save(st)
    except OSError as e:
        warn(f"the state file could not be written: {e}")
        return 1
    return 0


def cmd_status():
    st = read_state(STATE)
    print(f"plymouth theme: {current_theme() or 'unknown'}"
          f"{' (ours since ' + st['plymouth_initramfs'] + ' build)' if ours(st) else ''}")
    if st.get("plymouth_initramfs_bak"):
        print(f"plymouth backup: {st['plymouth_initramfs_bak']} (kernel {st.get('plymouth_bak_kernel', '?')})")
    if "plymouth_pending" in st:
        print(f"plymouth: an interrupted `{st['plymouth_pending']}` is pending (the next on/off undoes it)")
    print(f"cmdline splash: {'yes' if has_splash() else 'no (Plymouth does not show)'}")
    return 0


def main(argv):
    cmds = {"on": cmd_on, "off": cmd_off, "status": cmd_status}
    if len(argv) != 2 or argv[1] not in cmds:
        print(__doc__, file=sys.stderr)
        return 2
    if argv[1] != "status":
        catch_signals()
    try:
        return cmds[argv[1]]()
    except Interrupted as e:                  # before the pending record: nothing to undo
        hold_signals()
        warn(f"interrupted ({e}) before any change")
        return 130
    except (Fail, OSError) as e:              # before the pending record (a read, lsinitramfs): nothing changed
        warn(f"{e}: nothing changed")
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
