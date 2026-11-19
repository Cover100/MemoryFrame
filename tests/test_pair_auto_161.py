"""#161 auto-pair (Option A; Sage auto-pair-161/index.md, Wren wren.md): the QR landing submits its own POST
pair form once on load, so a scan needs no tap. GET (and HEAD, prefetch) never pair or use up the code; the
"Pair this phone" button stays as the no-JS fallback; dead QR tokens get "That QR code has expired." and the
code form. TTL, single use, CSRF, cookie flags and #153 device reuse are unchanged.

Cookie values are never printed: comparisons are booleans or SHA-256 hashes."""
import hashlib
import json
import os
import re
import threading
from pathlib import Path

import pytest

from memoryframe import auth
from memoryframe import db as mfdb

ROOT = Path(__file__).resolve().parents[1]
LOCAL = {"REMOTE_ADDR": "127.0.0.1"}
PHONE = {"REMOTE_ADDR": "10.42.0.23"}
EXPIRED = "That QR code has expired."
RESCAN = "Scan the new code on the frame, or enter its 6-digit code below."
PAIRING = "Pairing this phone\u2026"
SLOW = "This is taking a while. Tap the button to pair."


@pytest.fixture
def clock(monkeypatch):
    t = {"now": 1_000_000.0}
    monkeypatch.setattr(auth, "clock", lambda: t["now"])
    return t


def start(client):
    return client.post("/pairing/start", environ_base=LOCAL).get_json()


def tok(app):
    return app.extensions["mf_pairing"].token


def q(app, sql, args=()):
    conn = mfdb.connect(app.config["DB_PATH"])
    try:
        return [tuple(r) for r in conn.execute(sql, args).fetchall()]
    finally:
        conn.close()


def sha(v):
    return hashlib.sha256(v.encode()).hexdigest()


def live(client):
    return client.get("/pairing/state", environ_base=LOCAL).get_json()["round"] is not None


# ---------- GET / HEAD / prefetch never use up the code ----------

PREFETCH = [{"Sec-Purpose": "prefetch"}, {"Sec-Purpose": "prefetch;prerender"}, {"Purpose": "prefetch"},
            {"X-Moz": "prefetch"}]


def test_get_head_and_prefetch_never_consume(client, mf_app, clock):
    start(client)
    t = tok(mf_app)
    p = mf_app.extensions["mf_pairing"]
    for _ in range(3):
        assert client.get(f"/pair?t={t}", environ_base=PHONE).status_code == 200
    assert client.head(f"/pair?t={t}", environ_base=PHONE).status_code == 200
    for h in PREFETCH:
        assert client.get(f"/pair?t={t}", headers=h, environ_base=PHONE).status_code == 503
    assert live(client) and p.token == t and p.wrong == 0
    assert client.get_cookie(auth.COOKIE) is None
    clock["now"] += 119                                         # still live at the TTL boundary
    assert client.get(f"/pair?t={t}", environ_base=PHONE).status_code == 200
    assert client.post("/pair", data={"token": t}, environ_base=PHONE).status_code == 303


def test_prefetch_get_is_503_no_store(client, mf_app):
    start(client)
    r = client.get(f"/pair?t={tok(mf_app)}", headers={"Sec-Purpose": "prefetch"}, environ_base=PHONE)
    assert r.status_code == 503 and r.headers["Cache-Control"] == "no-store" and r.data == b""


def test_dead_token_peek_counts_no_wrong_try_or_rate_limit(client, mf_app):
    start(client)
    p = mf_app.extensions["mf_pairing"]
    for _ in range(15):                                         # more than RATE_LIMIT and MAX_WRONG
        assert client.get("/pair?t=junk", environ_base=PHONE).status_code == 200
    assert p.wrong == 0 and live(client) and not p.attempts.get(PHONE["REMOTE_ADDR"])


# ---------- the landing page ----------

def test_live_token_landing_has_auto_submit_and_no_js_fallback(client, mf_app):
    start(client)
    t = tok(mf_app)
    r = client.get(f"/pair?t={t}", environ_base=PHONE)
    h = r.get_data(as_text=True)
    assert r.status_code == 200 and r.headers["Cache-Control"] == "no-store"
    form = re.search(r'<form method="post" action="/pair" id="tokenForm".*?</form>', h, re.S).group(0)
    assert 'name="csrf_token" value="' in form and f'name="token" value="{t}"' in form
    assert 'name="device"' in form                               # PR163 iPad hint still sent
    assert '<button type="submit" class="block big" id="tokenButton">Pair this phone</button>' in form   # no-JS
    assert 'id="pairStatus" role="status" aria-live="polite">You scanned the code on the frame.</p>' in form
    assert re.search(r'<script src="/static/pair-auto\.js\?v=[0-9a-f]+"></script>', h)
    assert "/static/pair.js" in h and h.index("/static/pair.js") < h.index("/static/pair-auto.js")
    assert not re.search(r"<script(?![^>]*\ssrc=)[^>]*>", h)    # no inline script (CSP)
    assert 'id="codeForm"' in h and 'id="helpCard"' in h and EXPIRED not in h
    assert r.headers["Content-Security-Policy"] == client.get("/pair", environ_base=PHONE).headers[
        "Content-Security-Policy"]                               # CSP unchanged


def test_plain_pair_page_has_no_auto_script(client):
    h = client.get("/pair", environ_base=PHONE).get_data(as_text=True)
    assert "pair-auto.js" not in h and 'id="tokenForm"' not in h and EXPIRED not in h


@pytest.mark.parametrize("how", ["expired", "used", "junk", "oversized", "rotated"])
def test_dead_token_shows_expired_note_and_code_form(client, mf_app, clock, how):
    start(client)
    t = tok(mf_app)
    if how == "expired":
        clock["now"] += 120
    elif how == "used":
        assert mf_app.test_client().post("/pair", data={"token": t}, environ_base=PHONE).status_code == 303
    elif how == "junk":
        t = "not-a-real-token"
    elif how == "oversized":
        t = "x" * 500
    else:
        start(client)                                            # a new round: the old QR is dead
    r = client.get(f"/pair?t={t}", environ_base=PHONE)
    h = r.get_data(as_text=True)
    assert r.status_code == 200 and r.headers["Cache-Control"] == "no-store"
    assert f'<p class="err" role="status" id="qrExpired">{EXPIRED}</p>' in h and RESCAN in re.sub(r"</?span[^>]*>", "", h)
    assert 'id="tokenForm"' not in h and "You scanned the code on the frame." not in h
    assert "pair-auto.js" not in h and 'name="token"' not in h
    assert 'id="code" name="code"' in h and "Pair new device" in h      # the normal code form + hint
    assert h.index(EXPIRED) < h.index('id="codeForm"')


def test_failed_post_400_adds_rescan_sentence(client, mf_app):
    start(client)
    t = tok(mf_app)
    assert client.post("/pair", data={"token": t}, environ_base=PHONE).status_code == 303
    other = mf_app.test_client()
    r = other.post("/pair", data={"token": t}, environ_base={"REMOTE_ADDR": "10.42.0.24"})
    h = r.get_data(as_text=True)
    assert r.status_code == 400
    assert "That code is wrong or has expired. Scan the new code on the frame to try again." in h
    assert "Check the code on the frame." not in h
    j = other.post("/pair", json={"token": t}, environ_base={"REMOTE_ADDR": "10.42.0.24"})
    assert j.status_code == 400 and j.get_json()["error"] == \
        "That code is wrong or has expired. Check the code on the frame."           # JSON unchanged


def test_rate_limit_text_unchanged(client):
    start(client)
    for _ in range(10):
        client.post("/pair", data={"code": "x"}, environ_base=PHONE)
    r = client.post("/pair", data={"code": "x"}, environ_base=PHONE)
    assert r.status_code == 429 and "Too many attempts. Wait a minute and try again.</p>" in r.get_data(as_text=True)


# ---------- already paired / #153 / single use / flags ----------

def test_already_paired_phone_redirects_to_admin_and_round_stays(client, mf_app):
    code = start(client)["round"]["code"]
    assert client.post("/pair", data={"code": code}, environ_base=PHONE).status_code == 303
    start(client)
    t = tok(mf_app)
    for m in (client.get, client.head):
        r = m(f"/pair?t={t}", environ_base=PHONE)
        assert r.status_code == 302 and r.location.endswith("/admin/")
    assert live(client) and tok(mf_app) == t


def test_cookie_hidden_on_get_then_auto_post_reuses_row(mf_app):
    """The Strict cookie may be hidden on the QR GET (camera app -> browser) but sent on the page's own POST:
    #153 reuses the row (one device, same id, rotated secret, old cookie dead)."""
    phone = mf_app.test_client()
    lc = mf_app.test_client()
    code = start(lc)["round"]["code"]
    assert phone.post("/pair", data={"code": code}, environ_base=PHONE).status_code == 303
    old = phone.get_cookie(auth.COOKIE).value
    start(lc)
    t = tok(mf_app)
    fresh = mf_app.test_client()                                  # the GET without the cookie
    h = fresh.get(f"/pair?t={t}", environ_base=PHONE).get_data(as_text=True)
    assert 'id="tokenForm"' in h
    r = phone.post("/pair", data={"token": t}, environ_base=PHONE)   # the auto-POST carries the cookie
    assert r.status_code == 303
    new = phone.get_cookie(auth.COOKIE).value
    assert q(mf_app, "SELECT COUNT(*) FROM devices") == [(1,)]
    hashes = [h for (h,) in q(mf_app, "SELECT token_hash FROM sessions WHERE revoked = 0")]
    assert hashes == [sha(new)] and sha(old) != sha(new)
    stale = mf_app.test_client()
    stale.set_cookie(auth.COOKIE, old)
    assert stale.get("/api/v1/status", environ_base=PHONE).status_code == 401


def test_concurrent_auto_posts_one_wins(mf_app):
    start(mf_app.test_client())
    t = tok(mf_app)
    out = []

    def go(i):
        c = mf_app.test_client()
        out.append(c.post("/pair", data={"token": t}, environ_base={"REMOTE_ADDR": f"10.42.0.{60 + i}"}).status_code)
    th = [threading.Thread(target=go, args=(i,)) for i in range(8)]
    [x.start() for x in th]
    [x.join() for x in th]
    assert sorted(out) == [303] + [400] * 7


def test_post_without_csrf_or_foreign_origin_leaves_code_live(mf_app):
    c = mf_app.test_client()
    start(c)
    t = tok(mf_app)
    assert c.post("/pair", data={"token": t}, environ_base=PHONE, csrf=False).status_code == 403
    assert c.post("/pair", data={"token": t}, environ_base=PHONE,
                  headers={"Origin": "http://evil.example"}).status_code == 403
    assert live(c)
    assert c.post("/pair", data={"token": t}, environ_base=PHONE).status_code == 303


def test_cookie_flags_unchanged_on_auto_path(client, mf_app):
    start(client)
    r = client.post("/pair", data={"token": tok(mf_app)}, environ_base=PHONE)
    c = r.headers["Set-Cookie"]
    assert c.startswith("mf_session=") and "HttpOnly" in c and "SameSite=Strict" in c and "Path=/" in c
    assert "Secure" not in c and "Domain" not in c and "Max-Age=31536000" in c


# ---------- the script itself: plain JS for Samsung Internet / iPhone Safari ----------

def test_pair_auto_js_is_plain_es5():
    js = (ROOT / "static" / "pair-auto.js").read_text()
    code = "\n".join(line.split("//")[0] for line in js.splitlines())
    for bad in ("=>", "let ", "const ", "`", "fetch(", "Promise", "classList", "async ", "await ", "?.", "??",
                "class ", "...", "requestSubmit"):
        assert bad not in code, bad
    assert "document.prerendering" in code and "visibilityState" in code and "persisted" in code
    assert "form.submit()" in code and "submitted" in code and "4000" in code
    assert PAIRING.replace("\u2026", "\\u2026") in js and SLOW in js


# ---------- headless Chrome (phone viewport) ----------

from test_frontend_browser import BROWSER, evaluate, page, server  # noqa: E402,F401  (fixtures)

browser = pytest.mark.skipif(BROWSER is None, reason="no Chromium/Chrome installed")
ADMIN = "location.pathname === '/admin/'"          # admin.js then drops ?paired=1 (MF-33)
STUB_SUBMIT = "HTMLFormElement.prototype.submit = function(){ window.__submits = (window.__submits || 0) + 1; };"


def _shot(page, name):
    d = os.environ.get("MF_PAIR_SHOTS")
    if d:
        import base64
        os.makedirs(d, exist_ok=True)
        png = base64.b64decode(page.call("Page.captureScreenshot", {"format": "png"})["result"]["data"])
        with open(os.path.join(d, name), "wb") as f:
            f.write(png)


def _phone(page, width=390, scripts=(), js=True):
    page.call("Emulation.setDeviceMetricsOverride", {"width": width, "height": 844, "deviceScaleFactor": 1,
                                                    "mobile": True})
    page.call("Network.enable")
    page.call("Page.navigate", {"url": "about:blank"})           # leave the frame page (it polls /pairing/*)
    page.pump(0.3)
    page.call("Network.clearBrowserCookies")
    page.call("Emulation.setScriptExecutionDisabled", {"value": not js})
    for s in scripts:
        page.call("Page.addScriptToEvaluateOnNewDocument", {"source": s})


def _go(page, server, path, settle=0.8):
    page.call("Page.navigate", {"url": server + path})
    page.pump(settle)


def _wait(page, expr, secs=8):
    import time
    end = time.monotonic() + secs
    while not evaluate(page, expr) and time.monotonic() < end:
        page.pump(0.2)
    return evaluate(page, expr)


def _vis(page, el_id):
    return evaluate(page, f"(()=>{{const e=document.getElementById('{el_id}');"
                          f"return !!e && getComputedStyle(e).display !== 'none';}})()")


def _paired(app):
    return q(app, "SELECT COUNT(*) FROM sessions WHERE revoked = 0")[0][0]


@browser
def test_browser_live_qr_pairs_with_no_tap(page, server, mf_app):
    _phone(page)
    start(mf_app.test_client())
    _go(page, server, f"pair?t={tok(mf_app)}", settle=0.2)
    assert _wait(page, ADMIN), evaluate(page, "location.href")
    assert _paired(mf_app) == 1 and mf_app.extensions["mf_pairing"].token is None
    assert page.exceptions() == []


@browser
@pytest.mark.parametrize("width", [360, 390])
def test_browser_pairing_then_slow_state(page, server, mf_app, width):
    """Wren state 1: only hero, heading and "Pairing this phone…"; after ~4 s everything is back."""
    _phone(page, width, scripts=[STUB_SUBMIT])                  # hold the page in state 1 (no navigation)
    start(mf_app.test_client())
    _go(page, server, f"pair?t={tok(mf_app)}", settle=0.5)
    assert evaluate(page, "document.getElementById('pairStatus').textContent") == PAIRING
    assert evaluate(page, "document.getElementById('tokenForm').className").strip() == "tok is-pairing"
    assert evaluate(page, "window.__submits") == 1
    for el in ("tokenButton", "orCode", "codeForm", "helpCard"):
        assert not _vis(page, el), el
    assert evaluate(page, "document.querySelector('h1').textContent") == "Pair this phone"
    assert evaluate(page, "document.documentElement.scrollWidth") <= width
    _shot(page, f"161-pairing-{width}.png")
    assert _wait(page, f"document.getElementById('pairStatus').textContent === {json.dumps(SLOW)}", secs=6)
    for el in ("tokenButton", "orCode", "codeForm", "helpCard"):
        assert _vis(page, el), el
    assert "is-pairing" not in evaluate(page, "document.getElementById('tokenForm').className")
    assert evaluate(page, "window.__submits") == 1                # once only
    assert _paired(mf_app) == 0 and mf_app.extensions["mf_pairing"].token is not None
    _shot(page, f"161-slow-{width}.png")
    assert page.exceptions() == []


@browser
def test_browser_hidden_page_waits_until_visible(page, server, mf_app):
    hide = ("Object.defineProperty(document, 'visibilityState', {configurable: true, get: function(){"
            " return window.__vis || 'hidden'; }});")
    _phone(page, scripts=[hide])
    start(mf_app.test_client())
    t = tok(mf_app)
    _go(page, server, f"pair?t={t}", settle=1.5)
    assert evaluate(page, "location.pathname") == "/pair" and _paired(mf_app) == 0
    assert mf_app.extensions["mf_pairing"].token == t                 # not used while hidden
    evaluate(page, "window.__vis = 'visible'; document.dispatchEvent(new Event('visibilitychange'));"
                   "document.dispatchEvent(new Event('visibilitychange'))")
    assert _wait(page, ADMIN)
    assert _paired(mf_app) == 1 and q(mf_app, "SELECT COUNT(*) FROM sessions")[0][0] == 1   # once


@browser
@pytest.mark.parametrize("width", [360, 390])
def test_browser_no_js_shows_button_and_it_pairs(page, server, mf_app, width):
    _phone(page, width, js=False)
    start(mf_app.test_client())
    _go(page, server, f"pair?t={tok(mf_app)}")
    assert evaluate(page, "document.getElementById('pairStatus').textContent") == "You scanned the code on the frame."
    for el in ("tokenButton", "orCode", "codeForm", "helpCard"):
        assert _vis(page, el), el
    assert _paired(mf_app) == 0
    _shot(page, f"161-no-js-{width}.png")
    evaluate(page, "document.getElementById('tokenButton').click()")
    assert _wait(page, ADMIN)
    assert _paired(mf_app) == 1


@browser
@pytest.mark.parametrize("width", [360, 390])
def test_browser_expired_and_failed_states(page, server, mf_app, width):
    _phone(page, width)
    start(mf_app.test_client())
    _go(page, server, "pair?t=expired-or-used-token")
    assert evaluate(page, "document.getElementById('qrExpired').textContent") == EXPIRED
    assert evaluate(page, "getComputedStyle(document.getElementById('qrExpired')).color") == evaluate(
        page, "(()=>{const p=document.createElement('p');p.className='err';document.getElementById('page')"
              ".appendChild(p);const c=getComputedStyle(p).color;p.remove();return c;})()")
    assert not evaluate(page, "!!document.getElementById('tokenForm')") and _vis(page, "codeForm")
    assert evaluate(page, "document.documentElement.scrollWidth") <= width
    _shot(page, f"161-expired-{width}.png")
    # state 3: the auto-POST failed (code used up elsewhere between GET and POST)
    _phone(page, width, scripts=[STUB_SUBMIT])
    start(mf_app.test_client())
    _go(page, server, f"pair?t={tok(mf_app)}", settle=0.4)
    assert mf_app.test_client().post("/pair", data={"token": tok(mf_app)},
                                     environ_base={"REMOTE_ADDR": "10.42.0.99"}).status_code == 303
    evaluate(page, "HTMLFormElement.prototype.submit = Object.getPrototypeOf(document.createElement('form'))"
                   ".constructor.prototype.submit; document.getElementById('tokenButton').hidden = false;"
                   "document.getElementById('tokenButton').click()")
    assert _wait(page, "!!document.querySelector('.err[role=alert]')")
    assert "Scan the new code on the frame to try again." in evaluate(
        page, "document.querySelector('.err[role=alert]').textContent")
    _shot(page, f"161-failed-{width}.png")
