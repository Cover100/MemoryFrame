"""MF-18: change the hotspot name/password from the phone (fake helper, short timers)."""
import html as htmlmod
import logging
import time

import pytest

from memoryframe import db as mfdb, wifi

PHONE = {"REMOTE_ADDR": "10.42.0.23"}
LAN_PHONE = {"REMOTE_ADDR": "192.168.50.9"}
LOCAL = {"REMOTE_ADDR": "127.0.0.1"}
OLD_PW, NEW_PW = "old-password-1", "Brand new pw!"

FAKE_HELPER = r'''#!/bin/bash
D="$(dirname "$0")"
in=""; if [ "$1" = wifi-apply ]; then in="$(cat)"; fi
echo "$1 stdin_lines=$(printf '%s' "$in" | grep -c '')" >> "$D/calls"
[ -f "$D/rc-$1" ] && exit "$(cat "$D/rc-$1")"
exit 0
'''


@pytest.fixture
def helper(tmp_path, mf_app, monkeypatch):
    d = tmp_path / "helper"
    d.mkdir()
    h = d / "fake-netmode"
    h.write_text(FAKE_HELPER)
    h.chmod(0o755)
    mf_app.config["NET_HELPER"] = [str(h)]
    mf_app.config["NETMODE_LOCK"] = str(tmp_path / "netmode.lock")
    monkeypatch.setenv("MEMORYFRAME_SSID", "MemoryFrame-AP")
    monkeypatch.setenv("MEMORYFRAME_WIFI_PASSWORD", OLD_PW)
    monkeypatch.setenv("MEMORYFRAME_NETMODE_STATE", str(tmp_path / "netmode.json"))
    (tmp_path / "netmode.json").write_text('{"mode": "product"}')   # the frame booted in product mode
    started = []
    mf_app.config["WIFI_START"] = lambda: started.append(1)    # apply is driven by the tests

    class H:
        dir = d
        start = started

        def calls(self):
            p = d / "calls"
            return p.read_text().splitlines() if p.exists() else []

        def fail(self, cmd, rc=4):
            (d / f"rc-{cmd}").write_text(str(rc))
    return H()


def row(app):
    conn = mfdb.connect(app.config["DB_PATH"])
    try:
        return conn.execute("SELECT * FROM wifi_change WHERE id = 1").fetchone()
    finally:
        conn.close()


def submit(c, ssid="Frame Home", password=NEW_PW, env=PHONE):
    return c.post("/admin/wifi", data={"ssid": ssid, "password": password}, environ_base=env)


def start_pending(app, c):
    r = submit(c)
    assert r.status_code == 200, r.data
    r.get_data()
    r.close()                                                # runs call_on_close (WIFI_START)
    rw = row(app)
    wifi._apply_after_response(app, rw["new_ssid"], rw["new_password"], rw["deadline"])
    return r


# ---------- page ----------

def test_admin_home_links_frame_wifi(paired):
    html = htmlmod.unescape(paired.get("/admin/", environ_base=PHONE).get_data(as_text=True))
    assert '<a class="big" href="/admin/wifi">Frame Wi-Fi</a>' in html
    assert html.index("/admin/library") < html.index("/admin/wifi")


def test_page_prefills_name_not_password(paired, helper):
    html = htmlmod.unescape(paired.get("/admin/wifi/", environ_base=PHONE).get_data(as_text=True))
    assert "Current Wi-Fi: <span id=\"currentName\">MemoryFrame-AP</span>" in html
    assert 'id="ssid" name="ssid" value="MemoryFrame-AP"' in html
    assert 'id="password" name="password" type="password" value=""' in html
    assert OLD_PW not in html


def test_requires_pairing_and_csrf(client, helper, mf_app):
    assert client.get("/admin/wifi/", environ_base=PHONE).status_code == 302
    from conftest import pair_client
    pair_client(client)
    r = client.post("/admin/wifi/", data={"ssid": "x", "password": NEW_PW}, environ_base=PHONE, csrf=False)
    assert r.status_code == 403 and row(mf_app) is None
    assert client.get("/admin/wifi/keep", environ_base=PHONE).status_code == 302   # nothing pending


@pytest.mark.parametrize("ssid, password, msg", [
    ("x" * 33, NEW_PW, wifi.MSG_NAME),
    ("é" * 17, NEW_PW, wifi.MSG_NAME),                      # 34 bytes
    ("", NEW_PW, wifi.MSG_NAME),
    ("bad\x07name", NEW_PW, wifi.MSG_NAME),
    ("ok", "a" * 7, wifi.MSG_PASSWORD),
    ("ok", "a" * 64, wifi.MSG_PASSWORD),
    ("ok", "pässwort12", wifi.MSG_PASSWORD),
    ("ok", "emoji😀pass", wifi.MSG_PASSWORD),
    ("MemoryFrame-AP", OLD_PW, wifi.MSG_SAME),
])
def test_invalid_input_400_nothing_applied(paired, helper, mf_app, ssid, password, msg):
    r = submit(paired, ssid, password)
    assert r.status_code == 400 and msg in htmlmod.unescape(r.get_data(as_text=True))
    assert row(mf_app) is None and helper.start == [] and helper.calls() == []
    assert password not in htmlmod.unescape(r.get_data(as_text=True)) or password in ("", )


def test_valid_32_byte_name_and_63_char_password(paired, helper, mf_app):
    assert submit(paired, "é" * 16, "~" * 63).status_code == 200


# ---------- apply ----------

def test_change_goes_pending_then_helper_with_stdin(paired, helper, mf_app):
    r = start_pending(mf_app, paired)
    html = htmlmod.unescape(r.get_data(as_text=True))
    assert "Rejoin the new Wi-Fi" in html and 'join "Frame Home" with your new password' in html
    assert "If you don't confirm within 5 minutes, the old Wi-Fi comes back." in html
    assert NEW_PW not in html
    assert helper.start == [1]                               # started after the response
    assert helper.calls() == ["wifi-apply stdin_lines=2"]   # password on stdin, not argv
    rw = row(mf_app)
    assert rw["state"] == "pending" and rw["old_profile"] == "MemoryFrame-hotspot"
    assert rw["new_profile"] == "MemoryFrame-AP-pending" and rw["deadline"] > time.time() + 250


def test_frame_shows_new_qr_and_countdown(paired, helper, mf_app, client):
    start_pending(mf_app, paired)
    st = client.get("/pairing/state", environ_base=LOCAL).get_json()
    wc = st["wifi_change"]
    assert wc["state"] == "pending" and wc["new_ssid"] == "Frame Home" and 290 <= wc["remaining"] <= 300
    assert "<svg" in wc["qr_svg"]
    assert NEW_PW not in str(st).replace(wc["qr_svg"], "")    # only inside the QR


def test_second_change_409(paired, helper, mf_app):
    start_pending(mf_app, paired)
    r = submit(paired, "Another", NEW_PW)
    assert r.status_code == 409 and wifi.MSG_BUSY in htmlmod.unescape(r.get_data(as_text=True))
    assert paired.get("/admin/wifi/", environ_base=PHONE).headers["Location"].endswith("/admin/wifi/keep")
    assert paired.get("/admin/", environ_base=PHONE).headers["Location"].endswith("/admin/wifi/keep")


def test_dev_mode_409_and_form_replaced(paired, helper, mf_app, tmp_path):
    (tmp_path / "netmode.json").write_text('{"mode": "dev"}')
    html = htmlmod.unescape(paired.get("/admin/wifi/", environ_base=PHONE).get_data(as_text=True))
    assert "Wi-Fi can't be changed right now." in html and 'id="wifiForm"' not in html
    assert submit(paired).status_code == 409 and row(mf_app) is None


def test_mode_switch_in_progress_409(paired, helper, mf_app, tmp_path):
    import fcntl
    lock = open(tmp_path / "netmode.lock", "w")
    fcntl.flock(lock, fcntl.LOCK_EX)
    try:
        assert submit(paired).status_code == 409 and row(mf_app) is None
    finally:
        lock.close()
    assert submit(paired).status_code == 200


def test_activation_failure_rolls_back(paired, helper, mf_app, client):
    helper.fail("wifi-apply")
    start_pending(mf_app, paired)
    rw = row(mf_app)
    assert rw["state"] == "rolled_back" and rw["reason"] == "activation" and rw["new_password"] is None
    assert helper.calls() == ["wifi-apply stdin_lines=2", "wifi-rollback stdin_lines=0"]
    wc = client.get("/pairing/state", environ_base=LOCAL).get_json()["wifi_change"]
    assert wc["state"] == "rolled_back" and wc["old_ssid"] == "MemoryFrame-AP"


# ---------- keep / commit ----------

def test_keep_page_and_commit(paired, helper, mf_app, client):
    start_pending(mf_app, paired)
    html = htmlmod.unescape(paired.get("/admin/wifi/keep", environ_base=PHONE).get_data(as_text=True))
    assert "Keep new Wi-Fi?" in html
    assert 'You\'re connected to "Frame Home". Tap to keep it. Other phones will need to join it too.' in html
    assert "Old Wi-Fi returns in" in html
    r = paired.post("/admin/wifi/keep", environ_base=PHONE)
    assert r.status_code == 200 and "✓ Wi-Fi updated" in htmlmod.unescape(r.get_data(as_text=True))
    assert helper.calls()[-1] == "wifi-commit stdin_lines=0"
    rw = row(mf_app)
    assert rw["state"] == "committed" and rw["new_password"] is None
    # the frame and the onboarding QR now use the new Wi-Fi
    st = client.get("/pairing/state", environ_base=LOCAL).get_json()
    assert st["wifi_change"]["state"] == "committed" and st["ssid"] == "Frame Home"
    # later visitors: admin home, no second commit
    assert paired.post("/admin/wifi/keep", environ_base=PHONE).status_code == 303
    assert helper.calls().count("wifi-commit stdin_lines=0") == 1


def test_get_never_commits(paired, helper, mf_app):
    start_pending(mf_app, paired)
    for _ in range(3):
        paired.get("/admin/wifi/keep", environ_base=PHONE)
        paired.get("/admin/", environ_base=PHONE)
    assert row(mf_app)["state"] == "pending"


def test_keep_must_come_over_the_hotspot(paired, helper, mf_app):
    start_pending(mf_app, paired)
    r = paired.post("/admin/wifi/keep", environ_base=LAN_PHONE)
    assert r.status_code == 403 and row(mf_app)["state"] == "pending"


def test_keep_after_deadline_too_late(paired, helper, mf_app):
    start_pending(mf_app, paired)
    conn = mfdb.connect(mf_app.config["DB_PATH"])
    conn.execute("UPDATE wifi_change SET deadline = ?", (time.time() - 1,))
    conn.commit()
    conn.close()
    r = paired.post("/admin/wifi/keep", environ_base=PHONE)
    assert r.status_code == 409
    assert 'Too late, the old Wi-Fi is back. Join "MemoryFrame-AP" and try again.' in htmlmod.unescape(r.get_data(as_text=True))
    assert row(mf_app)["state"] == "rolled_back"


def test_commit_helper_failure_rolls_back(paired, helper, mf_app):
    start_pending(mf_app, paired)
    helper.fail("wifi-commit")
    r = paired.post("/admin/wifi/keep", environ_base=PHONE)
    assert r.status_code == 500 and row(mf_app)["state"] == "rolled_back"


# ---------- rollback ----------

def test_timeout_rolls_back(paired, helper, mf_app, client):
    mf_app.config["WIFI_WINDOW"] = 0.3
    start_pending(mf_app, paired)
    end = time.time() + 5
    while row(mf_app)["state"] == "pending" and time.time() < end:
        time.sleep(0.05)
    rw = row(mf_app)
    assert rw["state"] == "rolled_back" and rw["reason"] == "timeout"
    assert helper.calls()[-1] == "wifi-rollback stdin_lines=0"
    html = htmlmod.unescape(paired.get("/admin/", environ_base=PHONE).get_data(as_text=True))
    assert 'Wi-Fi change cancelled. The frame is still on "MemoryFrame-AP".' in html
    assert "Wi-Fi change cancelled" not in htmlmod.unescape(paired.get("/admin/", environ_base=PHONE).get_data(as_text=True))


def test_restart_while_pending_rolls_back(paired, helper, mf_app):
    start_pending(mf_app, paired)
    wifi.startup_check(mf_app)
    end = time.time() + 5
    while row(mf_app)["state"] == "pending" and time.time() < end:
        time.sleep(0.05)
    assert row(mf_app)["state"] == "rolled_back" and row(mf_app)["reason"] == "restart"


def test_reset_boot_drops_committed_override(paired, helper, mf_app, tmp_path):
    start_pending(mf_app, paired)
    paired.post("/admin/wifi/keep", environ_base=PHONE)
    conn = mfdb.connect(mf_app.config["DB_PATH"])
    assert wifi.current_wifi(conn)[0] == "Frame Home"
    (tmp_path / "netmode.json").write_text('{"mode": "product", "reset": true, "boot_id": "b9"}')
    wifi.startup_check(mf_app)
    assert wifi.current_wifi(conn)[0] == "MemoryFrame-AP"
    conn.close()


# ---------- the password never leaks ----------

def test_password_never_logged_or_returned(paired, helper, mf_app, client, caplog):
    with caplog.at_level(logging.DEBUG):
        start_pending(mf_app, paired)
        bodies = [paired.get(u, environ_base=PHONE).get_data(as_text=True)
                  for u in ("/admin/wifi/keep", "/admin/wifi/")]
        st = client.get("/pairing/state", environ_base=LOCAL).get_json()
        fs = client.get("/frame/status", environ_base=LOCAL).get_data(as_text=True)
        bodies.append(paired.post("/admin/wifi/keep", environ_base=PHONE).get_data(as_text=True))
        wifi.rollback(mf_app, "timeout")
    st.get("wifi_change", {}).pop("qr_svg", None)
    st.pop("wifi_qr_svg", None)
    for text in bodies + [str(st), fs, caplog.text, "\n".join(helper.calls())]:
        assert NEW_PW not in text and OLD_PW not in text


def test_missing_netmode_state_means_dev_409_helper_never_called(paired, helper, mf_app, tmp_path):
    """#41 SAFETY: no netmode.json (dev Pi, service no-op) -> dev: nothing touches home Wi-Fi."""
    (tmp_path / "netmode.json").unlink()
    html = htmlmod.unescape(paired.get("/admin/wifi/", environ_base=PHONE).get_data(as_text=True))
    assert "Wi-Fi can't be changed right now." in html and 'id="wifiForm"' not in html
    assert submit(paired).status_code == 409 and row(mf_app) is None
    assert helper.calls() == [] and helper.start == []
