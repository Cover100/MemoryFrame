"""Follow-up to #62 (Juniper's review): as root, netmode.sh and hotspot.sh ignore every env hook and
use a fixed PATH; the reset path runs a root-owned hotspot.sh copy, and hotspot.conf is parsed, never sourced."""
import os
import subprocess
from pathlib import Path

from test_hotspot_script import run as hs_run, read, stub  # noqa: F401  (fixture)

ROOT = Path(__file__).resolve().parents[1]
NETMODE = ROOT / "deploy" / "netmode.sh"


def _hook_env(tmp_path):
    mark = tmp_path / "hook-ran"
    evil = tmp_path / "evil"
    evil.write_text(f"#!/bin/sh\necho \"$0 $*\" >> {mark}\nexit 0\n")
    evil.chmod(0o755)
    bindir = tmp_path / "bin"; bindir.mkdir()
    for n in ("nmcli", "iw", "systemd-run", "logger", "flock"):
        (bindir / n).symlink_to(evil)
    e = dict(os.environ, NMCLI=str(evil), IW=str(evil), SYSTEMD_RUN=str(evil), SUDO=str(evil),
             MEMORYFRAME_HOTSPOT_SH=str(evil), MEMORYFRAME_BOOT_DIR=str(tmp_path / "boot"),
             MEMORYFRAME_STATE_DIR=str(tmp_path / "run"), PATH=f"{bindir}:{os.environ['PATH']}")
    return e, mark


def test_netmode_as_root_ignores_env_hooks_and_path(tmp_path):
    e, mark = _hook_env(tmp_path)
    e["MEMORYFRAME_AS_ROOT_TEST"] = "1"
    subprocess.run(["bash", str(NETMODE), "status"], env=e, capture_output=True, text=True, timeout=30)
    assert not mark.exists()                                  # no hook and no PATH-planted binary ran
    assert not (tmp_path / "run").exists() and not (tmp_path / "boot").exists()


def test_netmode_non_root_still_honours_test_hooks(tmp_path):
    e, mark = _hook_env(tmp_path)
    subprocess.run(["bash", str(NETMODE), "status"], env=e, capture_output=True, text=True, timeout=30)
    assert mark.exists()                                      # sanity: the same setup does reach the stub


def test_netmode_root_block_is_first_and_fixed():
    s = NETMODE.read_text()
    blk = s.split('if [ "$(id -u)" = 0 ]', 1)[1].split("\nfi\n", 1)[0]
    assert "export PATH=/usr/sbin:/usr/bin:/sbin:/bin" in blk
    assert "MEMORYFRAME_*|NMCLI|IW|SYSTEMD_RUN|SUDO) unset" in blk
    assert "MEMORYFRAME_HOTSPOT_SH=/usr/local/sbin/memoryframe-hotspot" in blk
    assert s.index('if [ "$(id -u)" = 0 ]') < s.index('NMCLI="${NMCLI:-nmcli}"')


def test_root_owned_hotspot_copy_and_conf_installed_and_unit_has_no_hook():
    inst = (ROOT / "deploy" / "install.sh").read_text()
    assert "install -o root -g root -m 0755 \"$APP_DIR/deploy/hotspot.sh\" /usr/local/sbin/memoryframe-hotspot" in inst
    assert "-m 0600 \"$APP_DIR/deploy/hotspot.conf\" /etc/memoryframe/hotspot.conf" in inst
    assert "MEMORYFRAME_HOTSPOT_SH" not in (ROOT / "deploy" / "memoryframe-netmode.service").read_text()
    assert "/usr/local/sbin/memoryframe-hotspot" in (ROOT / "deploy" / "uninstall.sh").read_text()


def test_hotspot_as_root_ignores_hooks(tmp_path, stub):
    d, conf, env = stub
    e, mark = _hook_env(tmp_path)
    env = dict(env, NMCLI=e["NMCLI"], SUDO=e["SUDO"], PATH=e["PATH"], MEMORYFRAME_AS_ROOT_TEST="1")
    hs_run(env)
    assert not mark.exists()
    assert "Frame" not in read(d, "log")                      # the test conf (hook) was not used either


def test_hotspot_conf_is_parsed_not_sourced(tmp_path, stub):
    d, conf, env = stub
    boom = tmp_path / "boom"
    conf.write_text(
        "# comment\n"
        f'SSID="Fr $(id) `id`"\n'
        "PASSWORD='good-long-pass-42'\r\n"
        f"touch {boom}\n"
        f"EVIL=`touch {boom}`\n"
        "  CHANNEL=11\n")
    r = hs_run(env)
    assert r.returncode == 0, r.stderr
    assert not boom.exists()
    log = read(d, "log")
    assert "802-11-wireless.ssid Fr $(id) `id`" in log      # literal value, never executed
    assert "802-11-wireless.channel 11" in log and "wifi-sec.psk good-long-pass-42 " in log + " "
    assert "unknown key EVIL" in r.stderr and "not KEY=value" in r.stderr


def test_hotspot_conf_bad_values_rejected(stub):
    d, conf, env = stub
    conf.write_text('SSID="Frame"\nPASSWORD="good-long-pass-42"\nIFACE="wlan0; reboot"\n')
    r = hs_run(env)
    assert r.returncode == 1 and "bad IFACE" in r.stderr
