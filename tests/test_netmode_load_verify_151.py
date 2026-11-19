"""#151: `nmcli connection load` exits 0 on the Pi even when it prints "Could not load file". netmode.sh
must check that the profile really is in NetworkManager after each load (like hotspot_saved does), not
trust the exit code: the wifi-change (wifi-apply) load and the home-wifi load."""
import json
import os

import pytest

from test_netmode_keyfiles import HCON, SENT, dev_fallback_on_hotspot
from test_netmode_script import HOT, NEWPW, PENDING, active, env, keyfiles, on_hotspot, run, state  # noqa: F401

HPEND = "MemoryFrame-home-pending"


def nm_log(env):
    return (env["d"] / "log").read_text()


def ups(env, name):
    return [l for l in nm_log(env).splitlines() if l.endswith(f"connection up {name}")]


# ---------- wifi-apply (MF-18 hotspot change) ----------

@pytest.mark.parametrize("filename_column", [True, False], ids=["filename", "no-filename"])
def test_wifi_apply_load_ok_passes(on_hotspot, filename_column):
    env = on_hotspot
    if not filename_column:
        (env["d"] / "nofilename").write_text("")   # the Pi's nmcli has no FILENAME column
    r = run(env, "wifi-apply", input=f"New\n{NEWPW}\n")
    assert r.returncode == 0, r.stderr
    assert active(env) == PENDING and "did not load" not in r.stderr
    [kf] = keyfiles(env, PENDING)
    assert (env["d"] / "p" / PENDING / "file").read_text().strip() == str(kf)
    assert r.stdout.startswith("uuid=uuid-new-")


@pytest.mark.parametrize("fault", ["loadsilent", "loadbadid"])
@pytest.mark.parametrize("filename_column", [True, False], ids=["filename", "no-filename"])
def test_wifi_apply_load_rc0_but_profile_missing_fails_loudly(on_hotspot, fault, filename_column):
    env = on_hotspot
    if not filename_column:
        (env["d"] / "nofilename").write_text("")
    (env["d"] / fault).write_text(f"{PENDING}-*.nmconnection")   # loadbadid: NM took it under another name
    r = run(env, "wifi-apply", input=f"New\n{NEWPW}\n")
    assert r.returncode == 4                                       # same exit as a load that returns non-zero
    assert "wifi change: NetworkManager did not load the new profile" in r.stderr
    assert "nmcli load exited 0" in r.stderr and NEWPW not in r.stderr
    assert ups(env, PENDING) == []                                 # never tried to bring it up
    assert f"connection down {HOT}" not in nm_log(env).split("connection load")[-1]   # hotspot never taken down
    assert active(env) == HOT and not [n for n in os.listdir(env["d"] / "p") if n.startswith(PENDING)]
    assert keyfiles(env, PENDING) == []                            # the unloaded file (holds the password) removed
    assert not (env["state"] / "wifi-change-pending").exists()
    assert state(env)["mode"] == "product"
    assert "wifi change rolled back" in r.stderr


# ---------- home-wifi (#68, dev fallback) ----------

def _request(env, ssid="Oversclan_5G"):
    rq = env["tmp"] / "app" / "home-wifi"
    rq.parent.mkdir(mode=0o700, exist_ok=True)
    rq.write_text(f"v1\n{ssid.encode().hex()}\n{SENT}\n")
    env["env"]["MEMORYFRAME_HOME_REQ"] = str(rq)
    env["env"]["MEMORYFRAME_HOME_WAIT"] = "1"
    return run(env, "home-wifi", "--request")


def _home(env):
    return json.loads((env["state"] / "home-wifi.json").read_text())


@pytest.mark.parametrize("filename_column", [True, False], ids=["filename", "no-filename"])
def test_home_wifi_load_ok_passes(env, filename_column):
    dev_fallback_on_hotspot(env)
    if not filename_column:
        (env["d"] / "nofilename").write_text("")
    r = _request(env)
    assert r.returncode == 0, r.stderr
    assert _home(env)["state"] == "saved" and "did not load" not in r.stderr
    assert len(ups(env, HPEND)) == 1 and HCON in os.listdir(env["d"] / "p")


@pytest.mark.parametrize("fault", ["loadsilent", "loadbadid"])
@pytest.mark.parametrize("filename_column", [True, False], ids=["filename", "no-filename"])
def test_home_wifi_load_rc0_but_profile_missing_fails_loudly(env, fault, filename_column):
    dev_fallback_on_hotspot(env)
    if not filename_column:
        (env["d"] / "nofilename").write_text("")
    (env["d"] / fault).write_text(f"{HPEND}-*.nmconnection")
    r = _request(env)
    assert r.returncode == 4
    assert "home-wifi: NetworkManager did not load the profile" in r.stderr
    assert "nmcli load exited 0" in r.stderr and SENT not in r.stderr
    h = _home(env)
    assert h["state"] == "failed" and h["reason"] == "connect_failed"
    assert ups(env, HPEND) == []                                   # never tried
    assert active(env) == HOT                                      # hotspot kept
    assert HCON not in os.listdir(env["d"] / "p") and HPEND not in os.listdir(env["d"] / "p")
    assert keyfiles(env, HPEND) == [] and keyfiles(env, HCON) == []   # nothing saved, no password left behind
    assert not [n for n in os.listdir(env["d"] / "p") if n.startswith(HPEND)]   # whatever NM took is unloaded


def test_script_checks_every_client_load():
    """Every `connection load` of a new pending keyfile is followed by a profile check (#151)."""
    src = (os.path.dirname(__file__) + "/../deploy/netmode.sh")
    text = open(src).read()
    assert text.count('profile_loaded "$f" "$PENDING_CON"') == 1
    assert text.count('profile_loaded "$f" "$HOME_PENDING"') == 1


# ---------- Hale's #151 review nit: two profiles with the same id ----------

@pytest.mark.parametrize("filename_column", [True, False], ids=["filename", "no-filename"])
def test_wifi_apply_two_profiles_with_the_pending_id_fails_loudly(on_hotspot, filename_column):
    env = on_hotspot
    if not filename_column:
        (env["d"] / "nofilename").write_text("")
    (env["d"] / "dupname").write_text(PENDING + "\n")          # NM lists a second MemoryFrame-AP-pending
    r = run(env, "wifi-apply", input=f"New\n{NEWPW}\n")
    assert r.returncode == 4
    assert f"2 NetworkManager profiles are named {PENDING} (expected exactly 1)" in r.stderr
    assert "wifi change: NetworkManager did not load the new profile" in r.stderr and NEWPW not in r.stderr
    assert ups(env, PENDING) == [] and active(env) == HOT
    assert keyfiles(env, PENDING) == [] and not (env["state"] / "wifi-change-pending").exists()


@pytest.mark.parametrize("filename_column", [True, False], ids=["filename", "no-filename"])
def test_home_wifi_two_profiles_with_the_pending_id_fails_loudly(env, filename_column):
    dev_fallback_on_hotspot(env)
    if not filename_column:
        (env["d"] / "nofilename").write_text("")
    (env["d"] / "dupname").write_text(HPEND + "\n")
    r = _request(env)
    assert r.returncode == 4
    assert f"2 NetworkManager profiles are named {HPEND} (expected exactly 1)" in r.stderr
    assert "home-wifi: NetworkManager did not load the profile" in r.stderr and SENT not in r.stderr
    assert _home(env)["state"] == "failed" and ups(env, HPEND) == [] and active(env) == HOT
    assert keyfiles(env, HPEND) == [] and keyfiles(env, HCON) == []
