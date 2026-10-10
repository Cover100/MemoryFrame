"""Runs deploy/hotspot.sh against a stub nmcli / systemd-run (no real network changes)."""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "deploy" / "hotspot.sh"
HOME_WIFI = "netplan-wlan0-Oversclan_5G"

STUB_NMCLI = r'''#!/bin/bash
# Minimal stateful nmcli stub. State dir: $STUB_DIR
D="$STUB_DIR"; echo "nmcli $*" >> "$D/log"
case "$*" in
  "-g NAME connection show") cat "$D/profiles"; exit 0;;
  "-g GENERAL.CONNECTION device show wlan0") cat "$D/active"; exit 0;;
esac
[ "$1" = connection ] || [ "$1" = device ] || exit 0
case "$1 $2" in
  "connection add")
    n=""; ac=yes; a=("$@")
    for i in "${!a[@]}"; do
      [ "${a[$i]}" = con-name ] && n="${a[$((i+1))]}"
      [ "${a[$i]}" = autoconnect ] && ac="${a[$((i+1))]}"
    done
    echo "$n" >> "$D/profiles"; echo "$ac" > "$D/ac-$n";;
  "connection modify")
    n="$3"; a=("$@")
    for i in "${!a[@]}"; do [ "${a[$i]}" = connection.autoconnect ] && echo "${a[$((i+1))]}" > "$D/ac-$n"; done;;
  "connection up") echo "$3" > "$D/active";;
  "connection down") [ "$(cat "$D/active")" = "$3" ] && : > "$D/active";;
  "connection delete") grep -vx "$3" "$D/profiles" > "$D/p.tmp"; mv "$D/p.tmp" "$D/profiles"; rm -f "$D/ac-$3";;
  "device connect") echo "''' + HOME_WIFI + r'''" > "$D/active";;
esac
exit 0
'''

STUB_SYSTEMD_RUN = r'''#!/bin/bash
echo "systemd-run $*" >> "$STUB_DIR/log"
# Save the command so the test can "fire the timer" later.
shift 2; printf '%q ' "$@" > "$STUB_DIR/revert_cmd"
'''


@pytest.fixture
def stub(tmp_path):
    d = tmp_path / "stub"
    d.mkdir()
    (d / "profiles").write_text(f"{HOME_WIFI}\nHotspot\n")
    (d / f"ac-{HOME_WIFI}").write_text("yes\n")
    (d / "ac-Hotspot").write_text("no\n")
    (d / "active").write_text(HOME_WIFI + "\n")
    (d / "log").write_text("")
    for name, body in (("nmcli", STUB_NMCLI), ("systemd-run", STUB_SYSTEMD_RUN)):
        p = d / name
        p.write_text(body)
        p.chmod(0o755)
    conf = tmp_path / "hotspot.conf"
    conf.write_text('SSID="Frame"\nPASSWORD="good-long-pass-42"\n')
    env = dict(os.environ, STUB_DIR=str(d), NMCLI=str(d / "nmcli"), SUDO="",
               SYSTEMD_RUN=str(d / "systemd-run"), MEMORYFRAME_HOTSPOT_CONF=str(conf))
    return d, conf, env


def run(env, *args):
    return subprocess.run(["bash", str(SCRIPT), *args], env=env, stdin=subprocess.DEVNULL,
                          capture_output=True, text=True)


def read(d, name):
    return (d / name).read_text().strip()


pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")


def test_default_creates_profile_without_activating(stub):
    d, _, env = stub
    r = run(env)
    assert r.returncode == 0, r.stderr
    assert "MemoryFrame-hotspot" in read(d, "profiles").splitlines()
    assert read(d, "ac-MemoryFrame-hotspot") == "no"
    assert read(d, "active") == HOME_WIFI          # home Wi-Fi untouched
    log = read(d, "log")
    assert "connection up" not in log
    assert "autoconnect no" in log                  # created with autoconnect off from the start


def test_rerun_is_idempotent(stub):
    d, _, env = stub
    run(env)
    run(env)
    assert read(d, "profiles").splitlines().count("MemoryFrame-hotspot") == 1
    assert read(d, "log").count("connection add") == 1


def test_never_touches_other_profiles(stub):
    d, _, env = stub
    run(env)
    run(env, "--enable-at-boot")
    run(env, "--rollback")
    for line in read(d, "log").splitlines():
        assert HOME_WIFI not in line, line
        assert " Hotspot" not in line.replace("MemoryFrame-hotspot", ""), line
    assert read(d, f"ac-{HOME_WIFI}") == "yes"
    assert read(d, "ac-Hotspot") == "no"


@pytest.mark.parametrize("pw", ["short", "MemoryFrame123", "memoryframe123"])
def test_rejects_weak_passwords(stub, pw):
    d, conf, env = stub
    conf.write_text(f'SSID="Frame"\nPASSWORD="{pw}"\n')
    assert run(env).returncode != 0
    assert "MemoryFrame-hotspot" not in read(d, "profiles")


def test_enable_at_boot_sets_autoconnect_but_does_not_activate(stub):
    d, _, env = stub
    assert run(env, "--enable-at-boot").returncode == 0
    assert read(d, "ac-MemoryFrame-hotspot") == "yes"
    assert read(d, "active") == HOME_WIFI


def test_test_mode_schedules_revert_before_activation_and_revert_works(stub):
    d, _, env = stub
    r = run(env, "--test", "120")
    assert r.returncode == 0, r.stderr
    log = read(d, "log").splitlines()
    sr = [i for i, l in enumerate(log) if l.startswith("systemd-run")]
    up = [i for i, l in enumerate(log) if "connection up MemoryFrame-hotspot" in l]
    assert sr and up and sr[0] < up[0]              # revert armed before the switch
    assert "--on-active=120" in log[sr[0]]
    assert read(d, "active") == "MemoryFrame-hotspot"
    assert read(d, "ac-MemoryFrame-hotspot") == "no"
    # Fire the scheduled revert.
    subprocess.run(["bash", "-c", read(d, "revert_cmd")], env=env, check=True)
    assert read(d, "active") == HOME_WIFI


@pytest.mark.parametrize("arg", [[], ["abc"], ["5"], ["99999"]])
def test_test_mode_validates_seconds(stub, arg):
    d, _, env = stub
    assert run(env, "--test", *arg).returncode == 2
    assert "connection up" not in read(d, "log")


def test_rollback_restores_home_wifi(stub):
    d, _, env = stub
    run(env, "--test", "120")                       # hotspot is up
    r = run(env, "--rollback")
    assert r.returncode == 0, r.stderr
    assert "MemoryFrame-hotspot" not in read(d, "profiles")
    assert read(d, "active") == HOME_WIFI
    assert read(d, "profiles").splitlines() == [HOME_WIFI, "Hotspot"]


def test_rollback_when_absent_is_safe(stub):
    d, _, env = stub
    assert run(env, "--rollback").returncode == 0
    assert read(d, "profiles").splitlines() == [HOME_WIFI, "Hotspot"]
