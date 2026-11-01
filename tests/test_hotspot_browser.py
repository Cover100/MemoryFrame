"""#105 B2 in headless Chrome: on a password mismatch the overlay hides the QR but still shows the name.
#98: except in the W0 welcome screen, whose spec shows the Wi-Fi step only with the Wi-Fi QR."""
import json

import pytest

from test_frontend_browser import evaluate, net, page, server, text  # noqa: F401  (fixtures)


def _state(tmp_path, monkeypatch, ssid):
    p = tmp_path / "nm-browser.json"
    p.write_text(json.dumps({"mode": "product", "requested": "product", "hotspot_ssid": ssid}))
    monkeypatch.setenv("MEMORYFRAME_NETMODE_STATE", str(p))


@pytest.mark.no_media
def test_mismatch_shows_name_without_qr(page, server, mf_app, net, tmp_path, monkeypatch):
    monkeypatch.setenv("MEMORYFRAME_SSID", "ConfName")
    monkeypatch.setenv("MEMORYFRAME_WIFI_PASSWORD", "conf-pw-123456")
    _state(tmp_path, monkeypatch, "Hand Edited")
    page.pump(3)
    vis = "(id=>{const e=document.getElementById(id);return !!(e.offsetWidth||e.offsetHeight)})"
    # #98 W0 (unpaired, empty): the Wi-Fi step only with the Wi-Fi QR (16-WELCOME.md), so no join line and
    # no password wording; the 2-step layout instead.
    assert evaluate(page, f"{vis}('welcomeHead')")
    assert not evaluate(page, f"{vis}('wifiStep')") and not evaluate(page, f"{vis}('wifiJoin')")
    assert "password" not in evaluate(page, "document.getElementById('pairOverlay').innerText").lower()
    # #105 B2 behaviour outside welcome mode: on-demand "Pair new device" from the menu
    evaluate(page, "memoryFramePairing.openMenu()")
    page.pump(0.5)
    evaluate(page, "document.getElementById('menuPair').click()")
    page.pump(1.5)
    assert not evaluate(page, f"{vis}('welcomeHead')")
    assert evaluate(page, f"{vis}('wifiStep')")
    assert evaluate(page, f"{vis}('wifiJoin')") and not evaluate(page, f"{vis}('wifiQr')")
    assert evaluate(page, "document.getElementById('wifiQr').innerHTML") == ""
    assert " ".join(text(page, "wifiJoin").split()) == "Join Hand Edited with its password"
    assert evaluate(page, "getComputedStyle(document.getElementById('wifiJoinName')).display") == "inline"   # Wren: one line
    assert "conf-pw-123456" not in evaluate(page, "document.body.innerText")
    # password known again (name matches hotspot.conf): QR back, join line gone
    _state(tmp_path, monkeypatch, "ConfName")
    page.pump(3)
    assert evaluate(page, f"{vis}('wifiQr')") and not evaluate(page, f"{vis}('wifiJoin')")
    assert text(page, "pairSsid") == "ConfName"
