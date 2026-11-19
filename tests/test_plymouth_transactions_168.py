"""#168 review (Hale; Reed's scope): `plymouth on|off` are transactions. M1: a pending record is written before the
first change; SIGINT/SIGTERM/SIGHUP at any step roll back; a kill -9 leaves a pending record that the next `on` or
`off` undoes; an unwritable state dir aborts before any change; a failed final state write rolls back. S1: a rollback
whose own steps fail never crashes: every step is tried, the backups stay, what is left and the copy-from-PC hint are
printed, exit non-zero. N2: a retried off rebuilds while the initramfs lists ours. N4: plymouthd.conf edits made
after `on` survive `off`. N5: no traceback, no .new files, leftovers are removed. Fake tools from
test_plymouth_theme_162; signals come from wrappers around them (kill -<SIG> $PPID = the helper)."""
import importlib.util
import os
import signal
import subprocess
import sys
from pathlib import Path

import pytest

from test_plymouth_theme_162 import CONF_ORIG, KERNEL, ROOT, pl, snapshot  # noqa: F401  (fixture)

not_root = pytest.mark.skipif(os.geteuid() == 0, reason="permission tests need a non-root user")


def wrap(p, tool, script, name=None):
    """A wrapper for one fake tool: `script` runs after (or instead of, with {real}) the real fake."""
    real = p.env[tool]
    w = p.tmp / (name or f"wrap-{tool.lower()}")
    body = script.replace("{real}", real).replace("{boot}", str(p.boot)).replace("{state}", str(p.state))
    w.write_text("#!/bin/bash\n" + body + "\n")
    w.chmod(0o755)
    return {tool: str(w)}


def everything(p):
    return snapshot(p.boot, p.themes, p.conf.parent)


def clean(p, r):
    out = r.stdout + r.stderr
    assert "Traceback" not in out, out
    left = [x.name for d in (p.boot, p.conf.parent, p.themes) for x in d.iterdir() if x.name.endswith(".new")
            or x.name.startswith(".memoryframe")]
    assert left == [], left


def pristine(p, before):
    assert everything(p) == before and p.theme() == "pix"
    assert not p.state.exists() or "plymouth_" not in p.state.read_text()


# ---------------- M1: signals ----------------

@pytest.mark.parametrize("sig", ["INT", "TERM", "HUP"])
@pytest.mark.parametrize("where", ["after-build", "mid-build-truncated", "verify", "set-theme"])
def test_a_signal_during_on_rolls_everything_back(pl, sig, where):
    before = everything(pl)
    if where == "after-build":
        env = wrap(pl, "UPDATE_INITRAMFS", f'"{{real}}" "$@"; kill -{sig} $PPID; sleep 5')
    elif where == "mid-build-truncated":
        env = wrap(pl, "UPDATE_INITRAMFS", f': > "{{boot}}/initramfs8"; kill -{sig} $PPID; sleep 5')
    elif where == "verify":                                         # the 2nd lsinitramfs call = after the build
        env = wrap(pl, "LSINITRAMFS", f'n=$(cat "{pl.tmp}/lsn" 2>/dev/null || echo 0); echo $((n+1)) > "{pl.tmp}/lsn"\n'
                                       f'[ "$n" -ge 1 ] && {{ kill -{sig} $PPID; sleep 5; }}\nexec "{{real}}" "$@"')
    else:
        env = wrap(pl, "PLYMOUTH_SET", f'"{{real}}" "$@"; [ $# -gt 0 ] && {{ kill -{sig} $PPID; sleep 5; }}; exit 0')
    r = pl.run("plymouth", "on", **env)
    assert r.returncode == 130, r.stdout + r.stderr
    assert "interrupted" in r.stderr and "rolled back" in r.stdout
    clean(pl, r)
    pristine(pl, before)
    r2 = pl.run("plymouth", "on")                                    # and a normal run works afterwards
    assert r2.returncode == 0 and pl.theme() == "memoryframe"


@pytest.mark.parametrize("sig", ["INT", "TERM", "HUP"])
def test_a_signal_during_off_puts_ours_back_and_keeps_the_state(pl, sig):
    orig = pl.initrd.read_bytes()
    assert pl.run("plymouth", "on").returncode == 0
    built, st = pl.initrd.read_bytes(), pl.st()
    env = wrap(pl, "UPDATE_INITRAMFS", f': > "{{boot}}/initramfs8"; kill -{sig} $PPID; sleep 5')
    r = pl.run("plymouth", "off", **env)
    assert r.returncode == 130 and "state kept" in r.stderr
    clean(pl, r)
    assert pl.initrd.read_bytes() == built and pl.theme() == "memoryframe" and pl.st() == st
    assert pl.run("plymouth", "off").returncode == 0 and pl.initrd.read_bytes() == orig


def test_a_signal_before_the_pending_record_changes_nothing(pl):
    before = everything(pl)
    env = wrap(pl, "LSINITRAMFS", 'kill -INT $PPID; sleep 5')
    r = pl.run("plymouth", "on", **env)
    assert r.returncode == 130 and "before any change" in r.stderr
    clean(pl, r)
    pristine(pl, before)


# ---------------- M1: kill -9 leaves a pending record ----------------

def _killed_on(p, extra=""):
    env = wrap(p, "UPDATE_INITRAMFS", f'"{{real}}" "$@"; {extra}kill -KILL $PPID; sleep 5')
    r = p.run("plymouth", "on", **env)
    assert r.returncode != 0
    st = p.st()
    assert st["plymouth_pending"] == "on" and st["plymouth_run_kernel"] == KERNEL
    assert st["plymouth_run_bak_path"].endswith(st["plymouth_run_bak"]) and st["plymouth_run_conf_bak"]
    assert st["plymouth_run_prev"] == "pix" and len(st["plymouth_run_sha"]) == 64
    assert p.theme() == "memoryframe" and (p.themes / "memoryframe").is_dir()      # the frame was left mutated
    return st


def test_after_a_kill_the_next_on_undoes_it_then_installs(pl):
    orig = pl.initrd.read_bytes()
    _killed_on(pl)
    r = pl.run("plymouth", "on")
    assert r.returncode == 0 and "interrupted `plymouth on` was found" in r.stdout
    clean(pl, r)
    st = pl.st()
    assert "plymouth_pending" not in st and st["plymouth_sha_before"] == __import__("hashlib").sha256(orig).hexdigest()
    assert len(list(pl.boot.glob("initramfs8.memoryframe-bak.*"))) == 1


def test_after_a_kill_off_undoes_it(pl):
    before = everything(pl)
    _killed_on(pl, extra=': > "{boot}/initramfs8"; ')              # and the initramfs left empty
    assert pl.initrd.stat().st_size == 0
    r = pl.run("plymouth", "off")
    assert r.returncode == 0 and "undoing it first" in r.stdout and "no Memory Frame theme of ours left" in r.stdout
    clean(pl, r)
    pristine(pl, before)


def test_status_shows_a_pending_run(pl):
    _killed_on(pl)
    assert "an interrupted `on` is pending" in pl.run("plymouth", "status").stdout


# ---------------- M1: state not writable ----------------

@not_root
def test_an_unwritable_state_dir_aborts_before_any_change(pl):
    before = everything(pl)
    pl.state.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(pl.state.parent, 0o555)
    try:
        r = pl.run("plymouth", "on")
    finally:
        os.chmod(pl.state.parent, 0o755)
    assert r.returncode == 1 and "cannot be written" in r.stderr and "nothing changed" in r.stderr
    clean(pl, r)
    pristine(pl, before)
    assert pl.rebuilds() == []


def test_a_state_path_that_is_a_directory_aborts_before_any_change(pl):
    before = everything(pl)
    pl.state.mkdir(parents=True)
    r = pl.run("plymouth", "on")
    assert r.returncode == 1 and "nothing changed" in r.stderr
    assert "Traceback" not in r.stderr and everything(pl) == before and pl.rebuilds() == []


@not_root
def test_a_failed_final_state_write_rolls_back_and_the_next_run_finishes_it(pl):
    before = everything(pl)
    pl.state.parent.mkdir(parents=True, exist_ok=True)
    env = wrap(pl, "UPDATE_INITRAMFS", f'"{{real}}" "$@"; chmod 555 "{pl.state.parent}"')
    try:
        r = pl.run("plymouth", "on", **env)
    finally:
        os.chmod(pl.state.parent, 0o755)
    assert r.returncode == 1 and "Traceback" not in r.stderr
    assert everything(pl) == before and pl.theme() == "pix"                     # files back
    assert pl.st().get("plymouth_pending") == "on"                              # the record could not be cleared
    r2 = pl.run("plymouth", "on")
    assert r2.returncode == 0 and "undoing it first" in r2.stdout and pl.theme() == "memoryframe"


# ---------------- S1: the rollback's own steps fail ----------------

def test_a_lost_backup_does_not_crash_the_rollback(pl):
    # Hale C: the backups vanish, then the build is bad: no traceback; theme, files and conf still restored
    env = wrap(pl, "UPDATE_INITRAMFS", f'rm -f "{{boot}}"/*.memoryframe-bak.*; exec "{{real}}" "$@"')
    r = pl.run("plymouth", "on", FAKE_UI="noscript", **env)
    assert r.returncode == 1 and "Traceback" not in r.stderr
    assert "no intact backup" in r.stderr and "auto_initramfs" in r.stderr       # what is left + the PC hint
    assert pl.theme() == "pix" and not (pl.themes / "memoryframe").exists() and pl.conf.read_text() == CONF_ORIG
    assert pl.st()["plymouth_pending"] == "on"


@not_root
def test_a_failing_restore_copy_keeps_the_backup_and_says_how_to_fix_it(pl):
    env = wrap(pl, "UPDATE_INITRAMFS", f'"{{real}}" "$@"; chmod 555 "{{boot}}"')
    try:
        r = pl.run("plymouth", "on", FAKE_UI="noscript", **env)
    finally:
        os.chmod(pl.boot, 0o755)
    assert r.returncode == 1 and "Traceback" not in r.stderr
    st = pl.st()
    bak = st["plymouth_run_bak"]
    assert "initramfs8 not restored" in r.stderr and f"copy {bak} over initramfs8" in r.stderr
    assert (pl.boot / bak).exists() and st["plymouth_pending"] == "on"
    assert pl.theme() == "pix" and pl.conf.read_text() == CONF_ORIG               # the other steps still ran
    assert not list(pl.boot.glob(".*.new"))
    r2 = pl.run("plymouth", "off")                                             # writable again: off finishes it
    assert r2.returncode == 0 and "undoing it first" in r2.stdout
    assert not list(pl.boot.glob("*.memoryframe-bak.*")) and not pl.state.exists()


# ---------------- N4 / N5 ----------------

def test_off_keeps_plymouthd_conf_edits_made_after_on(pl):
    assert pl.run("plymouth", "on").returncode == 0
    pl.conf.write_text(pl.conf.read_text() + "DeviceTimeout=3\n")
    r = pl.run("plymouth", "off")
    assert r.returncode == 0 and "edited after `on`: kept" in r.stdout
    text = pl.conf.read_text()
    assert "DeviceTimeout=3" in text and "Theme=pix" in text and "Theme=memoryframe" not in text


def test_leftovers_of_an_interrupted_copy_are_removed(pl):
    (pl.boot / ".initramfs8.new").write_text("half")
    (pl.boot / ".initramfs8.memoryframe-bak.20260101000000.new").write_text("half")
    (pl.conf.parent / ".plymouthd.conf.new").write_text("half")
    (pl.themes / ".memoryframe.new.abc").mkdir()
    r = pl.run("plymouth", "on")
    assert r.returncode == 0 and "removed the leftover .initramfs8.new" in r.stdout
    clean(pl, r)


def test_copy_synced_leaves_no_new_file_when_it_fails(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMORYFRAME_BOOT_DIR", str(tmp_path))
    spec = importlib.util.spec_from_file_location("bsp", ROOT / "deploy" / "boot-splash-plymouth.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    src, dst = tmp_path / "a", tmp_path / "initramfs8"
    src.write_bytes(b"x" * 100)
    dst.write_bytes(b"old")

    def boom(*a, **k):
        raise OSError(28, "No space left on device")
    monkeypatch.setattr(m.shutil, "copyfileobj", boom)
    with pytest.raises(OSError):
        m.copy_synced(src, dst)
    assert dst.read_bytes() == b"old" and not (tmp_path / ".initramfs8.new").exists()


def test_a_failing_lsinitramfs_before_the_run_is_one_line(pl):
    before = everything(pl)
    r = pl.run("plymouth", "on", FAKE_LS="fail")
    assert r.returncode == 1 and "Traceback" not in r.stderr and "nothing changed" in r.stderr
    assert everything(pl) == before


def test_the_initramfs_is_only_ever_written_by_rename():
    src = (ROOT / "deploy" / "boot-splash-plymouth.py").read_text()
    assert src.count("copy_synced(") >= 4 and "shutil.copy2" not in src and "shutil.copyfile(SRC" in src
    assert "signal.SIGHUP" in src and "signal.SIGTERM" in src and "signal.SIGINT" in src
