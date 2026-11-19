"""#154 follow-up (Hale's PR160 nits).

(1) Device names: iPadOS Safari reports a Mac User-Agent, so the pair page adds a display-only hint ("ipad")
    when the browser has a touch screen (navigator.maxTouchPoints > 1) and a Macintosh UA. The server accepts
    only that exact value, only with a Macintosh UA, and only for the stored label; auth never reads it.
    Old rows (labels cut at 80 chars) show the OS version for iPhone/iPad when the browser token is missing.
    Android shows plain "Android": Chrome's reduced UA freezes "Android 10; K" on every Android.
(2) ?removed=1 is dropped from the address bar after the banner shows (as MF-33 does for ?paired=1).
Cookie values are never printed."""
import re
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from memoryframe import auth
from memoryframe import db as mfdb

ROOT = Path(__file__).resolve().parents[1]
LOCAL = {"REMOTE_ADDR": "127.0.0.1"}
PHONE = {"REMOTE_ADDR": "10.42.0.23"}
IPAD_AS_MAC = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) "
               "Version/17.1 Safari/605.1.15")
IPHONE = ("Mozilla/5.0 (iPhone; CPU iPhone OS 18_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) "
          "Version/18.1 Mobile/15E148 Safari/604.1")
ANDROID = ("Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) "
           "Chrome/131.0.0.0 Mobile Safari/537.36")
IPAD = ("Mozilla/5.0 (iPad; CPU OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) "
        "Version/17.0 Mobile/15E148 Safari/604.1")


def names(app):
    conn = mfdb.connect(app.config["DB_PATH"])
    try:
        return [r[0] for r in conn.execute("SELECT name FROM devices ORDER BY id")]
    finally:
        conn.close()


def pair(c, ua, how="form", **extra):
    code = c.post("/pairing/start", environ_base=LOCAL).get_json()["round"]["code"]
    data = {"code": code, **extra}
    if how == "json":
        r = c.post("/pair", json=data, environ_base=PHONE, headers={"User-Agent": ua})
        assert r.status_code == 200
    else:
        r = c.post("/pair", data=data, environ_base=PHONE, headers={"User-Agent": ua})
        assert r.status_code == 303
    return r


# ---------- (1) the iPad hint ----------

@pytest.mark.parametrize("how", ["form", "json"])
def test_ipad_hint_with_a_mac_ua_is_stored_as_ipad(mf_app, client, how):
    pair(client, IPAD_AS_MAC, how=how, device="ipad")
    [name] = names(mf_app)
    assert auth.device_display(name) == "iPad · Safari" and len(name) <= auth.LABEL_MAX
    assert client.get("/api/v1/status", environ_base=PHONE).status_code == 200


def test_mac_without_the_hint_stays_mac(mf_app, client):
    pair(client, IPAD_AS_MAC)
    assert auth.device_display(names(mf_app)[0]) == "Mac · Safari"


@pytest.mark.parametrize("ua,hint", [
    (IPHONE, "ipad"),                       # only a Macintosh UA can be relabelled
    (ANDROID, "ipad"),
    (IPAD_AS_MAC, "iPad"), (IPAD_AS_MAC, "ipad "), (IPAD_AS_MAC, "android"), (IPAD_AS_MAC, "x" * 500),
    (IPAD_AS_MAC, "ipad\n<script>"),
])
def test_other_hints_or_uas_are_ignored(mf_app, client, ua, hint):
    pair(client, ua, device=hint)
    [name] = names(mf_app)
    assert name == auth.device_label(ua)                                    # stored exactly as without a hint
    assert "<script>" not in name and "x" * 50 not in name


def test_a_json_hint_that_is_not_a_string_is_ignored(mf_app, client):
    pair(client, IPAD_AS_MAC, how="json", device=["ipad"])
    assert names(mf_app) == [auth.device_label(IPAD_AS_MAC)]


def test_the_hint_never_affects_auth(mf_app, client):
    """A wrong code with the hint is still a wrong code; the hint picks no device and makes no session."""
    code = client.post("/pairing/start", environ_base=LOCAL).get_json()["round"]["code"]
    wrong = "000000" if code != "000000" else "111111"
    r = client.post("/pair", data={"code": wrong, "device": "ipad"}, environ_base=PHONE,
                    headers={"User-Agent": IPAD_AS_MAC})
    assert r.status_code == 400 and names(mf_app) == []
    src = (ROOT / "memoryframe" / "auth.py").read_text()
    body = src[src.index("def current_device"):src.index("def paired_count")]
    assert "hint" not in body and "device_hint" not in (ROOT / "memoryframe" / "websec.py").read_text()


def test_repair_with_the_hint_relabels_the_same_row(mf_app, client):
    pair(client, IPAD_AS_MAC)
    pair(client, IPAD_AS_MAC, device="ipad")                                # #153: same row, refreshed label
    [name] = names(mf_app)
    assert auth.device_display(name) == "iPad · Safari"


def test_pair_page_carries_the_hint_field_and_script(client):
    h = client.get("/pair?t=abc", environ_base=PHONE).get_data(as_text=True)
    assert h.count('<input type="hidden" name="device" value="" class="devHint">') == 2   # QR form and code form
    assert "/static/pair.js" in h
    js = (ROOT / "static" / "pair.js").read_text()
    assert "/Macintosh/.test(navigator.userAgent) && navigator.maxTouchPoints > 1" in js
    assert 'i.value = "ipad"' in js


# ---------- (1) old rows: an 80-char label without the browser token ----------

# Chrome's reduced UA, as stored for devices 19/20/21 (cut at 80 chars before #154).
ANDROID_REDUCED = "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/"
ANDROID_REDUCED_FULL = ("Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/192.168.1.1 Mobile Safari/537.36")


@pytest.mark.parametrize("ua,label", [
    (IPHONE, "iPhone · iOS 18"), (IPAD, "iPad · iPadOS 17"), (ANDROID, "Android"),
    ("Mozilla/5.0 (Linux; Android 9; SM-G960F) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Mobile",
     "Android"),
    (ANDROID_REDUCED, "Android"),
    (IPAD_AS_MAC, "Mac"), ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko)", "Windows"),
])
def test_old_80_char_labels_show_the_os_version(ua, label):
    old = auth.device_label(ua)[:80]                                         # what PR160 and earlier stored
    assert "Safari/" not in old and "Chrome/" not in old                     # the browser token was cut off
    assert auth.device_display(old) == label


def test_full_labels_are_unchanged():
    assert auth.device_display(auth.device_label(IPHONE)) == "iPhone · Safari"
    assert auth.device_display(auth.device_label(ANDROID)) == "Android · Chrome"
    assert auth.device_display(auth.device_label(ANDROID_REDUCED_FULL)) == "Android · Chrome"
    assert auth.device_display("phone") == "Phone"


# ---------- (2) ?removed=1 is stripped after the banner ----------

def test_banner_scripts_strip_removed_from_the_url():
    s = (ROOT / "static" / "settings.js").read_text()
    assert 'if(document.getElementById("devRemoved") && window.history && history.replaceState){' in s
    assert 'history.replaceState(null, "", location.pathname + location.hash);' in s     # keeps #phones
    p = (ROOT / "static" / "pair.js").read_text()
    assert 'if(document.getElementById("removedNote") && window.history && history.replaceState){' in p
    assert 'q.delete("removed")' in p                                        # keeps ?t=<token> if any


# ---------- headless Chrome ----------

from test_frontend_browser import BROWSER, evaluate, page, server  # noqa: E402,F401  (fixtures)


def _cookie(page, server, token):
    page.call("Network.enable")
    page.call("Network.setCookie", {"name": "mf_session", "value": token, "domain": urlsplit(server).hostname,
                                    "path": "/", "httpOnly": True, "sameSite": "Strict"})


@pytest.mark.skipif(BROWSER is None, reason="no Chromium/Chrome installed")
def test_banners_show_once_then_the_url_is_clean(page, server, mf_app):
    conn = mfdb.connect(mf_app.config["DB_PATH"])
    mine = auth.create_session(conn, IPHONE)
    conn.close()
    _cookie(page, server, mine)
    page.call("Page.navigate", {"url": server + "admin/settings?removed=1#phones"})
    page.pump(2)
    assert evaluate(page, "!!document.getElementById('devRemoved')")
    assert evaluate(page, "location.search") == "" and evaluate(page, "location.hash") == "#phones"
    page.call("Network.clearBrowserCookies")                                 # removed: no session any more
    page.call("Page.navigate", {"url": server + "pair?removed=1"})
    page.pump(2)
    assert evaluate(page, "!!document.getElementById('removedNote')") and evaluate(page, "location.search") == ""
    assert page.exceptions() == []


@pytest.mark.skipif(BROWSER is None, reason="no Chromium/Chrome installed")
def test_ipad_as_mac_with_touch_fills_the_hint(page, server, mf_app):
    page.call("Emulation.setUserAgentOverride", {"userAgent": IPAD_AS_MAC})
    page.call("Page.navigate", {"url": server + "pair"})                     # a real Mac: no touch screen
    page.pump(2)
    assert evaluate(page, "navigator.maxTouchPoints") <= 1
    assert evaluate(page, "[...document.querySelectorAll('input.devHint')].map(i => i.value).join()") == ""      # /pair: the code form only
    page.call("Emulation.setTouchEmulationEnabled", {"enabled": True, "maxTouchPoints": 5})   # iPadOS
    page.call("Page.navigate", {"url": server + "pair"})
    page.pump(2)
    assert evaluate(page, "navigator.maxTouchPoints") == 5
    assert evaluate(page, "[...document.querySelectorAll('input.devHint')].map(i => i.value).join()") == "ipad"
    assert page.exceptions() == []
