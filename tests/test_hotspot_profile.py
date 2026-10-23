"""#89: the shown hotspot name is the one the MemoryFrame-hotspot profile really broadcasts
(netmode.json hotspot_ssid, written by deploy/netmode.sh); the password is never read back or logged."""
import json
import logging

import pytest

from memoryframe import db as mfdb
from memoryframe import frameinfo, wifi
from memoryframe.db import set_setting

LOCAL = {"REMOTE_ADDR": "127.0.0.1"}
PHONE = {"REMOTE_ADDR": "10.42.0.23"}
SECRET = "sentinel-PW-do-not-log-9731"
OTHER_SECRET = "stale-db-PW-5521"


@pytest.fixture
def nm(tmp_path, monkeypatch):
    path = tmp_path / "netmode.json"
    monkeypatch.setenv("MEMORYFRAME_NETMODE_STATE", str(path))
    monkeypatch.setenv("MEMORYFRAME_SSID", "ConfName")
    monkeypatch.setenv("MEMORYFRAME_WIFI_PASSWORD", SECRET)
    monkeypatch.delenv("MEMORYFRAME_SHOW_WIFI_QR", raising=False)

    def write(**kw):
        d = {"mode": "product", "requested": "product", "boot_id": "b1"}
        d.update(kw)
        path.write_text(json.dumps(d))
    return write


def cw(mf_app):
    conn = mfdb.connect(mf_app.config["DB_PATH"])
    try:
        return wifi.current_wifi(conn)
    finally:
        conn.close()


def store(mf_app, ssid, password):
    conn = mfdb.connect(mf_app.config["DB_PATH"])
    set_setting(conn, "wifi_ssid", ssid)
    set_setting(conn, "wifi_password", password)
    conn.commit()
    conn.close()


# ---------- the profile SSID wins ----------

def test_live_ssid_matches_conf(mf_app, nm):
    nm(hotspot_ssid="ConfName")
    assert cw(mf_app) == ("ConfName", SECRET)


def test_nmcli_edit_shows_new_name_and_no_qr_with_unknown_password(mf_app, nm, client):
    nm(hotspot_ssid="ConfName")
    assert cw(mf_app)[0] == "ConfName"
    nm(hotspot_ssid="Edited By Hand")                      # netmode.sh rewrote the state after an edit
    assert cw(mf_app) == ("Edited By Hand", None)          # name follows the profile; password unknown
    st = client.get("/pairing/state", environ_base=LOCAL).get_json()
    assert st["ssid"] == "Edited By Hand" and st["wifi_qr_svg"] is None


def test_committed_change_matching_live_ssid(mf_app, nm):
    store(mf_app, "Phone Name", "phone-pw-123")
    nm(hotspot_ssid="Phone Name")
    assert cw(mf_app) == ("Phone Name", "phone-pw-123")


def test_stale_stored_value_is_not_used_with_a_different_profile(mf_app, nm):
    store(mf_app, "Old Phone Name", OTHER_SECRET)
    nm(hotspot_ssid="ConfName")                            # profile reset/edited back to the conf name
    assert cw(mf_app) == ("ConfName", SECRET)
    nm(hotspot_ssid="Third")
    assert cw(mf_app) == ("Third", None)                   # the stale password is never paired with it


def test_overlay_and_qr_show_the_profile_ssid(mf_app, nm, client):
    nm(hotspot_ssid="ConfName")
    st = client.get("/pairing/state", environ_base=LOCAL).get_json()
    assert st["ssid"] == "ConfName" and "<svg" in st["wifi_qr_svg"]
    nm(hotspot_ssid="Renamed")
    store(mf_app, "Renamed", "renamed-pw-77")
    st = client.get("/pairing/state", environ_base=LOCAL).get_json()
    assert st["ssid"] == "Renamed" and "<svg" in st["wifi_qr_svg"]
    assert client.get("/frame/status", environ_base=LOCAL).get_json()["ssid"] == "Renamed"


def test_phone_page_shows_profile_ssid_when_paired(mf_app, nm, paired):
    nm(hotspot_ssid="Phone Visible Name")
    body = paired.get("/admin/settings", environ_base=PHONE).get_data(as_text=True)
    assert "Phone Visible Name" in body and SECRET not in body


# ---------- fallback to the old order ----------

@pytest.mark.parametrize("raw", [None, "", "{", "[]", '{"mode": "product"}', '{"mode": "product", "hotspot_ssid": ""}',
                                 '{"mode": "product", "hotspot_ssid": 5}',
                                 '{"mode": "product", "hotspot_ssid": "' + "x" * 33 + '"}',
                                 '{"mode": "product", "hotspot_ssid": "a\\nb"}'])
def test_no_usable_profile_ssid_keeps_old_order(mf_app, nm, tmp_path, raw):
    if raw is not None:
        (tmp_path / "netmode.json").write_text(raw)
    assert frameinfo.netmode_state()["hotspot_ssid"] == ""
    assert cw(mf_app) == ("ConfName", SECRET)                     # MEMORYFRAME_SSID / hotspot.conf
    store(mf_app, "Phone Name", "phone-pw-123")
    assert cw(mf_app) == ("Phone Name", "phone-pw-123")           # a committed MF-18 change wins


def test_app_never_runs_nmcli(mf_app, nm, monkeypatch):
    import subprocess

    def boom(*a, **k):
        raise AssertionError("nmcli/subprocess must not be called by the app for the Wi-Fi name")
    monkeypatch.setattr(subprocess, "run", boom)
    monkeypatch.setattr(subprocess, "Popen", boom)
    nm(hotspot_ssid="X")
    assert cw(mf_app) == ("X", None)


# ---------- start-up reconcile: mismatch logged, profile wins, nothing deleted ----------

def test_startup_logs_mismatch_without_secrets(mf_app, nm, caplog):
    store(mf_app, "Old Phone Name", OTHER_SECRET)
    nm(hotspot_ssid="ConfName")
    with caplog.at_level(logging.DEBUG):
        wifi.startup_check(mf_app)
    msgs = " ".join(r.getMessage() for r in caplog.records)
    assert "profile wins" in msgs
    assert SECRET not in msgs and OTHER_SECRET not in msgs
    conn = mfdb.connect(mf_app.config["DB_PATH"])
    assert mfdb.get_setting(conn, "wifi_ssid") == "Old Phone Name"       # nothing deleted
    conn.close()


def test_startup_no_warning_when_in_sync(mf_app, nm, caplog):
    store(mf_app, "Same", "pw-pw-pw-1")
    nm(hotspot_ssid="Same")
    with caplog.at_level(logging.DEBUG):
        wifi.startup_check(mf_app)
    assert "profile wins" not in " ".join(r.getMessage() for r in caplog.records)


# ---------- no password in logs or responses ----------

def test_no_password_in_logs_or_api(mf_app, nm, client, paired, caplog):
    store(mf_app, "Old Phone Name", OTHER_SECRET)
    nm(hotspot_ssid="ConfName")
    with caplog.at_level(logging.DEBUG):
        wifi.startup_check(mf_app)
        client.get("/pairing/state", environ_base=LOCAL)
        client.get("/frame/status", environ_base=LOCAL)
        for path in ("/admin/", "/admin/settings", "/admin/wifi"):
            paired.get(path, environ_base=PHONE)
        nm(hotspot_ssid="Edited")
        client.get("/pairing/state", environ_base=LOCAL)
    logged = "\n".join(r.getMessage() + " " + str(r.args) for r in caplog.records)
    assert SECRET not in logged and OTHER_SECRET not in logged
    for url, env in (("/frame/status", LOCAL), ("/api/v1/status", PHONE)):
        r = (client if url.startswith("/frame") else paired).get(url, environ_base=env)
        assert SECRET not in r.get_data(as_text=True) and OTHER_SECRET not in r.get_data(as_text=True)


# ---------- privacy: an unpaired visitor never sees the SSID or password ----------

@pytest.mark.parametrize("path", ["/pair", "/admin/", "/admin/settings", "/admin/wifi", "/"])
def test_unpaired_visitor_sees_no_ssid_or_password(mf_app, nm, client, path):
    nm(hotspot_ssid="Secretish Home Name")
    r = client.get(path, environ_base=PHONE, follow_redirects=True)
    body = r.get_data(as_text=True)
    assert "Secretish Home Name" not in body and SECRET not in body and OTHER_SECRET not in body


def test_unpaired_visitor_cannot_read_frame_endpoints(mf_app, nm, client):
    nm(hotspot_ssid="Secretish Home Name")
    for path in ("/pairing/state", "/frame/status"):
        assert client.get(path, environ_base=PHONE).status_code == 403


def test_phone_wifi_panel_rules(mf_app, nm):
    """The offline-panel context: no SSID for an unpaired visitor, none in dev mode, the profile name otherwise."""
    from flask import g
    nm(hotspot_ssid="Panel Name")
    with mf_app.test_request_context("/admin/", environ_base=PHONE):
        ctx = {}
        mf_app.update_template_context(ctx)
        assert ctx["phone_wifi"]()["ssid"] == ""                       # unpaired
    with mf_app.test_request_context("/admin/", environ_base=PHONE):
        g.device_id = 1
        ctx = {}
        mf_app.update_template_context(ctx)
        assert ctx["phone_wifi"]()["ssid"] == "Panel Name"             # paired, product mode
    nm(mode="dev", requested="dev", hotspot_ssid="Panel Name")
    with mf_app.test_request_context("/admin/", environ_base=PHONE):
        g.device_id = 1
        ctx = {}
        mf_app.update_template_context(ctx)
        assert ctx["phone_wifi"]()["ssid"] == ""                       # dev mode: never the hotspot name


# ---------- Juniper's #105 review ----------

def test_one_bad_field_does_not_discard_the_state(mf_app, nm, tmp_path):
    (tmp_path / "netmode.json").write_bytes(b'{"mode": "product", "hotspot_error": true, "reset": true, '
                                            b'"active": "a\x1bb\xff", "hotspot_ssid": "x\x1by"}')
    s = frameinfo.netmode_state()
    assert s["mode"] == "product" and s["hotspot_error"] is True and s["reset"] is True
    assert s["hotspot_ssid"] == ""


def test_overlay_join_line_when_password_unknown(mf_app, nm, client):
    nm(hotspot_ssid="Hand Edited")
    st = client.get("/pairing/state", environ_base=LOCAL).get_json()
    assert st["wifi_qr_svg"] is None and st["wifi_join_name"] == "Hand Edited"
    nm(hotspot_ssid="ConfName")                                         # password known: QR, no join line
    st = client.get("/pairing/state", environ_base=LOCAL).get_json()
    assert "<svg" in st["wifi_qr_svg"] and st["wifi_join_name"] is None
    from conftest import pair_client
    nm(hotspot_ssid="Hand Edited")
    pair_client(client)
    assert client.get("/pairing/state", environ_base=LOCAL).get_json()["wifi_join_name"] is None


def test_join_line_markup_is_csp_safe():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    html = (root / "templates" / "index.html").read_text()
    line = next(l for l in html.splitlines() if 'id="wifiJoin"' in l)
    assert "style=" not in line and 'class="stepText"' in line and "with its password" in line
    js = (root / "static" / "pairing.js").read_text()
    assert '$("wifiJoinName").textContent' in js and "wifi_join_name" in js


def test_startup_logs_no_qr_names_only(mf_app, nm, caplog):
    nm(hotspot_ssid="Hand Edited")
    with caplog.at_level(logging.WARNING):
        wifi.startup_check(mf_app)
    msgs = " ".join(r.getMessage() for r in caplog.records)
    assert "no Wi-Fi QR" in msgs and SECRET not in msgs
