"""#162 (13.4): /boot/firmware/memoryframe-desktop.txt makes session-start.sh do nothing and kiosk.sh exit at once
(the stock desktop); removing it restores both. deploy/boot-splash.sh desktop on|off creates/removes it."""
import os
import subprocess
from pathlib import Path

from test_kiosk_script import SCRIPT as KIOSK, env, launches, log_text, run_until, start_kiosk  # noqa: F401
from test_session_start_162 import go as session_start

ROOT = Path(__file__).resolve().parents[1]


def flag_cmd(bootd, what):
    e = {"PATH": os.environ["PATH"], "HOME": str(bootd), "MEMORYFRAME_BOOT_DIR": str(bootd)}
    r = subprocess.run(["bash", str(ROOT / "deploy" / "boot-splash.sh"), "desktop", what], env=e,
                       capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    return r


def test_boot_splash_desktop_on_off(tmp_path):
    bootd = tmp_path / "boot"
    bootd.mkdir()
    (bootd / "memoryframe-mode.txt").write_text("product\n")
    flag_cmd(bootd, "on")
    assert (bootd / "memoryframe-desktop.txt").exists()
    flag_cmd(bootd, "on")                                                          # idempotent
    flag_cmd(bootd, "off")
    assert not (bootd / "memoryframe-desktop.txt").exists()
    assert (bootd / "memoryframe-mode.txt").read_text() == "product\n"


def test_kiosk_exits_at_once_with_the_flag_and_launches_without(env):
    tmp_path, _state, _, e = env
    bootd = tmp_path / "boot"
    bootd.mkdir()
    (bootd / "memoryframe-desktop.txt").write_text("x\n")
    e = dict(e, MEMORYFRAME_SPLASH="1")
    p = start_kiosk(e)
    assert p.wait(timeout=10) == 0
    assert "desktop-mode flag present" in log_text(tmp_path) and launches(tmp_path) == []
    (bootd / "memoryframe-desktop.txt").unlink()                                   # removal restores the kiosk
    run_until(e, lambda: len(launches(tmp_path)) >= 1)


def test_session_start_does_nothing_with_the_flag_and_works_without(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    lines, log, _ = session_start(tmp_path / "a", flag=True)
    assert lines == [] and "desktop-mode flag present" in log
    lines, _, _ = session_start(tmp_path / "b", flag=False)
    assert lines and lines[0].startswith("wtype")


def test_flag_lives_on_the_boot_partition_in_every_script():
    for f in ("kiosk.sh", "session-start.sh", "boot-splash.sh"):
        assert "memoryframe-desktop.txt" in (ROOT / "deploy" / f).read_text()
        assert "/boot/firmware" in (ROOT / "deploy" / f).read_text()
