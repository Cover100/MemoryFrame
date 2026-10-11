"""MF-36: the frame side of product/dev mode (state file -> banner, toast, error screen)."""
import json
import re
from pathlib import Path

import pytest

from memoryframe import frameinfo
from test_frontend_browser import evaluate, net, page, server, text  # noqa: F401  (fixtures)

ROOT = Path(__file__).resolve().parents[1]
LOCAL = {"REMOTE_ADDR": "127.0.0.1"}


@pytest.fixture
def nmstate(tmp_path, monkeypatch):
    path = tmp_path / "netmode.json"
    monkeypatch.setenv("MEMORYFRAME_NETMODE_STATE", str(path))

    def write(**kw):
        d = {"mode": "product", "requested": "product", "dev_fallback": False, "hotspot_error": False,
             "active": "MemoryFrame-hotspot", "boot_id": "b1"}
        d.update(kw)
        path.write_text(json.dumps(d))
    return write


def test_missing_state_is_product(monkeypatch, tmp_path):
    monkeypatch.setenv("MEMORYFRAME_NETMODE_STATE", str(tmp_path / "nope.json"))
    assert frameinfo.netmode_state() == {"mode": "product", "dev_fallback": False, "hotspot_error": False,
                                         "boot_id": ""}


@pytest.mark.parametrize("raw", ["", "{", "[]", '{"mode": "evil", "hotspot_error": "yes"}'])
def test_garbage_state_is_product(monkeypatch, tmp_path, raw):
    p = tmp_path / "s.json"
    p.write_text(raw)
    monkeypatch.setenv("MEMORYFRAME_NETMODE_STATE", str(p))
    s = frameinfo.netmode_state()
    assert s["mode"] == "product" and s["hotspot_error"] is False


def test_pairing_state_carries_netmode(client, nmstate, net):
    nmstate(mode="dev", requested="dev", active="Home")
    net.update(mode="lan", lan_ip="192.168.50.124", ips=["192.168.50.124"])
    nm = client.get("/pairing/state", environ_base=LOCAL).get_json()["netmode"]
    assert nm == {"dev": True, "lan_ip": "192.168.50.124", "dev_fallback": False, "hotspot_error": False,
                  "boot_id": "b1"}


def test_product_has_no_lan_ip(client, nmstate, net):
    nmstate()
    nm = client.get("/pairing/state", environ_base=LOCAL).get_json()["netmode"]
    assert nm["dev"] is False and nm["lan_ip"] is None


def test_no_network_mode_controls_in_user_ui():
    """6: no dev-mode / network-mode item in the frame menu or on any phone page."""
    for f in (ROOT / "templates").glob("*.html"):
        html = f.read_text()
        # the MF-36 status displays themselves (banner/toast/error copy) are not controls
        html = re.sub(r"<!-- MF-36 \(frame only\).*?(?=<script)", "", html, flags=re.S)
        low = html.lower()
        for bad in ("developer mode", "dev mode", "netmode", "network mode", "product mode", "memoryframe-mode"):
            assert bad not in low, (f.name, bad)
    assert "netmode" not in (ROOT / "static" / "script.js").read_text().lower()


def test_ui_copy_never_mentions_password():
    html = (ROOT / "templates" / "index.html").read_text()
    block = html[html.index('id="devBanner"'):html.index("pairing.js")]
    assert "password" not in block.lower() and "sudo" not in block and "netmode.sh" not in block


# ---------- browser (800x480) ----------

def visible(page, el_id):
    return evaluate(page, f"(()=>{{const e=document.getElementById('{el_id}');"
                          f"if(e.hidden) return false; const r=e.getBoundingClientRect();"
                          f"return r.width>0 && r.height>0;}})()")


def rect(page, sel):
    return evaluate(page, f"(()=>{{const r=document.querySelector('{sel}').getBoundingClientRect();"
                          f"return [r.left,r.top,r.right,r.bottom];}})()")


def test_product_boot_shows_nothing(page, server, mf_app, net, nmstate):
    nmstate()
    page.pump(2.5)
    assert not visible(page, "devBanner") and not visible(page, "devToast") and not visible(page, "hotspotError")


def test_dev_banner(page, server, mf_app, net, nmstate):
    nmstate(mode="dev", requested="dev")
    net.update(mode="lan", lan_ip="192.168.50.124", ips=["192.168.50.124"])
    page.pump(2.5)
    assert visible(page, "devBanner")
    assert text(page, "devBanner") == "Developer mode \u00b7 192.168.50.124"
    l, t, r, b = rect(page, "#devBanner")
    assert l == pytest.approx(12, abs=1) and b == pytest.approx(480 - 12, abs=1)
    assert evaluate(page, "getComputedStyle(document.getElementById('devBanner')).pointerEvents") == "none"
    net.update(mode="lan", lan_ip=None, ips=[])
    page.pump(2.5)
    assert text(page, "devBanner") == "Developer mode \u00b7 no network"
    nmstate()                                              # back to product
    page.pump(2.5)
    assert not visible(page, "devBanner")


def test_dev_fallback_toast_once_per_boot_and_tap_closes(page, server, mf_app, net, nmstate):
    nmstate(requested="dev", dev_fallback=True, boot_id="boot-xyz")
    page.pump(2.5)
    assert visible(page, "devToast")
    assert "Developer Wi-Fi not found, hotspot started" in text(page, "devToast")
    l, t, r, b = rect(page, "#devToast")
    assert (round(r - l), round(b - t)) == (560, 72) and l >= 0 and r <= 800
    assert evaluate(page, "memoryFramePairing.toastMs") == 30000
    evaluate(page, "document.getElementById('devToast').click()")
    page.pump(1.5)
    assert not visible(page, "devToast")
    evaluate(page, "location.reload()")                     # same boot: not shown again
    page.pump(3)
    assert not visible(page, "devToast")


def test_hotspot_error_screen_fits_and_close_snoozes(page, server, mf_app, net, nmstate):
    nmstate(hotspot_error=True)
    page.pump(2.5)
    assert visible(page, "hotspotError")
    card = rect(page, ".hotspot-error-card")
    assert (round(card[2] - card[0]), round(card[3] - card[1])) == (680, 400)
    assert card[0] >= 0 and card[1] >= 0 and card[2] <= 800 and card[3] <= 480
    # nothing inside the card is clipped
    assert evaluate(page, "(()=>{const c=document.querySelector('.hotspot-error-card');"
                          "return c.scrollHeight<=c.clientHeight && c.scrollWidth<=c.clientWidth;})()")
    btn = rect(page, "#hotspotErrorClose")
    assert round(btn[3] - btn[1]) == 64 and btn[2] <= card[2] and btn[3] <= card[3]
    foot = rect(page, ".hotspot-error-foot")
    assert foot[3] <= btn[1] or foot[2] <= btn[0]             # footer doesn't run under the button
    body = text(page, "hotspotError")
    for line in ("Memory Frame Wi-Fi didn't start", 'open the drive called "bootfs"',
                 "Create an empty file named memoryframe-reset (no extension).",
                 "make sure it only says: product", "Still not working? Restart the frame once."):
        assert line in body
    assert evaluate(page, "memoryFramePairing.snoozeMs") == 600000
    evaluate(page, "document.getElementById('hotspotErrorClose').click()")
    page.pump(2.5)
    assert not visible(page, "hotspotError")                  # snoozed (10 min), slideshow still there
    assert evaluate(page, "!!document.querySelector('#slideshow, .slide, img, video')")
