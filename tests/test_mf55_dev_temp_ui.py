"""MF-55 G2 (dev-until-reboot label) and G3 (temporary Developer Wi-Fi without a Wi-Fi address: no pairing).

Since the devtemp-pair fix dev-temp pairs on the frame's Wi-Fi IP (tests/test_devtemp_pair.py); the G3 card
remains only for "no usable address yet", which these tests force by patching frameinfo.network."""
import json
import re

import pytest

from memoryframe import frameinfo
from test_frontend_browser import evaluate, net, page, server, text, visible  # noqa: F401  (fixtures)

LOCAL = {"REMOTE_ADDR": "127.0.0.1"}
IP = "192.168.50.124"


@pytest.fixture
def devtemp(tmp_path, monkeypatch, net):
    p = tmp_path / "netmode.json"
    monkeypatch.setenv("MEMORYFRAME_NETMODE_STATE", str(p))
    net.update(mode="lan", lan_ip=IP, ips=[IP])

    def write(requested="dev-temp", mode="dev"):
        p.write_text(json.dumps({"mode": mode, "requested": requested, "dev_fallback": False,
                                 "hotspot_error": False, "boot_id": "b1"}))
    write()
    return write


def no_address(s):
    return IP not in s and ".local" not in s and ".lan" not in s and "10.42.0.1" not in s


def _no_ip(monkeypatch):
    # net.update(mode="unknown") would write an invalid MEMORYFRAME_FORCE_NETMODE; patch network directly.
    monkeypatch.setattr(frameinfo, "network", lambda: {"mode": "unknown", "lan_ip": None, "ips": []})


def test_state_in_dev_temp_without_address(client, devtemp, monkeypatch):
    _no_ip(monkeypatch)
    client.post("/pairing/start", environ_base=LOCAL)
    st = client.get("/pairing/state", environ_base=LOCAL).get_json()
    assert st["dev_temp"] is True and st["round"] is None and st["wifi_qr_svg"] is None
    assert st["url"] is None and st["url_fallback"] is None
    blob = json.dumps({k: v for k, v in st.items() if k != "netmode"})
    assert no_address(blob)


def test_persistent_dev_still_pairs(client, devtemp):
    devtemp(requested="dev")
    st = client.get("/pairing/state", environ_base=LOCAL).get_json()
    assert "dev_temp" not in st and st["url"]


@pytest.mark.no_media
def test_pairing_screen_in_dev_temp(page, server, devtemp, monkeypatch):
    _no_ip(monkeypatch)
    page.pump(3.0)
    assert visible(page, "pairOverlay")
    assert "No Wi-Fi address yet. Restart the frame to pair a phone." in text(page, "pairOverlay")
    assert no_address(text(page, "pairOverlay"))
    assert evaluate(page, "document.querySelectorAll('#pairOverlay svg:not(.mf-tick)').length") == 0
    assert evaluate(page, "getComputedStyle(document.getElementById('pairBox')).display") == "none"


def test_label_g2_style(page, server, devtemp):
    page.pump(3.0)
    assert text(page, "devBanner") == f"Developer Wi-Fi \u00b7 {IP} \u00b7 restart to exit"
    st = evaluate(page, "(()=>{const b=document.getElementById('devBanner');const s=getComputedStyle(b);"
                        "const r=b.getBoundingClientRect();return [s.fontSize,s.pointerEvents,s.position,"
                        "r.left,innerHeight-r.bottom,parseFloat(s.color.split(',')[3]||'1')];})()")
    assert st[0] == "18px" and st[1] == "none" and st[2] == "fixed"
    assert st[3] < 40 and st[4] > 400                        # #70 mock 12: top-left pill


def test_label_hidden_in_product(page, server, devtemp):
    devtemp(requested="product", mode="product")
    page.pump(3.0)
    assert not evaluate(page, "!document.getElementById('devBanner').hidden")
