"""Dev-temp pairing (Sage, MemoryFrame-design/devtemp-pair/index.md; replaces MF-55 G3's "no pairing"):
in temporary Developer Wi-Fi the pairing round issues a QR and link on the frame's Wi-Fi IP, but only an IP the
Host check accepts (frameinfo.own_ips). No usable IP: the "No Wi-Fi address yet" card. An explicit site
override still wins; the product (hotspot) and persistent-dev paths are unchanged.

Also Reed's audit lines (sign-out-all, per-device remove, #153 re-pair reuse): ids, counts and source, never a
cookie, token, hash or code. Cookie values are never printed: checks are booleans."""
import hashlib
import json
import logging
import re
import time

import pytest

from memoryframe import auth, frameinfo, pairing
from memoryframe import db as mfdb
from test_frontend_browser import BROWSER, evaluate, page, server, text, visible  # noqa: F401  (fixtures)

LOCAL = {"REMOTE_ADDR": "127.0.0.1"}
PHONE = {"REMOTE_ADDR": "192.168.50.77"}
IP = "192.168.50.124"
SAME_WIFI = "Phone on the same Wi-Fi as the frame"
NO_IP = "No Wi-Fi address yet. Restart the frame to pair a phone."


@pytest.fixture
def devtemp(tmp_path, monkeypatch):
    p = tmp_path / "netmode.json"
    monkeypatch.setenv("MEMORYFRAME_NETMODE_STATE", str(p))
    monkeypatch.delenv("MEMORYFRAME_SITE_URL", raising=False)
    monkeypatch.delenv("MEMORYFRAME_FORCE_NETMODE", raising=False)
    monkeypatch.setenv("MEMORYFRAME_PORTS", "80,5000")
    monkeypatch.setenv("MEMORYFRAME_SSID", "Frame-WiFi")
    monkeypatch.setenv("MEMORYFRAME_WIFI_PASSWORD", "correct horse;battery")
    state = {"mode": "lan", "lan_ip": IP, "ips": [IP]}
    own = [IP]
    monkeypatch.setattr(frameinfo, "network", lambda: dict(state))
    monkeypatch.setattr(frameinfo, "own_ips", lambda max_age=None: list(own))

    class Ctl:
        net, ips = state, own

        @staticmethod
        def write(requested="dev-temp", mode="dev"):
            p.write_text(json.dumps({"mode": mode, "requested": requested, "dev_fallback": False,
                                     "hotspot_error": False, "boot_id": "b1"}))
    Ctl.write()
    return Ctl


def started(client):
    client.post("/pairing/start", environ_base=LOCAL)
    return client.get("/pairing/state", environ_base=LOCAL).get_json()


def blank(st):
    assert st["dev_temp"] is True and st["round"] is None and st["wifi_qr_svg"] is None
    assert st["url"] is None and st["url_fallback"] is None and st["site_url"] is None
    assert IP not in json.dumps({k: v for k, v in st.items() if k != "netmode"})


# ---------- state ----------

def test_devtemp_state_url_uses_lan_ip(client, devtemp):
    st = started(client)
    assert "dev_temp" not in st and st["round"] and st["round"]["code"]
    assert st["url"] == f"http://{IP}/pair" and st["site_url"] == f"http://{IP}/"
    assert st["url_fallback"] is None and st["wifi_qr_svg"] is None and st["wifi_join_name"] is None
    assert st["netmode"]["temp"] is True


def test_devtemp_qr_encodes_lan_link(client, devtemp, mf_app):
    st = started(client)
    token = mf_app.extensions["mf_pairing"].token
    assert st["round"]["qr_svg"] == pairing.qr_svg(f"http://{IP}/pair?t={token}")
    assert len(mf_app.extensions["mf_pairing"].code) == 6 and len(token) == 32   # limits unchanged


def test_devtemp_lan_host_and_origin_accepted(client, devtemp, mf_app):
    started(client)
    base = f"http://{IP}"
    r = client.get("/pair", base_url=base, environ_base=PHONE)
    assert r.status_code == 200
    form_token = re.search(r'name="csrf_token" value="([^"]+)"', r.get_data(as_text=True)).group(1)
    code = mf_app.extensions["mf_pairing"].code
    evil = client.post("/pair", base_url=base, environ_base=PHONE, csrf=False,
                       data={"code": code, "csrf_token": form_token}, headers={"Origin": "http://evil.example"})
    assert evil.status_code == 403 and client.get_cookie(auth.COOKIE) is None
    r = client.post("/pair", base_url=base, environ_base=PHONE, csrf=False,
                    data={"code": code, "csrf_token": form_token}, headers={"Origin": base})
    assert r.status_code == 303 and r.headers["Location"].endswith("/admin/?paired=1")
    assert client.get_cookie(auth.COOKIE, domain=IP) is not None
    assert client.get("/admin/", base_url=base, environ_base=PHONE).status_code == 200


def test_devtemp_ip_not_an_own_address_falls_back(client, devtemp):
    devtemp.ips.clear()
    blank(started(client))


def test_devtemp_no_lan_ip_falls_back(client, devtemp):
    devtemp.net.update(mode="unknown", lan_ip=None, ips=[])
    blank(started(client))
    devtemp.net.update(mode="lan", lan_ip="", ips=[])
    blank(started(client))


def test_devtemp_hotspot_mode_unchanged(client, devtemp, monkeypatch):
    devtemp.write(requested="product", mode="product")        # the product path on the hotspot
    devtemp.net.update(mode="hotspot", lan_ip="10.42.0.1", ips=["10.42.0.1"])
    monkeypatch.setenv("MEMORYFRAME_PORTS", "5000")
    devtemp.ips[:] = ["10.42.0.1"]
    st = started(client)
    assert "dev_temp" not in st and st["url"] == "http://10.42.0.1:5000/pair" and st["round"]
    assert st["wifi_qr_svg"]                                   # first-run Wi-Fi QR as before


def test_devtemp_on_hotspot_fallback_shows_card(client, devtemp):
    """Spec (4): dev-temp whose Developer Wi-Fi failed (hotspot up, not a client network): today's card."""
    devtemp.net.update(mode="hotspot", lan_ip="10.42.0.1", ips=["10.42.0.1"])
    devtemp.ips[:] = ["10.42.0.1"]
    st = started(client)
    assert st["dev_temp"] is True and st["round"] is None and st["url"] is None


def test_devtemp_persistent_dev_unchanged(client, devtemp):
    devtemp.write(requested="dev")
    st = started(client)
    assert "dev_temp" not in st and st["url"] == "http://memoryframe.local/pair" and st["round"]


def test_devtemp_site_override_wins(client, devtemp, monkeypatch):
    monkeypatch.setenv("MEMORYFRAME_SITE_URL", "http://frame.home")
    monkeypatch.setenv("MEMORYFRAME_EXTRA_HOSTS", "frame.home")
    st = started(client)
    assert "dev_temp" not in st and st["url"] == "http://frame.home/pair" and st["round"]
    devtemp.ips.clear()                      # the override wins with or without a usable IP
    st = started(client)
    assert "dev_temp" not in st and st["url"] == "http://frame.home/pair"


# ---------- frame screen (headless Chrome, 800x480) ----------

def _wait_text(page, el_id, want, secs=15):
    end = time.monotonic() + secs
    while (text(page, el_id) or "") != want and time.monotonic() < end:
        page.pump(0.25)
    return text(page, el_id)


def _fits(page):
    r = json.loads(evaluate(page, "JSON.stringify((()=>{const o=[];for(const id of ['pairBox','pairUrl','pairUrlAlt',"
                                  "'pairQr','pairCode']){const e=document.getElementById(id);const b=e.getBoundingClientRect();"
                                  "o.push([id,b.left,b.top,b.right,b.bottom,e.scrollWidth<=e.clientWidth+1]);}"
                                  "return [innerWidth,innerHeight,o];})())"))
    assert r[:2] == [800, 480], r
    for id_, l, t, rt, b, no_overflow in r[2]:
        assert l >= -0.5 and t >= -0.5 and rt <= 800.5 and b <= 480.5, (id_, l, t, rt, b)
        assert no_overflow, id_
    # the hint stays inside its card, and the card ends above the W0 footer ("Then tap Upload")
    g = json.loads(evaluate(page, "JSON.stringify((()=>{const a=document.getElementById('pairUrlAlt').getBoundingClientRect();"
                                  "const c=document.getElementById('pairUrlAlt').closest('.step').getBoundingClientRect();"
                                  "const f=document.getElementById('welcomeFoot');const fr=f.getBoundingClientRect();"
                                  "return [a.bottom,c.bottom,f.hidden?null:fr.top];})())"))
    assert g[0] <= g[1] + 0.5, g
    assert g[2] is None or g[1] <= g[2] + 0.5, g


@pytest.mark.skipif(BROWSER is None, reason="no headless Chrome")
@pytest.mark.no_media
def test_devtemp_no_lan_ip_card_on_frame(page, server, devtemp):
    devtemp.net.update(mode="unknown", lan_ip=None, ips=[])
    page.pump(3.0)
    assert visible(page, "pairOverlay") and visible(page, "pairDevTemp")
    assert NO_IP in text(page, "pairOverlay") and IP not in text(page, "pairOverlay")
    assert evaluate(page, "document.querySelectorAll('#pairOverlay svg:not(.mf-tick)').length") == 0


@pytest.mark.skipif(BROWSER is None, reason="no headless Chrome")
def test_pairing_screen_in_dev_temp_shows_qr_and_hint(page, server, devtemp):
    from test_qr_fit import check
    page.pump(2.0)
    evaluate(page, "memoryFramePairing.openMenu()")
    page.pump(0.5)
    evaluate(page, "document.getElementById('menuPair').click()")
    end = time.monotonic() + 15
    while not (visible(page, "pairOverlay") and evaluate(page, "!!document.querySelector('#pairQr svg')")) \
            and time.monotonic() < end:
        page.pump(0.25)
    assert visible(page, "pairOverlay") and not visible(page, "pairDevTemp")
    assert text(page, "pairUrl") == f"http://{IP}/pair"
    assert text(page, "pairUrlAlt") == SAME_WIFI
    assert evaluate(page, "!!document.querySelector('#pairQr svg')")
    d = check(page, "devtemp-pair-800x480.png", ["pairQr"])
    assert d["pairQr"] == "" or d["pairQr"].startswith(f"http://{IP}/pair?t="), d   # decode is secondary (CI)
    _fits(page)


@pytest.mark.skipif(BROWSER is None, reason="no headless Chrome")
@pytest.mark.no_media
def test_devtemp_welcome_shows_ip_site_and_hint(page, server, devtemp):
    """#98 W0 (first run) shows the bare site address; in dev-temp it is the IP, with the same hint."""
    assert _wait_text(page, "pairUrl", f"http://{IP}") == f"http://{IP}"
    assert text(page, "pairUrlAlt") == SAME_WIFI and not visible(page, "pairDevTemp")
    assert evaluate(page, "!!document.querySelector('#pairQr svg')")
    _fits(page)


# ---------- Reed: audit log lines (no secrets) ----------

def _q(app, sql, args=()):
    conn = mfdb.connect(app.config["DB_PATH"])
    try:
        return [tuple(r) for r in conn.execute(sql, args).fetchall()]
    finally:
        conn.close()


def _pair(c, app, env):
    st = c.post("/pairing/start", environ_base=LOCAL).get_json()
    code, token = st["round"]["code"], app.extensions["mf_pairing"].token
    r = c.post("/pair", data={"code": code}, environ_base=env)
    assert r.status_code == 303
    return [code, token]


def test_audit_log_lines_have_id_count_source_and_no_secret(mf_app, caplog):
    caplog.set_level(logging.INFO, logger="memoryframe.auth")
    a, b = mf_app.test_client(), mf_app.test_client()
    pa, pb = {"REMOTE_ADDR": "10.42.0.23"}, {"REMOTE_ADDR": "10.42.0.24"}
    secrets_seen = _pair(a, mf_app, pa) + _pair(b, mf_app, pb)
    for c in (a, b):
        v = c.get_cookie(auth.COOKIE).value
        secrets_seen += [v, hashlib.sha256(v.encode()).hexdigest()]
    dev_a = _q(mf_app, "SELECT device_id FROM sessions WHERE revoked = 0 ORDER BY id")[0][0]
    dev_b = _q(mf_app, "SELECT device_id FROM sessions WHERE revoked = 0 ORDER BY id")[1][0]

    caplog.clear()
    secrets_seen += _pair(a, mf_app, pa)                   # (c) #153 re-pair: a's row reused
    v = a.get_cookie(auth.COOKIE).value
    secrets_seen += [v, hashlib.sha256(v.encode()).hexdigest()]
    assert f"re-pair (re-pair): device {dev_a} reused, 1 old session(s) revoked" in caplog.text

    caplog.clear()                                         # (b) Settings remove of the other phone
    assert a.post(f"/admin/devices/{dev_b}/remove", environ_base=pa).status_code == 303
    assert f"remove device (Settings remove): device {dev_b}, 1 session(s) revoked" in caplog.text

    caplog.clear()                                         # (a) sign-out-all from the phone site
    assert a.post("/admin/sign-out-all", environ_base=pa).status_code == 303
    assert f"sign out all (phone site): 1 session(s) on 1 device(s) revoked; devices {dev_a}" in caplog.text

    secrets_seen += _pair(a, mf_app, pa) + _pair(b, mf_app, pb)   # fresh sessions (revoked cookies: new rows)
    rows = sorted(r[0] for r in _q(mf_app, "SELECT DISTINCT device_id FROM sessions WHERE revoked = 0"))
    caplog.clear()                                         # (a) sign-out-all from the frame menu
    assert a.post("/pairing/sign-out-all", environ_base=LOCAL).get_json()["revoked"] == 2
    assert (f"sign out all (frame menu): 2 session(s) on 2 device(s) revoked; devices {rows[0]}, {rows[1]}"
            in caplog.text)

    every = " ".join(r.getMessage() for r in caplog.records) + caplog.text
    assert len(secrets_seen) == 16 and all(len(s) >= 6 for s in secrets_seen)
    assert not any(s in every for s in secrets_seen), "a secret reached the log"


def test_audit_log_remove_of_unknown_device(client, mf_app, caplog):
    caplog.set_level(logging.INFO, logger="memoryframe.auth")
    conn = mfdb.connect(mf_app.config["DB_PATH"])
    try:
        assert auth.revoke_device(conn, 999) is None
    finally:
        conn.close()
    assert "remove device (Settings remove): no device 999; nothing revoked" in caplog.text


def test_app_main_lets_info_audit_lines_through():
    """waitress.serve() configures logging at WARNING; app.main raises the memoryframe logger to INFO."""
    from pathlib import Path
    src = (Path(__file__).resolve().parent.parent / "app.py").read_text()
    assert 'logging.getLogger("memoryframe").setLevel(logging.INFO)' in src
