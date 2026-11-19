"""#162 quiet boot: deploy/boot-splash.sh on/off edits cmdline.txt (one line, tokens appended once, backup, atomic,
recorded so `off` removes only what it added). Hermetic: MEMORYFRAME_BOOT_DIR / MEMORYFRAME_BOOT_STATE in tmp_path."""
import hashlib
import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "deploy" / "boot-splash.sh"
TOKENS = ["quiet", "logo.nologo", "vt.global_cursor_default=0", "loglevel=3"]
# Hale's line from the frame (findings.md), sanitised: partition id and imager id replaced.
HALE = ("console=serial0,115200 console=tty1 root=PARTUUID=0badf00d-02 rootfstype=ext4 fsck.repair=yes rootwait "
        "quiet splash plymouth.ignore-serial-consoles ds=nocloud;i=rpi-imager-0000000000000 "
        "cfg80211.ieee80211_regdom=AU\n")
MODE = "dev\n"


def boot(tmp_path, cmdline=None, config=None):
    d = tmp_path / "boot"
    d.mkdir(exist_ok=True)
    if cmdline is not None:
        (d / "cmdline.txt").write_bytes(cmdline.encode())
    if config is not None:
        (d / "config.txt").write_bytes(config.encode())
    (d / "memoryframe-mode.txt").write_text(MODE)
    return d


def run(tmp_path, *args, extra=None):
    e = {"PATH": os.environ["PATH"], "HOME": str(tmp_path / "home"),
         "MEMORYFRAME_BOOT_DIR": str(tmp_path / "boot"),
         "MEMORYFRAME_BOOT_STATE": str(tmp_path / "etc" / "boot-splash.state"), **(extra or {})}
    r = subprocess.run(["bash", str(SCRIPT), *args], capture_output=True, text=True, env=e, timeout=30)
    assert r.returncode == 0, r.stdout + r.stderr
    return r


def md5(p):
    return hashlib.md5(p.read_bytes()).hexdigest()


def baks(d, name="cmdline.txt"):
    return sorted(d.glob(f"{name}.memoryframe-bak.*"))


def state(tmp_path):
    f = tmp_path / "etc" / "boot-splash.state"
    return f.read_text() if f.exists() else None


def test_hale_line_gets_missing_tokens_once_and_keeps_every_token_in_order(tmp_path):
    d = boot(tmp_path, HALE)
    mode = md5(d / "memoryframe-mode.txt")
    run(tmp_path, "on")
    once = (d / "cmdline.txt").read_text()
    assert once.count("\n") == 1 and once.endswith("\n")                       # still exactly one line
    toks = once.split()
    assert toks[:len(HALE.split())] == HALE.split()                              # existing tokens kept, in order
    assert toks[len(HALE.split()):] == ["logo.nologo", "vt.global_cursor_default=0", "loglevel=3"]  # quiet was there
    assert toks.count("quiet") == 1
    assert state(tmp_path) == "cmdline_added=logo.nologo vt.global_cursor_default=0 loglevel=3\n"
    assert [b.read_text() for b in baks(d)] == [HALE]                            # one backup = the original line
    run(tmp_path, "on")                                                          # run twice = identical, no new backup
    assert (d / "cmdline.txt").read_text() == once and len(baks(d)) == 1
    assert md5(d / "memoryframe-mode.txt") == mode
    assert not list(d.glob(".*.new"))                                            # no temp file left


def test_off_removes_only_our_tokens_and_a_preexisting_quiet_survives(tmp_path):
    d = boot(tmp_path, HALE)
    run(tmp_path, "on")
    run(tmp_path, "off")
    assert (d / "cmdline.txt").read_text() == HALE                               # byte-identical, quiet kept
    assert state(tmp_path) is None                                               # nothing of ours recorded any more
    run(tmp_path, "off")                                                         # off twice: nothing to do
    assert (d / "cmdline.txt").read_text() == HALE


def test_quiet_added_by_us_is_removed_by_off(tmp_path):
    line = "console=tty1 root=PARTUUID=0badf00d-02 rootfstype=ext4 rootwait\n"
    d = boot(tmp_path, line)
    run(tmp_path, "on")
    assert (d / "cmdline.txt").read_text() == line[:-1] + " " + " ".join(TOKENS) + "\n"
    run(tmp_path, "off")
    assert (d / "cmdline.txt").read_text() == line


def test_line_already_holding_the_tokens_is_not_written(tmp_path):
    line = "root=/dev/mmcblk0p2 rootwait quiet logo.nologo vt.global_cursor_default=0 loglevel=3\n"
    d = boot(tmp_path, line)
    before = os.stat(d / "cmdline.txt").st_mtime_ns
    r = run(tmp_path, "on")
    assert "already" in r.stdout and (d / "cmdline.txt").read_text() == line
    assert os.stat(d / "cmdline.txt").st_mtime_ns == before and baks(d) == [] and state(tmp_path) is None
    run(tmp_path, "off")
    assert (d / "cmdline.txt").read_text() == line                               # never ours: never removed


def test_existing_key_with_another_value_is_kept_and_not_duplicated(tmp_path):
    d = boot(tmp_path, "root=/dev/sda2 loglevel=7 vt.global_cursor_default=1\n")
    run(tmp_path, "on")
    toks = (d / "cmdline.txt").read_text().split()
    assert "loglevel=7" in toks and "loglevel=3" not in toks
    assert "vt.global_cursor_default=1" in toks and "vt.global_cursor_default=0" not in toks
    run(tmp_path, "off")
    assert (d / "cmdline.txt").read_text() == "root=/dev/sda2 loglevel=7 vt.global_cursor_default=1\n"


@pytest.mark.parametrize("line", [
    "root=/dev/mmcblk0p2   rootwait  ",                 # extra spaces, no newline
    "root=/dev/mmcblk0p2 rootwait",                     # no newline
    "root=/dev/mmcblk0p2 rootwait\r\n",                 # CRLF (edited on a PC)
    "  root=/dev/mmcblk0p2 rootwait\n",                 # leading spaces
])
def test_odd_spacing_and_newlines_round_trip(tmp_path, line):
    d = boot(tmp_path, line)
    run(tmp_path, "on")
    got = (d / "cmdline.txt").read_bytes().decode()
    assert got.split() == line.split() + TOKENS
    assert got.count("\n") == line.count("\n") and got.endswith(line[len(line.rstrip()):])   # same ending
    run(tmp_path, "on")
    assert (d / "cmdline.txt").read_bytes().decode() == got
    run(tmp_path, "off")
    assert (d / "cmdline.txt").read_bytes().decode() == line


@pytest.mark.parametrize("bad,why", [
    ("", "not exactly one line"),
    ("\n", "not exactly one line"),
    ("root=/dev/mmcblk0p2 rootwait\nquiet\n", "not exactly one line"),
    ("console=tty1 rootwait quiet\n", "no root= token"),
])
def test_refused_files_are_left_unchanged(tmp_path, bad, why):
    d = boot(tmp_path, bad)
    r = run(tmp_path, "on")                                                      # warns, exit 0
    assert why in r.stdout and "WARNING" in r.stdout
    assert (d / "cmdline.txt").read_bytes() == bad.encode() and baks(d) == [] and state(tmp_path) is None


def test_missing_cmdline_is_skipped(tmp_path):
    d = boot(tmp_path)
    r = run(tmp_path, "on")
    assert "not found" in r.stdout and not (d / "cmdline.txt").exists()


def test_only_boot_splash_edits_cmdline_txt():
    """test_mfapp_69 / test_sd_wear_97 forbid cmdline.txt elsewhere; the one writer is boot-splash(-edit)."""
    for f in (ROOT / "deploy").iterdir():
        if f.is_file() and f.name not in ("boot-splash.sh", "boot-splash-edit.py", "install.sh", "uninstall.sh"):
            code = "\n".join(l.split("#", 1)[0] for l in f.read_text(errors="replace").splitlines())
            assert "cmdline.txt" not in code, f.name
    for f in ("install.sh", "uninstall.sh"):
        code = "\n".join(l.split("#", 1)[0] for l in (ROOT / "deploy" / f).read_text().splitlines())
        assert "cmdline.txt" not in code, f                                       # they only call boot-splash.sh


def test_edit_never_mentions_the_mode_file_or_network_profiles():
    for f in ("boot-splash.sh", "boot-splash-edit.py", "session-start.sh", "bootwatch.sh", "labwc-autostart.py"):
        code = "\n".join(l.split("#", 1)[0] for l in (ROOT / "deploy" / f).read_text().splitlines())
        for bad in ("memoryframe-mode", "netplan", "nmcli", "NetworkManager", "system-connections", "hotspot.conf"):
            assert bad not in code, (f, bad)


def test_atomic_write_and_never_reboots():
    import re
    src = (ROOT / "deploy" / "boot-splash-edit.py").read_text()
    assert "os.fsync" in src and "os.replace" in src
    cmd = re.compile(r"(^|[;&|(]\s*)(sudo\s+)?(reboot|systemctl\s+(reboot|kexec)|shutdown\s+-r)\b")
    for f in ("boot-splash.sh", "boot-splash-edit.py", "session-start.sh", "bootwatch.sh", "kiosk.sh",
              "install.sh", "uninstall.sh"):
        for line in (ROOT / "deploy" / f).read_text().splitlines():
            code = re.sub(r"\"[^\"]*\"|'[^']*'", "''", line.split("#", 1)[0]).strip()   # messages are not commands
            assert not cmd.search(code), (f, line)
