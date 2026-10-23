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
if [ -f "$D/crash_at" ] && [[ "$*" == *" modify "* || "$*" == *" delete "* ]]; then   # power cut
  n=$(( $(cat "$D/crash_at") - 1 )); echo "$n" > "$D/crash_at"
  if [ "$n" -lt 0 ]; then rm -f "$D/crash_at"; kill -9 "$PPID"; exit 137; fi
fi
[ "$1" = --wait ] && shift 2
[ "$1" = -e ] && shift 2
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
    connection.uuid) cat "$f/uuid" 2>/dev/null || echo "uuid-$5";;
    connection.interface-name) cat "$f/ifname" 2>/dev/null;;
  esac; exit 0
fi
case "$1 $2" in
  "connection modify")
    if [ "$4" = connection.id ]; then
      mv "$D/p/$3" "$D/p/$5"; [ "$(cat "$D/active")" = "$3" ] && echo "$5" > "$D/active"
    else echo "$5" > "$D/p/$3/autoconnect"; [ -n "$7" ] && echo "$7" > "$D/p/$3/prio"; fi;;
  "connection load")
    n="$(sed -n 's/^id=//p' "$3")"; mkdir -p "$D/p/$n"
    echo 802-11-wireless > "$D/p/$n/type"; echo ap > "$D/p/$n/mode"; echo no > "$D/p/$n/autoconnect"
    echo "uuid-new-$RANDOM$RANDOM" > "$D/p/$n/uuid"
    cp "$3" "$D/p/$n/keyfile";;
  "connection delete") rm -rf "$D/p/$3"; [ "$(cat "$D/active")" = "$3" ] && : > "$D/active";;
  "connection up")
    [ -d "$D/p/$3" ] || exit 10
    grep -qxF "$3" "$D/fail" 2>/dev/null && { : > "$D/active"; exit 4; }   # NM drops the old one
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
    for name, body in (("iw", '#!/bin/bash\ncat "$STUB_DIR/stations" 2>/dev/null\n'),
                       ("systemd-run", '#!/bin/bash\necho "systemd-run $*" >> "$STUB_DIR/sdrun"\n')):
        (d / name).write_text(body)
        (d / name).chmod(0o755)
    nm = d / "nmcli"
    nm.write_text(FAKE_NMCLI)
    nm.chmod(0o755)
    boot, state = tmp_path / "boot", tmp_path / "run"
    boot.mkdir()
    (tmp_path / "boot_id").write_text("boot-1\n")
    e = dict(os.environ, STUB_DIR=str(d), MEMORYFRAME_NM_DIR=str(tmp_path / "nm"), NMCLI=str(nm), MEMORYFRAME_BOOT_DIR=str(boot),
             MEMORYFRAME_STATE_DIR=str(state), MEMORYFRAME_DEV_WAIT="1",
             MEMORYFRAME_BOOT_ID_FILE=str(tmp_path / "boot_id"),
             MEMORYFRAME_HOTSPOT_SH=str(tmp_path / "hotspot.sh"), IW=str(d / "iw"),
             SYSTEMD_RUN=str(d / "systemd-run"), MEMORYFRAME_VAR_DIR=str(tmp_path / "var"),
             MEMORYFRAME_CONFIRM_POLL="0.1", PATH=f"{d}:{os.environ['PATH']}")
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


def after_product(env):
    """Product mode was applied before (all client autoconnect off): boot 'dev' must undo it."""
    (env["d"] / "p" / HOT / "autoconnect").write_text("yes\n")    # hotspot is the preferred boot connection


def mutations(env):
    return [line for line in (env["d"] / "log").read_text().splitlines()
            if " modify " in line or " up " in line or " down " in line]


# ---------- product ----------

def test_product_mode_file_boots_product(env):
    (env["boot"] / "memoryframe-mode.txt").write_text("product\n")
    r = run(env, "boot")
    assert r.returncode == 0, r.stderr
    assert active(env) == HOT
    assert ac(env, HOME) == "yes" and ac(env, OTHER) == "yes"      # clients never modified
    assert ac(env, HOT) == "yes" and (env["d"] / "p" / HOT / "prio").read_text().strip() == "100"
    s = state(env)
    assert s == {"mode": "product", "requested": "product", "dev_fallback": False, "hotspot_error": False,
                 "active": HOT, "boot_id": "boot-1", "reset": False, "reset": False,
                 "hotspot_uuid": "uuid-MemoryFrame-hotspot", "wifi_profiles": False, "commit_recovered": False,
                 "hotspot_ssid": "MemoryFrame-AP"}


# ---------- SAFETY: nothing but an explicit "product" arms product mode ----------

@pytest.mark.parametrize("text", [None, "", "\n\n", "   \n", "nonsense\n", "prod\n"])
def test_missing_empty_or_unknown_mode_file_is_product(env, text):   # MF-51
    if text is not None:
        (env["boot"] / "memoryframe-mode.txt").write_text(text)
    for _ in range(2):                                        # two simulated reboots
        r = run(env, "boot")
        assert r.returncode == 0, r.stderr
    assert active(env) == HOT                                 # MF-51: product by default
    assert ac(env, HOME) == "yes" and ac(env, OTHER) == "yes"
    assert foreign_modifies(env) == []
    assert state(env)["mode"] == "product"
    after = (env["boot"] / "memoryframe-mode.txt")
    assert (after.read_text() if after.exists() else None) == text   # never written by boot


def test_status_shows_armed_vs_current(env):
    out = run(env, "status").stdout
    assert "armed for next boot: product (no mode file" in out
    run(env, "boot")
    out = run(env, "status").stdout
    assert "current mode: product" in out and f"active: {HOT}" in out
    (env["boot"] / "memoryframe-mode.txt").write_text("dev\n")   # armed, not yet applied
    out = run(env, "status").stdout
    assert "armed for next boot: dev" in out and "current mode: product" in out
    assert active(env) == HOT


def test_dev_fallback_keeps_client_autoconnect(env):
    after_product(env)
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
    after_product(env)
    (env["d"] / "active").write_text(HOT + "\n")
    (env["boot"] / "memoryframe-mode.txt").write_text("dev\n")
    r = run(env, "boot")
    assert r.returncode == 0, r.stderr
    assert active(env) == HOME
    s = state(env)
    assert s["mode"] == "dev" and s["dev_fallback"] is False and s["hotspot_error"] is False


def test_dev_ssid_selects_profile(env):
    after_product(env)
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
    after_product(env)
    r = run(env, "boot")
    assert r.returncode == 0
    assert active(env) == HOT
    s = state(env)
    assert s["mode"] == "product" and s["requested"] == "dev" and s["dev_fallback"] is True


def test_password_line_ignored_warned_never_logged(env):
    after_product(env)
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
    (env["boot"] / "memoryframe-mode.txt").unlink()        # deleted on a PC: MF-51 product default
    run(env, "boot")
    assert active(env) == HOT


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
    after_product(env)
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


# ---------- MF-18 helper: wifi-apply / wifi-commit / wifi-rollback ----------

PENDING = "MemoryFrame-AP-pending"
NEWPW = "n3w pass\\word!"


@pytest.fixture
def on_hotspot(env):
    (env["boot"] / "memoryframe-mode.txt").write_text("product\n")
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


@pytest.mark.parametrize("state_file", ["missing", "garbage", "dev"])
def test_apply_refused_unless_product_applied(env, state_file):
    """#41 SAFETY: a missing netmode.json means dev: the helper does nothing to home Wi-Fi."""
    env["state"].mkdir(parents=True, exist_ok=True)
    if state_file == "garbage":
        (env["state"] / "netmode.json").write_text("{nope")
    elif state_file == "dev":
        (env["boot"] / "memoryframe-mode.txt").write_text("dev\n")
        run(env, "boot")                                     # dev file -> home Wi-Fi
    for cmd in ("wifi-apply", "wifi-rollback", "wifi-commit"):
        r = run(env, cmd, input=f"New\n{NEWPW}\n")
        assert r.returncode != 0
    assert active(env) == HOME and mutations(env) == []
    assert ac(env, HOME) == "yes" and PENDING not in os.listdir(env["d"] / "p")
    assert not (env["state"] / "wifi-change-pending").exists()


def test_reset_flag_in_state(env):
    (env["boot"] / "memoryframe-reset").write_text("")
    run(env, "boot")
    assert state(env)["reset"] is True




# ---------- SAFETY: the boot service is a strict no-op on a dev Pi ----------

@pytest.mark.parametrize("text,want", [(None, HOT), ("dev\n", HOME), ("", HOT), ("junk\n", HOT)])
def test_boot_service_dev_file_or_product(env, text, want):   # MF-51
    if text is not None:
        (env["boot"] / "memoryframe-mode.txt").write_text(text)
    for _ in range(3):
        assert run(env, "boot").returncode == 0
    assert foreign_modifies(env) == [] and active(env) == want
    assert ac(env, HOME) == "yes" and ac(env, OTHER) == "yes"


def test_boot_dev_undoes_previous_product(env):
    (env["boot"] / "memoryframe-mode.txt").write_text("product\n")
    run(env, "boot")
    assert active(env) == HOT and ac(env, HOT) == "yes" and ac(env, HOME) == "yes"
    (env["boot"] / "memoryframe-mode.txt").write_text("dev\n")   # edited on a PC
    run(env, "boot")
    assert active(env) == HOME and ac(env, HOT) == "no" and ac(env, HOME) == "yes" and ac(env, OTHER) == "yes"



# ---------- Tester #40 (2): never modify a non-MemoryFrame profile ----------

def foreign_modifies(env):
    return [l for l in (env["d"] / "log").read_text().splitlines()
            if " modify " in l and not l.split(" modify ", 1)[1].startswith("MemoryFrame-")]


def test_never_modifies_client_profiles_in_any_path(env):
    b = env["boot"] / "memoryframe-mode.txt"
    run(env, "product")                                       # shell product
    run(env, "boot")                                          # product boot
    run(env, "dev")                                           # shell dev
    b.write_text("product\n"); run(env, "boot")
    b.write_text("dev\n"); run(env, "boot")                    # undo product from the boot partition
    (env["d"] / "fail").write_text(HOME + "\n"); run(env, "dev")   # dev fallback to hotspot
    (env["d"] / "fail").write_text("")
    (env["boot"] / "memoryframe-reset").write_text(""); run(env, "boot")   # recovery
    b.unlink(); run(env, "boot")
    assert any(" modify MemoryFrame-hotspot" in l for l in (env["d"] / "log").read_text().splitlines())
    assert foreign_modifies(env) == []
    assert ac(env, HOME) == "yes" and ac(env, OTHER) == "yes"


# ---------- Tester #40 (3): hotspot first, verified; on failure home Wi-Fi stays ----------

def test_hotspot_fails_home_wifi_restored_nothing_else_touched(env):
    (env["d"] / "fail").write_text(HOT + "\n")
    r = run(env, "product")
    assert r.returncode == 1
    assert active(env) == HOME                                # previous connection restored
    assert ac(env, HOT) == "no" and ac(env, HOME) == "yes"    # hotspot not made preferred
    assert foreign_modifies(env) == [] and state(env)["hotspot_error"] is True
    assert not (env["d"] / "sdrun").exists()


def test_hotspot_verified_before_it_is_made_preferred(env):
    run(env, "product")
    log = (env["d"] / "log").read_text().splitlines()
    up = next(i for i, l in enumerate(log) if f"up {HOT}" in l)
    mod = next(i for i, l in enumerate(log) if f"modify {HOT}" in l)
    assert up < mod


# ---------- MF-51: product mode never falls back (no no-phone timer) ----------

def test_product_never_falls_back_without_phone(env):
    run(env, "product")
    for _ in range(2):
        run(env, "boot")
    assert active(env) == HOT and ac(env, HOT) == "yes"
    assert not (env["d"] / "sdrun").exists()                 # no watch/timer is ever started
    assert run(env, "product-watch").returncode == 2         # subcommand removed
    assert (env["boot"] / "memoryframe-mode.txt").read_text().strip() == "product"
    assert "CONFIRM_MIN" not in SCRIPT.read_text() and "product-watch" not in SCRIPT.read_text()


# ---------- Tester #41 ----------

def test_apply_requires_active_hotspot(on_hotspot):
    env = on_hotspot
    (env["d"] / "active").write_text(HOME + "\n")            # product applied, but wlan0 is on home Wi-Fi
    r = run(env, "wifi-apply", input=f"New\n{NEWPW}\n")
    assert r.returncode == 3 and "not on MemoryFrame-hotspot" in r.stderr
    assert active(env) == HOME and PENDING not in os.listdir(env["d"] / "p")
    assert not (env["state"] / "wifi-change-pending").exists()


def test_rollback_restores_the_previously_active_connection(on_hotspot):
    env = on_hotspot
    run(env, "wifi-apply", input=f"New\n{NEWPW}\n")
    assert (env["state"] / "wifi-change-pending").read_text().strip() == HOT
    assert run(env, "wifi-rollback").returncode == 0
    assert active(env) == HOT
    # whatever was recorded is what comes back
    run(env, "wifi-apply", input=f"New\n{NEWPW}\n")
    (env["state"] / "wifi-change-pending").write_text(OTHER + "\n")
    assert run(env, "wifi-rollback").returncode == 0
    assert active(env) == OTHER


def test_commit_renames_before_deleting(on_hotspot):
    env = on_hotspot
    run(env, "wifi-apply", input=f"New\n{NEWPW}\n")
    before = len((env["d"] / "log").read_text().splitlines())
    assert run(env, "wifi-commit").returncode == 0
    steps = [l for l in (env["d"] / "log").read_text().splitlines()[before:] if " modify " in l or " delete " in l]
    i_new = next(i for i, l in enumerate(steps) if f"modify {PENDING} connection.id {HOT}" in l)
    i_del = next(i for i, l in enumerate(steps) if " delete " in l)
    assert i_new < i_del and f"delete MemoryFrame-hotspot-old" in steps[i_del]
    assert sorted(n for n in os.listdir(env["d"] / "p") if n.startswith("MemoryFrame")) == [HOT]


@pytest.mark.parametrize("cut", [0, 1, 2, 3])
def test_power_cut_during_commit_never_leaves_no_hotspot(on_hotspot, cut):
    env = on_hotspot
    run(env, "wifi-apply", input=f"New\n{NEWPW}\n")
    (env["d"] / "crash_at").write_text(str(cut))
    run(env, "wifi-commit")                                  # killed at the cut-th modify/delete
    names = os.listdir(env["d"] / "p")
    assert any(n in names for n in (HOT, "MemoryFrame-hotspot-old", PENDING))   # some AP profile survives
    (env["d"] / "crash_at").unlink(missing_ok=True)
    (env["d"] / "active").write_text("\n")                  # reboot
    run(env, "boot")
    aps = sorted(n for n in os.listdir(env["d"] / "p") if n.startswith("MemoryFrame"))
    assert aps == [HOT] and active(env) == HOT



def test_boot_finishing_a_commit_records_it_for_the_app(on_hotspot):
    env = on_hotspot
    r = run(env, "wifi-apply", input=f"New\n{NEWPW}\n")
    uuid = r.stdout.strip().split("uuid=", 1)[1]
    assert uuid.startswith("uuid-new-")
    (env["d"] / "crash_at").write_text("2")                  # cut after pending -> hotspot rename
    run(env, "wifi-commit")
    (env["d"] / "crash_at").unlink(missing_ok=True)
    (env["d"] / "active").write_text("\n")
    run(env, "boot")
    s = state(env)
    assert s["commit_recovered"] is True and s["hotspot_uuid"] == uuid and s["wifi_profiles"] is False


def test_boot_undoing_a_commit_is_not_recovered(on_hotspot):
    env = on_hotspot
    run(env, "wifi-apply", input=f"New\n{NEWPW}\n")
    (env["d"] / "crash_at").write_text("1")                  # cut after old -> -old rename only
    run(env, "wifi-commit")
    (env["d"] / "crash_at").unlink(missing_ok=True)
    (env["d"] / "active").write_text("\n")
    run(env, "boot")
    s = state(env)
    assert s["commit_recovered"] is False and s["wifi_profiles"] is False


# ---------- #89: netmode.json carries the SSID the hotspot profile really broadcasts ----------

def test_state_has_hotspot_ssid_after_an_nmcli_edit(env):
    (env["boot"] / "memoryframe-mode.txt").write_text("product\n")
    assert run(env, "boot").returncode == 0
    assert state(env)["hotspot_ssid"] == "MemoryFrame-AP"
    (env["d"] / "p" / HOT / "ssid").write_text("Edited: by \\ hand\n")      # nmcli connection modify ... ssid
    assert run(env, "boot").returncode == 0                                  # any state write refreshes it
    assert state(env)["hotspot_ssid"] == "Edited: by \\ hand"                # raw: ':' and '\' not escaped
    assert "-e no -g 802-11-wireless.ssid" in (env["d"] / "log").read_text()


def test_state_hotspot_ssid_empty_without_profile(env):
    import shutil as _sh
    _sh.rmtree(env["d"] / "p" / HOT)
    (env["boot"] / "memoryframe-mode.txt").write_text("product\n")
    run(env, "boot")
    assert state(env)["hotspot_ssid"] == ""


def test_state_never_contains_a_password(env):
    (env["boot"] / "memoryframe-mode.txt").write_text("product\n")
    (env["d"] / "p" / HOT / "psk").write_text(SECRET + "\n")
    run(env, "boot")
    assert SECRET not in (env["state"] / "netmode.json").read_text()
    assert "802-11-wireless-security" not in (env["d"] / "log").read_text()
    assert "show-secrets" not in (env["d"] / "log").read_text()


@pytest.mark.parametrize("raw,expect", [
    (b"Esc\x1bName", ""), (b"Bad\xffByte", ""), (b"a\x1bb", ""), (b"a\x01b", ""), (b"caf\xe9", ""),
    (b"del\x7fname", ""), (b"Tab\tName", ""), (b"x" * 33, ""),
    (b'Fam "home"', 'Fam "home"'), (b"back\\slash", "back\\slash"), ("Oma's Fotos ü".encode(), "Oma's Fotos ü"),
])
def test_odd_ssid_never_corrupts_state(env, raw, expect):
    """Juniper's #105 must-fix: the real script keeps netmode.json valid JSON, other flags survive."""
    (env["boot"] / "memoryframe-mode.txt").write_text("product\n")
    (env["d"] / "p" / HOT / "ssid").write_bytes(raw + b"\n")
    (env["d"] / "fail").write_text(HOT + "\n")                     # hotspot_error must survive too
    run(env, "boot")
    data = (env["state"] / "netmode.json").read_bytes()
    s = json.loads(data.decode("utf-8"))                             # strict: valid UTF-8 and JSON
    assert s["mode"] == "product" and s["hotspot_error"] is True and s["boot_id"] == "boot-1"
    assert s["hotspot_ssid"] == expect


def test_newline_ssid_is_dropped(env):
    (env["boot"] / "memoryframe-mode.txt").write_text("product\n")
    (env["d"] / "p" / HOT / "ssid").write_bytes(b"two\nlines\n")
    run(env, "boot")
    s = json.loads((env["state"] / "netmode.json").read_text())
    assert s["mode"] == "product" and s["hotspot_ssid"] == ""


def test_json_str_strips_controls_for_every_field(env):
    """The 'active' field (connection name) can't corrupt the file either."""
    r = subprocess.run(["bash", "-c", f'source <(sed -n "/^utf8_ok()/,/^}}/p;/^json_str()/,/^}}/p" {SCRIPT}); '
                       'json_str "$(printf "A\\x1bB\\tC\\"D%sE" "\\\\")"; echo; json_str "$(printf "bad\\xff")"'],
                       capture_output=True, text=True, env=env["env"])
    a, b = r.stdout.splitlines()
    assert json.loads(a) == 'AB C"D\\E' and json.loads(b) == ""
