"""#162 boot handover: deploy/boot-handover.sh on|off|status (lightdm drop-in, root handoff script, Chromium managed policy) and
deploy/plymouth-handoff.sh (settle, then quit --retain-splash). All in a temp dir through the three test hooks: nothing here can
touch the real /etc, and it never reads or writes cmdline.txt, config.txt, memoryframe-mode.txt or netplan."""
import json
import os
import stat
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HANDOVER = ROOT / "deploy" / "boot-handover.sh"
HANDOFF = ROOT / "deploy" / "plymouth-handoff.sh"


@pytest.fixture
def box(tmp_path):
    d = {"sbin": tmp_path / "sbin", "sysd": tmp_path / "systemd", "pol": tmp_path / "policies" / "managed"}
    env = dict(os.environ, MEMORYFRAME_SBIN=str(d["sbin"]), MEMORYFRAME_SYSTEMD_DIR=str(d["sysd"]),
               MEMORYFRAME_CHROMIUM_POLICY_DIR=str(d["pol"]))
    env.pop("MEMORYFRAME_BOOT_DIR", None)
    return d, env


def run(env, *args, script=HANDOVER):
    return subprocess.run(["bash", str(script), *args], env=env, capture_output=True, text=True, timeout=30)


def files(d):
    return (d["sbin"] / "memoryframe-plymouth-handoff", d["sysd"] / "lightdm.service.d" / "memoryframe-handoff.conf",
            d["pol"] / "memoryframe-boot.json")


def snapshot(root):
    return {str(p.relative_to(root)): (p.read_bytes(), p.stat().st_mtime_ns) for p in Path(root).rglob("*") if p.is_file()}


def test_on_writes_the_three_files(box):
    d, env = box
    r = run(env, "on")
    assert r.returncode == 0, r.stdout + r.stderr
    handoff, dropin, policy = files(d)
    assert handoff.read_bytes() == HANDOFF.read_bytes()
    assert stat.S_IMODE(handoff.stat().st_mode) == 0o755
    txt = dropin.read_text()
    assert "memoryframe-managed boot-handover" in txt and "[Service]\nExecStartPre=-" + str(handoff) + "\n" in txt
    assert txt.count("ExecStartPre=") == 1                                          # one directive, "-" = failure never stops lightdm
    assert json.loads(policy.read_text()) == {"BrowserThemeColor": "#0E1311", "BrowserColorScheme": "dark"}
    assert not list(d["sbin"].glob("*.??????")) and not list(d["pol"].glob("*.??????"))   # no temp files left


def test_on_twice_changes_nothing(box):
    d, env = box
    assert run(env, "on").returncode == 0
    before = snapshot(d["sbin"].parent)
    r = run(env, "on")
    assert r.returncode == 0 and "written" not in r.stdout and r.stdout.count("already set up") == 3, r.stdout
    assert snapshot(d["sbin"].parent) == before                                        # bytes and mtimes


def test_off_removes_exactly_ours_and_is_idempotent(box):
    d, env = box
    (d["pol"]).mkdir(parents=True)
    other = d["pol"] / "someone-else.json"
    other.write_text("{}")
    (d["sysd"] / "lightdm.service.d").mkdir(parents=True)
    other2 = d["sysd"] / "lightdm.service.d" / "autologin.conf"
    other2.write_text("[Service]\n")
    assert run(env, "on").returncode == 0
    r = run(env, "off")
    assert r.returncode == 0, r.stderr
    assert not any(f.exists() for f in files(d))
    assert other.read_text() == "{}" and other2.read_text() == "[Service]\n"            # neighbours untouched, dir kept
    r = run(env, "off")
    assert r.returncode == 0 and "not present" in r.stdout


def test_off_removes_the_empty_dropin_dir(box):
    d, env = box
    run(env, "on")
    run(env, "off")
    assert not (d["sysd"] / "lightdm.service.d").exists()


def test_off_keeps_a_file_somebody_edited(box):
    d, env = box
    run(env, "on")
    handoff, dropin, policy = files(d)
    policy.write_text('{"BrowserThemeColor": "#000000"}\n')
    handoff.write_text("#!/bin/sh\n# my own script\n")
    r = run(env, "off")
    assert r.returncode == 0
    assert policy.exists() and "was edited since: kept" in r.stdout
    assert handoff.exists() and "not ours: kept" in r.stdout
    assert not dropin.exists()


def test_status_reports_presence(box):
    d, env = box
    assert run(env, "status").stdout.count(": absent") == 3
    run(env, "on")
    assert run(env, "status").stdout.count(": present") == 3


def test_hooks_are_all_or_none(box):
    d, env = box
    env = dict(env)
    del env["MEMORYFRAME_SYSTEMD_DIR"]
    r = run(env, "on")
    assert r.returncode == 2 and "all three" in r.stderr
    assert not d["sbin"].exists() and not d["pol"].exists()


def test_boot_dir_test_mode_never_touches_real_etc(tmp_path):
    env = {k: v for k, v in os.environ.items() if not k.startswith("MEMORYFRAME_")}
    env["MEMORYFRAME_BOOT_DIR"] = str(tmp_path)
    for sub in ("on", "off"):
        r = run(env, sub)
        assert r.returncode == 0 and "test mode" in r.stdout and "skipped" in r.stdout


def test_boot_splash_off_and_handover_subcommand_run_it(box, tmp_path):
    d, env = box
    env = dict(env, MEMORYFRAME_BOOT_DIR=str(tmp_path / "boot"))
    sh = ROOT / "deploy" / "boot-splash.sh"
    r = run(env, "handover", "on", script=sh)
    assert r.returncode == 0, r.stdout + r.stderr
    assert all(f.exists() for f in files(d))
    r = run(env, "off", script=sh)                              # `off` also undoes the handover
    assert not any(f.exists() for f in files(d)), r.stdout + r.stderr


def test_uninstall_and_install_mention_the_handover():
    assert "handover on" in (ROOT / "deploy" / "install.sh").read_text()
    assert "boot-splash.sh" in (ROOT / "deploy" / "uninstall.sh").read_text()
    assert "swaybg" in (ROOT / "deploy" / "install.sh").read_text()


# ---------------- plymouth-handoff.sh ----------------

def fake_plymouth(tmp_path, running=True, theme="memoryframe"):
    log = tmp_path / "ply.log"
    ply = tmp_path / "plymouth"
    ply.write_text(f'#!/bin/sh\necho "$@" >> {log}\n[ "$1" = --ping ] && {{ {"exit 0" if running else "exit 1"}; }}\nexit 0\n')
    ply.chmod(0o755)
    setter = tmp_path / "pset"
    setter.write_text(f'#!/bin/sh\necho {theme}\n')
    setter.chmod(0o755)
    up = tmp_path / "uptime"
    up.write_text("12.34 99.0\n")
    env = dict(os.environ, MEMORYFRAME_PLYMOUTH=str(ply), MEMORYFRAME_PLYMOUTH_SET=str(setter), MEMORYFRAME_SETTLE_WAIT="0.05",
               MEMORYFRAME_UPTIME_FILE=str(up))
    return env, log


def test_handoff_sends_settle_then_quits_retaining_the_splash(tmp_path):
    env, log = fake_plymouth(tmp_path)
    r = subprocess.run(["sh", str(HANDOFF)], env=env, capture_output=True, text=True, timeout=30)
    assert r.returncode == 0
    assert log.read_text().splitlines() == ["--ping", "display-message --text=memoryframe-settle", "quit --retain-splash"]
    assert "uptime 12.34" in r.stdout and "splash retained" in r.stdout


def test_handoff_does_nothing_when_plymouth_is_not_running(tmp_path):
    env, log = fake_plymouth(tmp_path, running=False)
    r = subprocess.run(["sh", str(HANDOFF)], env=env, capture_output=True, text=True, timeout=30)
    assert r.returncode == 0 and log.read_text().splitlines() == ["--ping"]


def test_handoff_leaves_another_theme_to_lightdm(tmp_path):
    env, log = fake_plymouth(tmp_path, theme="pix")
    r = subprocess.run(["sh", str(HANDOFF)], env=env, capture_output=True, text=True, timeout=30)
    assert r.returncode == 0 and log.read_text().splitlines() == ["--ping"] and "not memoryframe" in r.stdout


def test_handoff_never_fails_lightdm(tmp_path):
    env, _ = fake_plymouth(tmp_path)
    env["MEMORYFRAME_PLYMOUTH"] = str(tmp_path / "no-such-client")
    r = subprocess.run(["sh", str(HANDOFF)], env=env, capture_output=True, text=True, timeout=30)
    assert r.returncode == 0
    (tmp_path / "plymouth").write_text('#!/bin/sh\n[ "$1" = quit ] && exit 1\nexit 0\n')
    env["MEMORYFRAME_PLYMOUTH"] = str(tmp_path / "plymouth")
    r = subprocess.run(["sh", str(HANDOFF)], env=env, capture_output=True, text=True, timeout=30)
    assert r.returncode == 0 and "failed" in r.stdout
