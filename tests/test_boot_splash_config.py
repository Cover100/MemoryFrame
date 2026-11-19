"""#162 quiet boot: deploy/boot-splash.sh on/off puts disable_splash=1 into config.txt's [all] section (no rainbow),
with a backup, once, and `off` restores the file byte-identical."""
import os

import pytest

from test_boot_splash_cmdline import HALE, baks, boot, md5, run, state

# Hale's config.txt (findings.md): ends in [all] (the fan overlay is the last line).
HALE_CONFIG = """# For more options and information see
# http://rptl.io/configtxt
dtparam=audio=on
camera_auto_detect=1
display_auto_detect=1
auto_initramfs=1
dtoverlay=vc4-kms-v3d
max_framebuffers=2
disable_fw_kms_setup=1
arm_64bit=1
disable_overscan=1
arm_boost=1

[cm4]
otg_mode=1

[cm5]
dtoverlay=dwc2,dr_mode=host

[pi5]
dtoverlay=nospi10

[all]
# MemoryFrame: Armor Lite fan on at 60 C (added by deploy/install.sh)
dtoverlay=gpio-fan,gpiopin=14,temp=60000
"""
LINE = "disable_splash=1"


def section_of(text, wanted):
    sec = "all"
    for l in text.splitlines():
        s = l.strip()
        if s.startswith("[") and s.endswith("]"):
            sec = s[1:-1].lower()
        elif s == wanted:
            return sec
    return None


def test_hale_file_ending_in_all_gets_one_line_in_all(tmp_path):
    d = boot(tmp_path, HALE, HALE_CONFIG)
    mode = md5(d / "memoryframe-mode.txt")
    run(tmp_path, "on")
    got = (d / "config.txt").read_text()
    assert got.startswith(HALE_CONFIG) and got.count(LINE) == 1
    assert section_of(got, LINE) == "all" and got.count("[all]") == 1             # no extra header
    assert [b.read_text() for b in baks(d, "config.txt")] == [HALE_CONFIG]
    run(tmp_path, "on")
    assert (d / "config.txt").read_text() == got and len(baks(d, "config.txt")) == 1
    run(tmp_path, "off")
    assert (d / "config.txt").read_text() == HALE_CONFIG and state(tmp_path) is None
    assert md5(d / "memoryframe-mode.txt") == mode


def test_file_ending_in_pi5_gets_an_all_header(tmp_path):
    cfg = "dtparam=audio=on\n[pi5]\ndtoverlay=nospi10\n"
    d = boot(tmp_path, HALE, cfg)
    run(tmp_path, "on")
    got = (d / "config.txt").read_text()
    assert got.startswith(cfg + "[all]\n") and section_of(got, LINE) == "all"
    run(tmp_path, "off")
    assert (d / "config.txt").read_text() == cfg


def test_no_sections_and_no_trailing_newline(tmp_path):
    cfg = "dtparam=audio=on"
    d = boot(tmp_path, HALE, cfg)
    run(tmp_path, "on")
    got = (d / "config.txt").read_text()
    assert got.startswith("dtparam=audio=on\n") and "[all]" not in got and got.endswith(LINE + "\n")
    run(tmp_path, "off")
    assert (d / "config.txt").read_text() == cfg


def test_existing_disable_splash_0_is_commented_and_restored(tmp_path):
    cfg = "[all]\ndisable_splash=0\narm_boost=1\n"
    d = boot(tmp_path, HALE, cfg)
    run(tmp_path, "on")
    got = (d / "config.txt").read_text()
    active = [l.strip() for l in got.splitlines() if l.strip().startswith("disable_splash")]
    assert active == [LINE]                                                       # the 0 is commented out
    assert "disable_splash=0" in got                                              # kept as a comment
    run(tmp_path, "off")
    assert (d / "config.txt").read_text() == cfg


def test_existing_disable_splash_1_not_ours_is_left_alone(tmp_path):
    cfg = "disable_splash=1\n[all]\narm_boost=1\n"
    d = boot(tmp_path, HALE, cfg)
    before = os.stat(d / "config.txt").st_mtime_ns
    r = run(tmp_path, "on")
    assert "not ours" in r.stdout and (d / "config.txt").read_text() == cfg
    assert os.stat(d / "config.txt").st_mtime_ns == before and baks(d, "config.txt") == []
    run(tmp_path, "off")
    assert (d / "config.txt").read_text() == cfg


@pytest.mark.parametrize("later", ["# fan\ndtoverlay=gpio-fan,gpiopin=14,temp=60000\n"])
def test_lines_added_after_ours_are_kept_by_off(tmp_path, later):
    cfg = "[pi5]\ndtoverlay=nospi10\n"
    d = boot(tmp_path, HALE, cfg)
    run(tmp_path, "on")
    with open(d / "config.txt", "a") as f:                                        # e.g. fan-overlay.sh afterwards
        f.write(later)
    run(tmp_path, "off")
    got = (d / "config.txt").read_text()
    assert LINE not in got and got.endswith(later) and section_of(got, "dtoverlay=gpio-fan,gpiopin=14,temp=60000") == "all"


def test_missing_config_is_skipped(tmp_path):
    d = boot(tmp_path, HALE)
    r = run(tmp_path, "on")
    assert "config.txt not found" in r.stdout and not (d / "config.txt").exists()
