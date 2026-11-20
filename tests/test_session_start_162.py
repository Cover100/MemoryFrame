"""#162 S3: deploy/session-start.sh (first line of the labwc user autostart): #155 hide keybind FIRST, then swaybg with
Wren's still splash, then a bounded loop that stops the lwrespawn wrappers before pcmanfm-pi / wf-panel-pi (this user,
exact names). Never Chromium, kiosk.sh or labwc. The desktop flag or a non-Wayland session: nothing. Stubs only."""
import os
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "deploy" / "session-start.sh"
TOOLS = ["bash", "date", "cat", "dirname", "id", "mkdir", "timeout", "printf"]


def setup(tmp_path, wayland=True, flag=False, swaybg=True, wtype=True, kill_for="3"):
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    calls = tmp_path / "calls"
    stub = f'#!/bin/bash\necho "${{0##*/}} $*" >> "{calls}"\n'
    (bin_ / "pkill").write_text(stub + "exit 0\n")              # "found and signalled" every time
    (bin_ / "pgrep").write_text(stub + "exit 1\n")
    (bin_ / "sleep").write_text(stub + "exit 0\n")              # the 0.3 s steps take no time here
    if wtype:
        (bin_ / "wtype").write_text(stub + "exit 0\n")
    if swaybg:
        (bin_ / "swaybg").write_text(stub + "exit 0\n")
    for f in bin_.iterdir():
        f.chmod(0o755)
    tools = tmp_path / "tools"
    tools.mkdir()
    for t in TOOLS:
        (tools / t).symlink_to(shutil.which(t))
    tmpfs = tmp_path / "mfkiosk"
    tmpfs.mkdir()
    bootd = tmp_path / "boot"
    bootd.mkdir()
    if flag:
        (bootd / "memoryframe-desktop.txt").write_text("x\n")
    (tmp_path / "uptime").write_text("17.25 30.00\n")
    e = {"PATH": f"{bin_}:{tools}", "HOME": str(tmp_path), "XDG_RUNTIME_DIR": str(tmp_path),
         "MEMORYFRAME_KIOSK_TMPFS": str(tmpfs), "MEMORYFRAME_BOOT_DIR": str(bootd),
         "MEMORYFRAME_UPTIME_FILE": str(tmp_path / "uptime"), "MEMORYFRAME_SESSION_KILL_FOR": kill_for}
    if wayland:
        e["XDG_SESSION_TYPE"] = "wayland"
    return e, calls, tmpfs


def go(tmp_path, **kw):
    e, calls, tmpfs = setup(tmp_path, **kw)
    r = subprocess.run(["bash", str(SCRIPT)], env=e, capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    lines = calls.read_text().splitlines() if calls.exists() else []
    log = (tmpfs / "memoryframe-session.log").read_text()
    return lines, log, tmpfs


def test_order_swaybg_first_then_hide_then_wrappers_before_processes(tmp_path):
    lines, log, tmpfs = go(tmp_path)
    # swaybg FIRST (the ink + lockup bridge after Plymouth quits retaining its splash), then the #155 pointer hide
    assert lines[0].startswith("swaybg -c #0E1311 -i ") and lines[0].endswith("static/boot/splash-A.png -m center")
    assert lines[1] == "wtype -M alt -M logo h -m alt -m logo"
    kills = [l for l in lines if l.startswith("pkill")]
    me = str(os.getuid())
    first = kills[:4]
    assert first[0].startswith(f"pkill -u {me} -f -- ") and "lwrespawn" in first[0]   # wrappers first
    assert first[1:] == [f"pkill -u {me} -x -- pcmanfm-pi", f"pkill -u {me} -x -- pcmanfm",
                         f"pkill -u {me} -x -- wf-panel-pi"]
    assert (tmpfs / "session-up").exists()
    assert (tmp_path / "memoryframe-swaybg.pid").exists()                         # kiosk.sh can swap it later
    assert "uptime 17.25" in log and "stopped the lwrespawn wrappers" in log and "stopped wf-panel-pi" in log


def test_loop_is_bounded(tmp_path):
    lines, _, _ = go(tmp_path, kill_for="3")
    assert sum(l.startswith("sleep 0.3") for l in lines) == 10                    # 3 s / 0.3 s
    assert sum(" -f -- " in l for l in lines) == 10


def test_never_signals_chromium_kiosk_or_labwc(tmp_path):
    lines, _, _ = go(tmp_path)
    for l in lines:
        if l.startswith(("pkill", "pgrep")):
            for bad in ("chromium", "kiosk", "labwc", "swaybg", "lightdm", "-9", "KILL"):
                assert bad not in l, l
            assert f"-u {os.getuid()}" in l                                        # this user only


def test_wrapper_pattern_matches_only_the_respawn_wrappers():
    import re
    src = SCRIPT.read_text()
    pat = re.search(r"WRAPPERS='([^']+)'", src).group(1)
    rx = re.compile(pat)
    for yes in ("/usr/bin/lwrespawn /usr/bin/pcmanfm-pi", "/bin/sh /usr/bin/lwrespawn /usr/bin/wf-panel-pi",
                "lwrespawn pcmanfm"):
        assert rx.search(yes), yes
    for no in ("/usr/bin/pcmanfm-pi", "/usr/bin/kanshi", "/usr/bin/lwrespawn /usr/bin/kanshi",
               "bash /opt/memoryframe/deploy/kiosk.sh", "/usr/bin/chromium --kiosk", "labwc -m",
               "/usr/bin/lwrespawn /usr/bin/wf-panel-pi-x"):
        assert not rx.search(no), no


def test_desktop_flag_skips_everything(tmp_path):
    lines, log, tmpfs = go(tmp_path, flag=True)
    assert lines == [] and "desktop-mode flag present" in log
    assert (tmpfs / "session-up").exists()                                         # no E4 in desktop mode either


def test_no_wayland_does_nothing(tmp_path):
    lines, log, _ = go(tmp_path, wayland=False)
    assert lines == [] and "not a Wayland session" in log


def test_without_swaybg_or_wtype_still_hides_the_desktop(tmp_path):
    lines, log, _ = go(tmp_path, swaybg=False, wtype=False)
    assert not any(l.startswith(("swaybg", "wtype")) for l in lines)
    assert any(l.startswith("pkill") for l in lines)
    assert "swaybg: not installed" in log and "wtype not installed" in log


def test_swaybg_image_is_wrens_file():
    import hashlib
    png = ROOT / "static" / "boot" / "splash-A.png"
    assert hashlib.sha256(png.read_bytes()).hexdigest() == \
        "168b6ab1904006eceab2c9541d7b3094854de96070da22ff0697a1542b607566"   # wren-A-static.png, byte-for-byte
