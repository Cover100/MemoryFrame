"""#154: remove ONE paired phone (per-device revoke) from Settings -> Paired phones.

Builds on #153's device-row model: removing device X revokes every session of X (its cookie gets 401 and it
must pair again with a code); every other device keeps its row and sessions. Removing the phone you are using
signs it out cleanly (cookie cleared) and the pair page says why. Same auth (paired session) and CSRF
(double-submit token) as Sign out all phones. Cookie values are never printed: comparisons are booleans."""
import re
import secrets
from urllib.parse import urlsplit

import pytest

from memoryframe import auth
from memoryframe import db as mfdb

LOCAL = {"REMOTE_ADDR": "127.0.0.1"}
PHONE = {"REMOTE_ADDR": "10.42.0.23"}
IPHONE = ("Mozilla/5.0 (iPhone; CPU iPhone OS 18_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) "
          "Version/18.1 Mobile/15E148 Safari/604.1")
ANDROID = ("Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) "
           "Chrome/131.0.0.0 Mobile Safari/537.36")


def q(app, sql, args=()):
    conn = mfdb.connect(app.config["DB_PATH"])
    try:
        return [tuple(r) for r in conn.execute(sql, args).fetchall()]
    finally:
        conn.close()


def pair(c, ua=IPHONE):
    code = c.post("/pairing/start", environ_base=LOCAL).get_json()["round"]["code"]
    r = c.post("/pair", data={"code": code}, environ_base=PHONE, headers={"User-Agent": ua})
    assert r.status_code == 303, r.status_code
    return q(c.application, "SELECT device_id FROM sessions WHERE revoked = 0 ORDER BY id DESC LIMIT 1")[0][0]


def ok(c):
    return c.get("/api/v1/status", environ_base=PHONE).status_code == 200


def remove(c, dev_id, **kw):
    return c.post(f"/admin/devices/{dev_id}/remove", environ_base=PHONE, **kw)


def settings(c):
    r = c.get("/admin/settings", environ_base=PHONE)
    assert r.status_code == 200
    return r.get_data(as_text=True)


def live(app, dev_id):
    return q(app, "SELECT COUNT(*) FROM sessions WHERE device_id = ? AND revoked = 0 AND expires_at > ?",
             (dev_id, mfdb.now()))[0][0]


def state(c):
    return c.get("/pairing/state", environ_base=LOCAL).get_json()


@pytest.fixture
def two(mf_app):
    a, b = mf_app.test_client(), mf_app.test_client()
    return a, pair(a, IPHONE), b, pair(b, ANDROID)


# ---------- 1. the list, with a confirm step per device ----------

def test_settings_lists_each_paired_phone_with_dates_and_a_confirmed_remove(mf_app, two):
    a, a_id, b, b_id = two
    h = settings(a)
    sec = h[h.index('id="phonesSection"'):]
    sec = sec[:sec.index("</section>")]
    rows = re.findall(r'<li data-device="(\d+)"', sec)
    assert rows == [str(a_id), str(b_id)]                                   # oldest first, live devices only
    assert "iPhone · Safari" in sec and "Android · Chrome" in sec           # tell phones apart, not raw UAs
    assert "Mozilla/5.0" not in sec
    assert sec.count("This phone") == 1
    mine = sec[sec.index(f'data-device="{a_id}"'):sec.index(f'data-device="{b_id}"')]
    assert "This phone" in mine
    assert len(re.findall(r"Paired \d{1,2} \w{3} \d{4}", sec)) == 2 and sec.count("Last seen just now") == 2
    for d in (a_id, b_id):
        form = re.search(rf'<form method="post" action="/admin/devices/{d}/remove" class="msgConfirm devConfirm"'
                         rf' id="devConfirm{d}" role="alertdialog" aria-labelledby="devConfirmText{d}" hidden>(.*?)</form>',
                         sec, re.S)
        assert form, d                                                        # the confirm step: hidden until Remove
        assert 'name="csrf_token"' in form.group(1) and 'type="submit"' in form.group(1)
        assert re.search(r'class="msgKeep devKeep btn-default"', form.group(1))   # Keep is the safe default
        assert f'aria-controls="devConfirm{d}"' in sec
    assert "You'll be signed out on this phone" in mine and "You'll be signed out on this phone" not in sec.replace(mine, "")
    assert 'id="aboutPhones">2<' in h


def test_revoked_devices_are_not_listed(mf_app, two):
    a, a_id, b, b_id = two
    a.post("/pairing/sign-out-all", environ_base=LOCAL)
    c = mf_app.test_client()
    c_id = pair(c, ANDROID)
    assert re.findall(r'<li data-device="(\d+)"', settings(c)) == [str(c_id)]


# ---------- 2. + 3. removing X revokes only X; X's cookie is rejected afterwards ----------

def test_removing_another_phone_revokes_only_its_sessions(mf_app, two):
    a, a_id, b, b_id = two
    c = mf_app.test_client()
    c_id = pair(c, ANDROID)
    conn = mfdb.connect(mf_app.config["DB_PATH"])                           # B also holds an older live session
    extra = secrets.token_urlsafe(32)
    conn.execute("INSERT INTO sessions (device_id, token_hash, created_at, expires_at) VALUES (?, ?, ?, ?)",
                 (b_id, auth.hash_token(extra), mfdb.now(), mfdb.now() + 86400))
    conn.commit()
    conn.close()
    before_a = q(mf_app, "SELECT * FROM sessions WHERE device_id = ?", (a_id,))
    before_c = q(mf_app, "SELECT * FROM sessions WHERE device_id = ?", (c_id,))
    devs = q(mf_app, "SELECT * FROM devices WHERE id != ?", (b_id,))
    r = remove(a, b_id)
    assert r.status_code == 303 and r.location.endswith("/admin/settings?removed=1#phones")
    assert live(mf_app, b_id) == 0                                           # every B session revoked
    assert not ok(b) and b.get("/admin/", environ_base=PHONE).status_code == 302
    other = mf_app.test_client()
    other.set_cookie(auth.COOKIE, extra)
    assert not ok(other)
    assert ok(a) and ok(c)                                                   # others stay signed in
    assert q(mf_app, "SELECT * FROM sessions WHERE device_id = ?", (a_id,)) == before_a
    assert q(mf_app, "SELECT * FROM sessions WHERE device_id = ?", (c_id,)) == before_c
    assert q(mf_app, "SELECT * FROM devices WHERE id != ?", (b_id,)) == devs
    assert q(mf_app, "SELECT COUNT(*) FROM devices WHERE id = ?", (b_id,)) == [(1,)]   # row kept (as sign-out-all)
    h = a.get("/admin/settings?removed=1", environ_base=PHONE).get_data(as_text=True)   # where the 303 lands
    assert 'id="devRemoved"' in h and "Phone removed." in h
    assert 'id="devRemoved"' not in settings(a)                               # a plain visit has no banner
    assert re.findall(r'<li data-device="(\d+)"', h) == [str(a_id), str(c_id)]


def test_a_removed_phone_must_pair_again_with_a_code(mf_app, two):
    a, a_id, b, b_id = two
    remove(a, b_id)
    assert b.get("/pair", environ_base=PHONE).status_code == 200            # the pair form, not /admin/
    new_id = pair(b, ANDROID)                                                # a code: a new row (#153: no live cookie)
    assert new_id not in (a_id, b_id) and ok(b) and ok(a)


# ---------- 4. removing the phone you are using ----------

def test_removing_this_phone_signs_it_out_cleanly(mf_app, two):
    a, a_id, b, b_id = two
    old = a.get_cookie(auth.COOKIE).value
    r = remove(a, a_id)
    assert r.status_code == 303 and r.location.endswith("/pair?removed=1")
    [h] = [x for x in r.headers.getlist("Set-Cookie") if x.startswith(auth.COOKIE + "=")]
    assert h.startswith(auth.COOKIE + '=;') or h.startswith(auth.COOKIE + "=;")
    assert "Max-Age=0" in h and "HttpOnly" in h and "SameSite=Strict" in h and "Path=/" in h
    assert a.get_cookie(auth.COOKIE) is None                                # the browser drops it
    stale = mf_app.test_client()
    stale.set_cookie(auth.COOKIE, old)
    assert not ok(stale) and not ok(a)
    p = a.get("/pair?removed=1", environ_base=PHONE)
    assert p.status_code == 200
    t = p.get_data(as_text=True)
    assert 'id="removedNote"' in t and "This phone was removed from the frame." in t
    assert 'id="removedNote"' not in a.get("/pair", environ_base=PHONE).get_data(as_text=True)
    assert ok(b) and live(mf_app, b_id) == 1


# ---------- 5. paired count and the frame's Wi-Fi QR ----------

def test_paired_count_and_wifi_qr_follow_removals(mf_app, two, monkeypatch):
    monkeypatch.setenv("MEMORYFRAME_SSID", "Frame")
    monkeypatch.setenv("MEMORYFRAME_WIFI_PASSWORD", "secret-pass-123")
    a, a_id, b, b_id = two
    st = state(a)
    assert st["paired"] == 2 and st["wifi_qr_svg"] is None
    remove(a, b_id)
    st = state(a)
    assert st["paired"] == 1 and st["wifi_qr_svg"] is None
    assert 'id="aboutPhones">1<' in settings(a)
    remove(a, a_id)
    st = state(a)
    assert st["paired"] == 0 and st["wifi_qr_svg"] and st["network"] == "hotspot"   # the QR shows again


# ---------- 6. auth and CSRF as for Sign out all phones ----------

def test_remove_needs_the_csrf_token(mf_app, two):
    a, a_id, b, b_id = two
    assert remove(a, b_id, csrf=False).status_code == 403
    assert remove(a, b_id, csrf=False, data={"csrf_token": "wrong"}).status_code == 403
    assert a.post(f"/admin/devices/{b_id}/remove", environ_base=PHONE,
                  headers={"Origin": "http://evil.example"}).status_code == 403
    tok = a.get_cookie("mf_csrf").value
    assert a.post(f"/admin/devices/{b_id}/remove", environ_base=PHONE, csrf=False,
                  data={"csrf_token": tok}).status_code == 303                 # the form field works (no JS fetch)
    assert live(mf_app, b_id) == 0 and ok(a)


def test_remove_needs_a_paired_session(mf_app, two):
    a, a_id, b, b_id = two
    stranger = mf_app.test_client()
    r = remove(stranger, b_id)
    assert r.status_code == 302 and r.location.endswith("/pair")
    assert ok(b) and ok(a)
    remove(a, b_id)
    r = remove(b, a_id)                                                      # the removed phone can't strike back
    assert r.status_code == 302 and ok(a)


def test_unknown_or_already_removed_device(mf_app, two):
    a, a_id, b, b_id = two
    assert remove(a, 9999).status_code == 404 and ok(a) and ok(b)
    assert remove(a, b_id).status_code == 303
    r = remove(a, b_id)                                                      # a second tap: same result, no error
    assert r.status_code == 303 and r.location.endswith("/admin/settings?removed=1#phones") and ok(a)
    assert a.get(f"/admin/devices/{b_id}/remove", environ_base=PHONE).status_code == 405


def test_frame_sign_out_all_unchanged(mf_app, two):
    a, a_id, b, b_id = two
    assert a.post("/pairing/sign-out-all", environ_base=LOCAL).get_json() == {"revoked": 2}
    assert not ok(a) and not ok(b)


@pytest.mark.parametrize("ua,label", [
    (IPHONE, "iPhone · Safari"), (ANDROID, "Android · Chrome"),
    ("Mozilla/5.0 (iPad; CPU OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) CriOS/120 Mobile/15E148 Safari/604.1",
     "iPad · Chrome"),
    ("Mozilla/5.0 (Linux; Android 14; SM-S918B) AppleWebKit/537.36 (KHTML, like Gecko) SamsungBrowser/25.0 Chrome/121 Mobile Safari/537.36",
     "Android · Samsung Internet"),
    ("Mozilla/5.0 (Android 14; Mobile; rv:132.0) Gecko/132.0 Firefox/132.0", "Android · Firefox"),
    ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131 Safari/537.36 Edg/131",
     "Windows · Edge"),
    ("Mozilla/5.0 (Macintosh; Intel Mac OS X 14_1) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17 Safari/605.1.15",
     "Mac · Safari"),
    ("phone", "Phone"), ("", "Phone"), ("curl/8.5", "Phone"),
])
def test_device_display_label(ua, label):
    assert auth.device_display(auth.device_label(ua)) == label


def test_last_seen_wording(monkeypatch):
    now = 1_800_000_000
    assert auth.seen_ago(now - 30, now) == "just now"
    assert auth.seen_ago(now - 5 * 60, now) == "5 min ago"
    assert auth.seen_ago(now - 3 * 3600, now) == "3 h ago"
    assert auth.seen_ago(now - 86400 - 5, now) == "1 day ago"
    assert auth.seen_ago(now - 3 * 86400, now) == "3 days ago"
    assert re.fullmatch(r"on \d{1,2} \w{3} \d{4}", auth.seen_ago(now - 30 * 86400, now))
    assert auth.seen_ago(None, now) == "never"


# ---------- headless Chrome: the confirm step ----------

from test_frontend_browser import BROWSER, evaluate, page, server  # noqa: E402,F401  (fixtures)


@pytest.mark.skipif(BROWSER is None, reason="no Chromium/Chrome installed")
def test_remove_confirm_in_the_browser(page, server, mf_app):
    conn = mfdb.connect(mf_app.config["DB_PATH"])
    mine = auth.create_session(conn, IPHONE)
    auth.create_session(conn, ANDROID)
    conn.close()
    other = q(mf_app, "SELECT id FROM devices WHERE name LIKE '%Android%'")[0][0]
    page.call("Emulation.setDeviceMetricsOverride", {"width": 412, "height": 915, "deviceScaleFactor": 1, "mobile": True})
    page.call("Network.enable")
    page.call("Network.setCookie", {"name": "mf_session", "value": mine, "domain": urlsplit(server).hostname,
                                    "path": "/", "httpOnly": True, "sameSite": "Strict"})
    page.call("Page.navigate", {"url": server + "admin/settings"})
    page.pump(2)
    js = "document.getElementById"
    btn = f"document.querySelector('[aria-controls=devConfirm{other}]')"
    assert evaluate(page, f"{js}('devConfirm{other}').hidden")
    evaluate(page, f"{btn}.click()")
    assert not evaluate(page, f"{js}('devConfirm{other}').hidden") and evaluate(page, f"{btn}.hidden")
    assert evaluate(page, "document.activeElement.classList.contains('devKeep')")      # Keep focused
    assert evaluate(page, f"{btn}.getAttribute('aria-expanded')") == "true"
    evaluate(page, f"{js}('devConfirm{other}').querySelector('.devKeep').click()")
    assert evaluate(page, f"{js}('devConfirm{other}').hidden") and not evaluate(page, f"{btn}.hidden")
    assert evaluate(page, "document.activeElement.getAttribute('aria-controls')") == f"devConfirm{other}"
    assert live(mf_app, other) == 1                                         # Keep removed nothing
    evaluate(page, f"{btn}.click()")
    evaluate(page, f"{js}('devConfirm{other}').querySelector('[type=submit]').click()")
    page.pump(2)
    assert live(mf_app, other) == 0
    assert evaluate(page, "location.pathname + location.search + location.hash") == "/admin/settings?removed=1#phones"
    assert evaluate(page, f"!!{js}('devRemoved')") and evaluate(page, "document.querySelectorAll('[data-device]').length") == 1
    assert evaluate(page, "document.documentElement.scrollWidth") <= 412
    assert page.exceptions() == []
