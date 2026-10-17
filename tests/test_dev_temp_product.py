"""On-device FAIL (main 53c438c, product mode): Developer Wi-Fi confirm did nothing.
Root cause: memoryframe.service had NoNewPrivileges=true + CapabilityBoundingSet, so the app's
`sudo -n memoryframe-netmode dev-temp` could never run. Also hardens profile choice and reporting."""
import re
import time
from pathlib import Path

import pytest

from test_frontend_browser import evaluate, net, page, server  # noqa: F401  (fixtures)
from test_netmode_script import HOT, env, profile, run, active, state  # noqa: F401

ROOT = Path(__file__).resolve().parents[1]
NETPLAN = "netplan-wlan0-Oversclan_5G"
LOCAL = {"REMOTE_ADDR": "127.0.0.1"}


# ---------- the service keeps its hardening; privileged actions are polkit-started units ----------
# (unit/polkit/install details: tests/test_privileged_units.py)

def test_app_starts_the_devtemp_unit_not_sudo():
    from memoryframe import display, privileged
    assert display.DEV_MODE_UNIT == privileged.UNITS["dev-temp"] == "memoryframe-devtemp.service"
    assert "sudo" not in (ROOT / "memoryframe" / "display.py").read_text().split("MF-51: Dev mode", 1)[1]


# ---------- suspect 3: helper picks the netplan client and never modifies it ----------

@pytest.fixture
def product_netplan(env):
    import shutil
    d = env["d"]
    for n in list((d / "p").iterdir()):
        if n.name != HOT:
            shutil.rmtree(n)
    profile(d, NETPLAN, ssid="Oversclan_5G", ac="no")      # netplan profile, never used yet (ts 0)
    profile(d, "MemoryFrame-AP-pending", mode="infrastructure", ssid="x")   # must never be picked
    (d / "p" / "MemoryFrame-AP-pending" / "ts").write_text("1900000000\n")
    profile(d, "Other-eth0-wifi", ssid="Elsewhere")
    (d / "p" / "Other-eth0-wifi" / "ifname").write_text("wlan1\n")
    (d / "p" / "Other-eth0-wifi" / "ts").write_text("1900000001\n")
    (env["boot"] / "memoryframe-mode.txt").write_text("product\n")
    assert run(env, "boot").returncode == 0 and active(env) == HOT
    return env


def no_netplan_modify(env):
    return not any(" modify " in l and NETPLAN in l for l in (env["d"] / "log").read_text().splitlines())


def test_dev_temp_in_product_mode_joins_netplan_profile(product_netplan):
    env = product_netplan
    r = run(env, "dev-temp")
    assert r.returncode == 0, r.stderr
    assert active(env) == NETPLAN and "ok: joined" in r.stdout
    assert state(env)["requested"] == "dev-temp"
    assert no_netplan_modify(env)
    assert not (env["boot"] / "memoryframe-mode.txt").read_text().strip() == "dev"


def test_dev_temp_client_fails_hotspot_back(product_netplan):
    env = product_netplan
    (env["d"] / "fail").write_text(NETPLAN + "\n")
    r = run(env, "dev-temp")
    assert r.returncode == 1 and "hotspot kept on" in r.stdout
    assert active(env) == HOT and no_netplan_modify(env)


def test_dev_temp_wait_is_about_30s():
    s = (ROOT / "deploy" / "netmode.sh").read_text()
    assert 'DEV_WAIT="${MEMORYFRAME_DEV_TEMP_WAIT:-30}"' in s


# ---------- suspect 4: server reports a JSON status ----------

def _token(client):
    html = client.get("/", environ_base=LOCAL).get_data(as_text=True)
    return html.split('name="mf-frame-token" content="', 1)[1].split('"', 1)[0]


@pytest.mark.parametrize("script,code,ok,reason", [
    ("echo 'ok: joined X'", 200, True, ""),
    ("echo 'Failed to start memoryframe-devtemp.service: Access denied' >&2; exit 4", 502, False, "not permitted"),
    ("exit 3", 502, False, "busy"),
    ("echo 'error: home Wi-Fi did not connect; hotspot kept on'; exit 1", 502, False, "did not connect"),
])
def test_endpoint_json_status(client, mf_app, script, code, ok, reason):
    mf_app.config["DEV_MODE_CMD"] = ["sh", "-c", script]
    r = client.post("/frame/dev-mode", headers={"X-Frame-Token": _token(client)}, environ_base=LOCAL)
    j = r.get_json()
    assert r.status_code == code and j["ok"] is ok and reason in j["reason"]


# ---------- suspect 5: the kiosk shows the result ----------

def _confirm(page):
    page.pump(2.5)
    evaluate(page, "document.getElementById('devModeOverlay').classList.add('visible');"
                   "document.getElementById('devModeAsk').hidden=false;")
    evaluate(page, "document.getElementById('devModeConfirm').click()")


def _box(page):
    return evaluate(page, "document.getElementById('devModeBox').innerText")


def test_kiosk_shows_joining_then_closes_on_success(page, server, mf_app, net):
    mf_app.config["DEV_MODE_CMD"] = ["sh", "-c", "sleep 1; echo ok"]
    _confirm(page)
    page.pump(0.3)
    assert "Joining home Wi-Fi" in _box(page)
    page.pump(1.5)
    assert not evaluate(page, "document.getElementById('devModeOverlay').classList.contains('visible')")


def test_kiosk_shows_failure_with_reason(page, server, mf_app, net):
    mf_app.config["DEV_MODE_CMD"] = ["sh", "-c", "echo 'error: home Wi-Fi did not connect'; exit 1"]
    _confirm(page)
    page.pump(1.0)
    box = _box(page)
    assert "Couldn't join home Wi-Fi \u2014 hotspot kept on" in box and "home Wi-Fi did not connect" in box
