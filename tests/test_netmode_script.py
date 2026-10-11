"""MF-36: deploy/netmode.sh against a stateful fake nmcli (no real network changes)."""
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "deploy" / "netmode.sh"
HOT = "MemoryFrame-hotspot"
HOME = "netplan-wlan0-Oversclan_5G"
OTHER = "Office"
SECRET = "hunter2-very-secret"

# Profiles: $D/p/<name>/{type,mode,ssid,autoconnect}. Active: $D/active. Failing ups: $D/fail.
FAKE_NMCLI = r'''#!/bin/bash
D="$STUB_DIR"; echo "nmcli $*" >> "$D/log"
[ "$1" = --wait ] && shift 2
case "$1 $2" in
  "-g NAME") ls "$D/p"; exit 0;;
  "-g GENERAL.CONNECTION") cat "$D/active"; exit 0;;
esac
if [ "$1" = -g ] && [ "$3 $4" = "connection show" ]; then
  f="$D/p/$5"; [ -d "$f" ] || exit 10
  case "$2" in
    connection.type) cat "$f/type";; 802-11-wireless.mode) cat "$f/mode";;
    802-11-wireless.ssid) cat "$f/ssid";; connection.autoconnect) cat "$f/autoconnect";;
    connection.timestamp) cat "$f/ts" 2>/dev/null || echo 0;;
  esac; exit 0
fi
case "$1 $2" in
  "connection modify")
    if [ "$4" = connection.id ]; then mv "$D/p/$3" "$D/p/$5"; else echo "$5" > "$D/p/$3/autoconnect"; fi;;
  "connection load")
    n="$(sed -n 's/^id=//p' "$3")"; mkdir -p "$D/p/$n"
    echo 802-11-wireless > "$D/p/$n/type"; echo ap > "$D/p/$n/mode"; echo no > "$D/p/$n/autoconnect"
    cp "$3" "$D/p/$n/keyfile";;
  "connection delete") rm -rf "$D/p/$3"; [ "$(cat "$D/active")" = "$3" ] && : > "$D/active";;
  "connection up")
    [ -d "$D/p/$3" ] || exit 10
    grep -qxF "$3" "$D/fail" 2>/dev/null && exit 4
    echo "$3" > "$D/active";;
  "connection down") [ "$(cat "$D/active")" = "$3" ] && : > "$D/active";;
esac
exit 0
'''


def profile(d, name, type_="802-11-wireless", mode="infrastructure", ssid=None, ac="yes"):
    p = d / "p" / name
    p.mkdir(parents=True)
    for k, v in (("type", type_), ("mode", mode), ("ssid", ssid or name), ("autoconnect", ac)):
        (p / k).write_text(v + "\n")


@pytest.fixture
def env(tmp_path):
    d = tmp_path / "stub"
    (d / "p").mkdir(parents=True)
    profile(d, HOT, mode="ap", ssid="MemoryFrame-AP", ac="no")
    profile(d, HOME, ssid="Oversclan_5G")
    (d / "p" / HOME / "ts").write_text("1760000000\n")       # most recently used
    profile(d, OTHER, ssid="OfficeNet")
    profile(d, "Wired connection 1", type_="802-3-ethernet")
    (d / "active").write_text(HOME + "\n")
    (d / "log").write_text("")
    nm = d / "nmcli"
    nm.write_text(FAKE_NMCLI)
    nm.chmod(0o755)
    boot, state = tmp_path / "boot", tmp_path / "run"
    boot.mkdir()
    (tmp_path / "boot_id").write_text("boot-1\n")
    e = dict(os.environ, STUB_DIR=str(d), MEMORYFRAME_NM_DIR=str(tmp_path / "nm"), NMCLI=str(nm), MEMORYFRAME_BOOT_DIR=str(boot),
             MEMORYFRAME_STATE_DIR=str(state), MEMORYFRAME_DEV_WAIT="1",
             MEMORYFRAME_BOOT_ID_FILE=str(tmp_path / "boot_id"),
             MEMORYFRAME_HOTSPOT_SH=str(tmp_path / "hotspot.sh"), PATH=f"{d}:{os.environ['PATH']}")
    return {"d": d, "boot": boot, "state": state, "env": e, "tmp": tmp_path}


def run(env, *args, input=None):
    return subprocess.run(["bash", str(SCRIPT), *args], env=env["env"], capture_output=True, text=True,
                          timeout=30, input=input)


def active(env):
    return (env["d"] / "active").read_text().strip()


def ac(env, name):
    return (env["d"] / "p" / name / "autoconnect").read_text().strip()


def state(env):
    return json.loads((env["state"] / "netmode.json").read_text())


def mutations(env):
    return [line for line in (env["d"] / "log").read_text().splitlines()
            if " modify " in line or " up " in line or " down " in line]


# ---------- product ----------

def test_product_mode_file_boots_product(env):
    (env["boot"] / "memoryframe-mode.txt").write_text("product\n")
    r = run(env, "boot")
    assert r.returncode == 0, r.stderr
    assert active(env) == HOT
    assert ac(env, HOME) == "no" and ac(env, OTHER) == "no" and ac(env, HOT) == "no"
    s = state(env)
    assert s == {"mode": "product", "requested": "product", "dev_fallback": False, "hotspot_error": False,
                 "active": HOT, "boot_id": "boot-1", "reset": False}


# ---------- SAFETY: nothing but an explicit "product" arms product mode ----------

@pytest.mark.parametrize("text", [None, "", "\n\n", "   \n", "nonsense\n", "prod\n"])
def test_missing_empty_or_unknown_mode_file_is_dev_and_touches_nothing(env, text):
    if text is not None:
        (env["boot"] / "memoryframe-mode.txt").write_text(text)
    for _ in range(2):                                        # two simulated reboots
        r = run(env, "boot")
        assert r.returncode == 0, r.stderr
    assert active(env) == HOME                                # home Wi-Fi kept
    assert ac(env, HOME) == "yes" and ac(env, OTHER) == "yes"
    assert mutations(env) == []                               # no up/down/modify at all
    assert state(env)["mode"] == "dev" and state(env)["requested"] == "none"
    after = (env["boot"] / "memoryframe-mode.txt")
    assert (after.read_text() if after.exists() else None) == text   # never written by boot


def test_status_shows_armed_vs_current(env):
    out = run(env, "status").stdout
    assert "armed for next boot: dev (no mode file" in out
    run(env, "boot")
    out = run(env, "status").stdout
    assert "current mode: dev" in out and f"active: {HOME}" in out
    (env["boot"] / "memoryframe-mode.txt").write_text("product\n")   # armed, not yet applied
    out = run(env, "status").stdout
    assert "armed for next boot: product" in out and "current mode: dev" in out
    assert active(env) == HOME


def test_dev_fallback_keeps_client_autoconnect(env):
    (env["boot"] / "memoryframe-mode.txt").write_text("dev\n")
    (env["d"] / "fail").write_text(HOME + "\n")
    (env["d"] / "active").write_text("\n")                 # home Wi-Fi down at boot
    run(env, "boot")
    assert active(env) == HOT and state(env)["dev_fallback"] is True
    assert ac(env, HOME) == "yes" and ac(env, OTHER) == "yes"   # next boot retries home Wi-Fi
    assert (env["boot"] / "memoryframe-mode.txt").read_text() == "dev\n"


def test_product_idempotent(env):
    (env["boot"] / "memoryframe-mode.txt").write_text("product\n")
    run(env, "boot")
    before = len(mutations(env))
    r = run(env, "boot")
    assert r.returncode == 0 and mutations(env)[before:] == []
    assert sorted(os.listdir(env["d"] / "p")) == sorted([HOT, HOME, OTHER, "Wired connection 1"])


@pytest.mark.parametrize("text", ["product", "PRODUCT\r\n", "  product  \n", "\nproduct\n"])
def test_mode_file_variants_mean_product(env, text):
    (env["boot"] / "memoryframe-mode.txt").write_text(text)
    run(env, "boot")
    assert active(env) == HOT and state(env)["mode"] == "product"


def test_hotspot_failure_recorded_no_error_exit_at_boot(env):
    (env["d"] / "fail").write_text(HOT + "\n")
    (env["boot"] / "memoryframe-mode.txt").write_text("product\n")
    r = run(env, "boot")
    assert r.returncode == 0                               # never fails the unit / no reboot loop
    s = state(env)
    assert s["hotspot_error"] is True and s["mode"] == "product"
    assert run(env, "product").returncode == 1            # from the shell: failure is visible


def test_missing_hotspot_profile_is_error_not_created(env):
    shutil.rmtree(env["d"] / "p" / HOT)
    (env["boot"] / "memoryframe-mode.txt").write_text("product\n")
    run(env, "boot")
    assert state(env)["hotspot_error"] is True
    assert HOT not in os.listdir(env["d"] / "p")


# ---------- dev ----------

def test_dev_from_boot_file_joins_last_used_profile(env):
    (env["d"] / "active").write_text(HOT + "\n")
    (env["boot"] / "memoryframe-mode.txt").write_text("dev\n")
    r = run(env, "boot")
    assert r.returncode == 0, r.stderr
    assert active(env) == HOME
    s = state(env)
    assert s["mode"] == "dev" and s["dev_fallback"] is False and s["hotspot_error"] is False


def test_dev_ssid_selects_profile(env):
    (env["boot"] / "memoryframe-mode.txt").write_text("dev\n")
    (env["boot"] / "memoryframe-dev-wifi.txt").write_text("ssid=OfficeNet\n")
    run(env, "boot")
    assert active(env) == OTHER and state(env)["mode"] == "dev"


def test_dev_idempotent(env):
    (env["boot"] / "memoryframe-mode.txt").write_text("dev\n")
    run(env, "boot")
    before = len(mutations(env))
    run(env, "boot")
    assert mutations(env)[before:] == []


@pytest.mark.parametrize("why", ["wrong_password", "unknown_ssid", "no_profiles"])
def test_dev_wifi_missing_falls_back_to_hotspot(env, why):
    (env["boot"] / "memoryframe-mode.txt").write_text("dev\n")
    if why == "wrong_password":
        (env["d"] / "fail").write_text(HOME + "\n")
    elif why == "unknown_ssid":
        (env["boot"] / "memoryframe-dev-wifi.txt").write_text("ssid=Nope\n")
    else:
        shutil.rmtree(env["d"] / "p" / HOME)
        shutil.rmtree(env["d"] / "p" / OTHER)
    (env["d"] / "active").write_text("\n")
    r = run(env, "boot")
    assert r.returncode == 0
    assert active(env) == HOT
    s = state(env)
    assert s["mode"] == "product" and s["requested"] == "dev" and s["dev_fallback"] is True


def test_password_line_ignored_warned_never_logged(env):
    (env["boot"] / "memoryframe-mode.txt").write_text("dev\n")
    (env["boot"] / "memoryframe-dev-wifi.txt").write_text(
        f"ssid=OfficeNet\npassword={SECRET}\nPSK = {SECRET}\nfoo={SECRET}\n")
    r = run(env, "boot")
    assert active(env) == OTHER
    assert "ignoring a 'password' line" in r.stderr and "ignoring a 'psk' line" in r.stderr
    assert "ignoring unknown key 'foo'" in r.stderr
    everything = r.stderr + r.stdout + (env["d"] / "log").read_text() + (env["state"] / "netmode.json").read_text()
    assert SECRET not in everything
    assert not any(" modify " in l and "psk" in l for l in mutations(env))   # never used


# ---------- shell path, recovery, locking ----------

def test_shell_switches_write_boot_file_and_never_password(env):
    assert run(env, "dev").returncode == 0 and active(env) == HOME
    assert (env["boot"] / "memoryframe-mode.txt").read_text() == "dev\n"
    assert run(env, "product").returncode == 0 and active(env) == HOT
    assert (env["boot"] / "memoryframe-mode.txt").read_text() == "product\n"
    for f in env["boot"].glob("memoryframe-*"):
        assert "password" not in f.read_text().lower()


def test_boot_file_returns_to_product_without_network(env):
    run(env, "dev")
    (env["boot"] / "memoryframe-mode.txt").write_text("product\n")
    run(env, "boot")
    assert active(env) == HOT
    run(env, "dev")
    (env["boot"] / "memoryframe-mode.txt").unlink()        # deleted on a PC: developer default
    run(env, "boot")
    assert active(env) == HOME                             # left as is (dev)


def test_reset_file_recreates_hotspot_and_forces_product(env):
    hs = env["tmp"] / "hotspot.sh"
    hs.write_text(f"#!/bin/bash\necho ran > {env['tmp']}/hotspot-ran\n")
    hs.chmod(0o755)
    (env["boot"] / "memoryframe-mode.txt").write_text("dev\n")
    (env["boot"] / "memoryframe-reset").write_text("")
    run(env, "boot")
    assert (env["tmp"] / "hotspot-ran").exists()
    assert not (env["boot"] / "memoryframe-reset").exists()
    assert (env["boot"] / "memoryframe-mode.txt").read_text() == "product\n"
    assert active(env) == HOT


def test_refused_while_wifi_change_pending(env):
    env["state"].mkdir()
    (env["state"] / "wifi-change-pending").write_text("")
    r = run(env, "dev")
    assert r.returncode == 3 and "pending" in r.stderr and active(env) == HOME


def test_refused_while_another_switch_runs(env):
    env["state"].mkdir()
    lock = env["state"] / "netmode.lock"
    holder = subprocess.Popen(["flock", str(lock), "sleep", "5"])
    try:
        import time
        time.sleep(0.3)
        r = run(env, "product")
        assert r.returncode == 3 and "in progress" in r.stderr
    finally:
        holder.kill()


def test_status_prints_mode_and_active(env):
    (env["d"] / "active").write_text(HOT + "\n")
    (env["d"] / "fail").write_text(HOME + "\n")
    (env["boot"] / "memoryframe-mode.txt").write_text("dev\n")
    run(env, "boot")
    out = run(env, "status").stdout
    assert "armed for next boot: dev" in out and "current mode: product" in out and f"active: {HOT}" in out
    assert "Developer Wi-Fi not found" in out or "developer Wi-Fi not found" in out


def test_bad_usage(env):
    assert run(env, "frobnicate").returncode == 2


def test_shellcheck_clean():
    sc = shutil.which("shellcheck")
    if not sc:
        pytest.skip("shellcheck not installed")
    r = subprocess.run([sc, str(SCRIPT)], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout


def test_service_unit():
    unit = (ROOT / "deploy" / "memoryframe-netmode.service").read_text()
    assert "Type=oneshot" in unit and "Before=memoryframe.service" in unit
    assert "memoryframe-netmode boot" in unit and "Restart=" not in unit


def test_sudoers_only_the_three_wifi_commands():
    rule = [l for l in (ROOT / "deploy" / "memoryframe-sudoers").read_text().splitlines()
            if l.strip() and not l.startswith("#")]
    assert len(rule) == 1
    cmds = [c.strip() for c in rule[0].split("NOPASSWD:", 1)[1].split(",")]
    assert cmds == [f"/usr/local/sbin/memoryframe-netmode {c}" for c in ("wifi-apply", "wifi-commit", "wifi-rollback")]
    assert "ALL=(root)" in rule[0] and "*" not in rule[0]


# ---------- MF-18 helper: wifi-apply / wifi-commit / wifi-rollback ----------

PENDING = "MemoryFrame-AP-pending"
NEWPW = "n3w pass\\word!"


@pytest.fixture
def on_hotspot(env):
    run(env, "boot")
    assert active(env) == HOT
    return env


def test_apply_reads_stdin_keeps_old_and_never_logs(on_hotspot):
    env = on_hotspot
    r = run(env, "wifi-apply", input=f"Café Frame\n{NEWPW}\n")
    assert r.returncode == 0, r.stderr
    assert active(env) == PENDING and HOT in os.listdir(env["d"] / "p")
    kf = (env["tmp"] / "nm" / f"{PENDING}.nmconnection")
    assert oct(kf.stat().st_mode & 0o777) == "0o600"
    body = kf.read_text()
    assert "psk=n3w pass\\\\word!" in body and "mode=ap" in body and "autoconnect=false" in body
    assert "ssid=67;97;102;195;169;32;70;114;97;109;101;" in body          # UTF-8 bytes, no escaping issues
    assert (env["state"] / "wifi-change-pending").exists()
    logs = r.stderr + (env["d"] / "log").read_text() + (env["state"] / "netmode.json").read_text()
    assert "n3w pass" not in logs and "Café" not in (env["d"] / "log").read_text()


def test_mode_switch_refused_while_pending(on_hotspot):
    env = on_hotspot
    run(env, "wifi-apply", input=f"New\n{NEWPW}\n")
    assert run(env, "dev").returncode == 3
    assert run(env, "wifi-apply", input=f"Other\n{NEWPW}\n").returncode == 3      # second change


def test_commit_leaves_exactly_one_ap_profile(on_hotspot):
    env = on_hotspot
    run(env, "wifi-apply", input=f"New\n{NEWPW}\n")
    r = run(env, "wifi-commit")
    assert r.returncode == 0, r.stderr
    aps = [n for n in os.listdir(env["d"] / "p") if (env["d"] / "p" / n / "mode").read_text().strip() == "ap"]
    assert aps == [HOT] and (env["d"] / "p" / HOT / "keyfile").exists()
    assert not (env["state"] / "wifi-change-pending").exists()
    assert run(env, "dev").returncode == 0                                  # switches allowed again


def test_rollback_restores_old_and_deletes_pending(on_hotspot):
    env = on_hotspot
    run(env, "wifi-apply", input=f"New\n{NEWPW}\n")
    assert run(env, "wifi-rollback").returncode == 0
    assert active(env) == HOT and PENDING not in os.listdir(env["d"] / "p")
    assert not (env["tmp"] / "nm" / f"{PENDING}.nmconnection").exists()
    assert not (env["state"] / "wifi-change-pending").exists()


def test_activation_failure_rolls_back(on_hotspot):
    env = on_hotspot
    (env["d"] / "fail").write_text(PENDING + "\n")
    r = run(env, "wifi-apply", input=f"New\n{NEWPW}\n")
    assert r.returncode == 4
    assert active(env) == HOT and PENDING not in os.listdir(env["d"] / "p")


@pytest.mark.parametrize("data", ["\nabcdefgh\n", "x" * 33 + "\nabcdefgh\n", "ok\nshort\n", "ok\n" + "a" * 64 + "\n",
                                  "ok\npässwort1\n", "a\tb\nabcdefgh\n"])
def test_apply_validates(on_hotspot, data):
    env = on_hotspot
    assert run(env, "wifi-apply", input=data).returncode == 2
    assert active(env) == HOT and not (env["state"] / "wifi-change-pending").exists()


def test_apply_refused_in_dev_mode(env):
    run(env, "dev")
    assert run(env, "wifi-apply", input=f"New\n{NEWPW}\n").returncode == 3


def test_commit_without_pending_refused(on_hotspot):
    assert run(on_hotspot, "wifi-commit").returncode == 4


def test_boot_rolls_back_unconfirmed_change(on_hotspot):
    env = on_hotspot
    run(env, "wifi-apply", input=f"New\n{NEWPW}\n")
    run(env, "boot")
    assert active(env) == HOT and PENDING not in os.listdir(env["d"] / "p")
    assert not (env["state"] / "wifi-change-pending").exists()


def test_reset_flag_in_state(env):
    (env["boot"] / "memoryframe-reset").write_text("")
    run(env, "boot")
    assert state(env)["reset"] is True
