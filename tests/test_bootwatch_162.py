"""#162 E4: deploy/bootwatch.sh (memoryframe-bootwatch.service) fires at 90 s of uptime only when there is no labwc and
no /run/mfkiosk/session-up: the Plymouth message with the Memory Frame theme, else Plymouth quit + 2 ASCII lines on
tty1 and chvt 1. A no-op when the session is up. Stubs: plymouth, plymouth-set-default-theme, chvt, pgrep, sleep."""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "deploy" / "bootwatch.sh"
UNIT = ROOT / "deploy" / "units" / "memoryframe-bootwatch.service"
TTY_TEXT = "The frame couldn't start. Error E4\n  Unplug for 10 seconds, plug back in.\n"


def go(tmp_path, uptime="91.5", labwc=False, session_up=False, ping=1, theme="pix", sleep_body="exit 0"):
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    calls = tmp_path / "calls"
    stub = f'#!/bin/bash\necho "${{0##*/}} $*" >> "{calls}"\n'
    (bin_ / "pgrep").write_text(stub + ("exit 0\n" if labwc else "exit 1\n"))
    (bin_ / "plymouth").write_text(stub + f'[ "$1" = --ping ] && exit {0 if ping else 1}\nexit 0\n')
    (bin_ / "plymouth-set-default-theme").write_text(stub + f"echo {theme}\n")
    (bin_ / "chvt").write_text(stub + "exit 0\n")
    (bin_ / "sleep").write_text(stub + sleep_body + "\n")
    for f in bin_.iterdir():
        f.chmod(0o755)
    tools = tmp_path / "tools"
    tools.mkdir()
    for t in ("bash", "printf", "cat"):
        (tools / t).symlink_to(shutil.which(t))
    up = tmp_path / "uptime"
    up.write_text(f"{uptime} 100.00\n")
    tmpfs = tmp_path / "mfkiosk"
    tmpfs.mkdir()
    if session_up:
        (tmpfs / "session-up").write_text("")
    tty = tmp_path / "tty1"
    e = {"PATH": f"{bin_}:{tools}", "MEMORYFRAME_UPTIME_FILE": str(up), "MEMORYFRAME_KIOSK_TMPFS": str(tmpfs),
         "MEMORYFRAME_TTY": str(tty)}
    r = subprocess.run(["bash", str(SCRIPT)], env=e, capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    return r, (calls.read_text().splitlines() if calls.exists() else []), (tty.read_text() if tty.exists() else None)


def test_no_session_at_90_s_plymouth_gone_writes_tty1(tmp_path):
    r, calls, tty = go(tmp_path, ping=0)
    assert tty is not None and tty.endswith(TTY_TEXT) and tty.isascii()
    assert "chvt 1" in calls and not any("display-message" in c for c in calls)
    assert "E4" in r.stdout


def test_memoryframe_theme_gets_the_plymouth_message(tmp_path):
    r, calls, tty = go(tmp_path, ping=1, theme="memoryframe")
    assert "plymouth display-message --text=memoryframe-e4" in calls
    assert tty is None and "chvt 1" not in calls


def test_stock_theme_quits_plymouth_and_uses_tty1(tmp_path):
    """PR A ships no theme with the E4 callback: pix would print the raw message, so tty1 instead."""
    r, calls, tty = go(tmp_path, ping=1, theme="pix")
    assert "plymouth quit" in calls and not any("display-message" in c for c in calls)
    assert tty.endswith(TTY_TEXT) and "chvt 1" in calls


@pytest.mark.parametrize("labwc,session_up", [(True, False), (False, True), (True, True)])
def test_noop_when_the_session_is_up(tmp_path, labwc, session_up):
    r, calls, tty = go(tmp_path, labwc=labwc, session_up=session_up)
    assert tty is None and not any(c.startswith(("plymouth", "chvt")) for c in calls)
    assert "session is up" in r.stdout


def test_waits_until_90_s_of_uptime(tmp_path):
    """Before 90 s it only sleeps; the stub sleep moves the clock 30 s on each call."""
    up = tmp_path / "uptime"
    r, calls, tty = go(tmp_path, uptime="20.0",
                       sleep_body=f'read -r u _ < "{up}"; echo "$(( ${{u%%.*}} + 30 )).0 1" > "{up}"; exit 0')
    sleeps = [c for c in calls if c.startswith("sleep")]
    assert len(sleeps) == 3                                                        # 20 -> 50 -> 80 -> 110
    assert tty is not None and tty.endswith(TTY_TEXT)


def test_session_appearing_while_waiting_stops_it(tmp_path):
    tmpfs = tmp_path / "mfkiosk"
    r, calls, tty = go(tmp_path, uptime="20.0", sleep_body=f': > "{tmpfs}/session-up"; exit 0')
    assert tty is None and len([c for c in calls if c.startswith("sleep")]) == 1


def test_unit_is_root_background_and_wanted_by_multi_user():
    u = UNIT.read_text()
    assert "Type=simple" in u and "WantedBy=multi-user.target" in u
    assert "ExecStart=/usr/local/sbin/memoryframe-bootwatch" in u
    assert "User=" not in u and "Type=oneshot" not in u                            # never delays the boot
    inst = (ROOT / "deploy" / "install.sh").read_text()
    assert 'put_file "$SRC_DIR/deploy/bootwatch.sh" /usr/local/sbin/memoryframe-bootwatch 0755' in inst
    assert "sudo systemctl enable memoryframe-bootwatch.service" in inst
    un = (ROOT / "deploy" / "uninstall.sh").read_text()
    assert "memoryframe-bootwatch.service" in un and "/usr/local/sbin/memoryframe-bootwatch" in un


def test_never_reboots_or_touches_anything_else():
    code = "\n".join(l.split("#", 1)[0] for l in SCRIPT.read_text().splitlines())
    for bad in ("reboot", "rm ", "systemctl", "memoryframe-mode", "> /boot", "nmcli"):
        assert bad not in code, bad
