"""Runs the real slideshow page in headless Chromium and fails on ANY uncaught
JS error (catches undefined globals such as a lost const after a bad merge).
Skipped when no Chromium/Chrome is installed (CI runners have one)."""
import base64
import shutil
import socket
import subprocess
import tempfile
import threading
import time

import pytest
from werkzeug.serving import make_server

from cdp import CDP, page_ws_url

BROWSER = next((shutil.which(b) for b in ("google-chrome", "chromium", "chromium-browser")
                if shutil.which(b)), None)
pytestmark = pytest.mark.skipif(BROWSER is None, reason="no Chromium/Chrome installed")

# 1x1 PNG
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture
def server(mf_app, lib, backlight):
    lib.add("a.png", PNG)
    lib.add("b.png", PNG + b"\0")   # distinct checksum (duplicates are skipped)
    port = free_port()
    srv = make_server("127.0.0.1", port, mf_app, threaded=True)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{port}/"
    srv.shutdown()


@pytest.fixture
def page(server):
    port = free_port()
    profile = tempfile.mkdtemp(prefix="mf-chrome-")
    proc = subprocess.Popen(
        [BROWSER, "--headless=new", "--no-sandbox", "--disable-gpu", f"--user-data-dir={profile}",
         f"--remote-debugging-port={port}", "--remote-debugging-address=127.0.0.1",
         f"--remote-allow-origins=http://127.0.0.1:{port}", "--window-size=800,480",
         "--no-first-run", "--password-store=basic", "about:blank"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        cdp = CDP(page_ws_url(port))
        cdp.call("Runtime.enable")
        cdp.call("Log.enable")
        cdp.call("Page.enable")
        # The frame's screen is exactly 800x480; don't depend on how this Chrome sizes its window.
        cdp.call("Emulation.setDeviceMetricsOverride",
                 {"width": 800, "height": 480, "deviceScaleFactor": 1, "mobile": False})
        cdp.call("Page.navigate", {"url": server})
        yield cdp
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        shutil.rmtree(profile, ignore_errors=True)


def fmt(exc):
    return [(e.get("exception", {}).get("description") or e.get("text"), e.get("lineNumber"))
            for e in exc]


def csp_violations(page):
    msgs = [e["params"]["entry"]["text"] for e in page.events if e.get("method") == "Log.entryAdded"]
    msgs += [str(e["params"].get("args")) for e in page.events
             if e.get("method") == "Runtime.consoleAPICalled" and e["params"].get("type") == "error"]
    return [m for m in msgs if "Content Security Policy" in m or "Refused to" in m]


def test_page_runs_without_js_errors(page):
    page.pump(3)
    assert page.exceptions() == [], fmt(page.exceptions())
    assert csp_violations(page) == []
    state = page.call("Runtime.evaluate", {
        "expression": "JSON.stringify({p: playlist.length, playing: isPlaying, b: brightnessSupported})",
        "returnByValue": True})["result"]["result"]["value"]
    assert state == '{"p":2,"playing":true,"b":true}'


def test_brightness_drag_has_no_js_errors_and_writes(page, backlight):
    page.pump(2)
    # Hold 1.2 s, then drag up: exercises setBrightness/sendBrightness/flushBrightness.
    page.call("Input.dispatchMouseEvent", {"type": "mousePressed", "x": 400, "y": 300,
                                           "button": "left", "clickCount": 1})
    page.pump(1.3)
    for i in range(30):
        page.call("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": 400, "y": 300 - i * 5,
                                               "button": "left", "buttons": 1})
        time.sleep(0.02)
    page.call("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": 400, "y": 150,
                                           "button": "left", "clickCount": 1})
    page.pump(1)
    assert page.exceptions() == [], fmt(page.exceptions())
    assert (backlight / "brightness").read_text().strip() != "100"   # a POST reached the fake sysfs


# ---------------- MF-12 pairing overlay ----------------

def evaluate(page, expr):
    return page.call("Runtime.evaluate", {"expression": expr, "returnByValue": True})["result"]["result"].get("value")


OVERLAY = """JSON.stringify((()=>{const o=document.getElementById('pairOverlay');
 const c=document.getElementById('pairCode'); const r=document.getElementById('pairBox').getBoundingClientRect();
 const q=document.querySelector('#pairQr svg');
 return {visible:getComputedStyle(o).display!=='none', code:c.textContent,
   font:parseFloat(getComputedStyle(c).fontSize), box:[r.left,r.top,r.right,r.bottom], qr:!!q,
   vw:innerWidth, vh:innerHeight};})())"""


def overlay(page):
    import json
    return json.loads(evaluate(page, OVERLAY))


def phone_pair(server, code):
    """Like a phone: load /pair (gets the CSRF cookie + form token), then submit the code."""
    import http.cookiejar
    import re
    import urllib.parse
    import urllib.request
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    html = opener.open(server + "pair").read().decode()
    token = re.search(r'name="csrf_token" value="([^"]+)"', html).group(1)
    form = urllib.parse.urlencode({"code": code, "csrf_token": token}).encode()
    assert opener.open(urllib.request.Request(server + "pair", data=form)).geturl().endswith("/admin/")


def visible(page, el_id):
    return evaluate(page, f"getComputedStyle(document.getElementById('{el_id}')).display !== 'none'")


def test_pairing_overlay_shown_until_paired_then_long_press(page, server, mf_app, monkeypatch):
    import json
    monkeypatch.setenv("MEMORYFRAME_SSID", "Frame-WiFi")
    monkeypatch.setenv("MEMORYFRAME_WIFI_PASSWORD", "correct horse;battery")
    page.pump(2.5)
    o = overlay(page)
    assert o["visible"] and o["qr"]
    code = mf_app.extensions["mf_pairing"].code
    assert o["code"].replace(" ", "") == code
    assert o["font"] >= 72
    left, top, right, bottom = o["box"]
    assert (o["vw"], o["vh"]) == (800, 480)
    assert left >= 0 and top >= 0 and right <= o["vw"] and bottom <= o["vh"], o   # fits 800x480
    # MF-19: Wi-Fi join QR first, then the site QR
    assert visible(page, "wifiStep") and evaluate(page, "!!document.querySelector('#wifiQr svg')")
    assert evaluate(page, "document.getElementById('siteTitle').textContent") == "2. Open site"

    phone_pair(server, code)
    page.pump(2.5)
    assert not overlay(page)["visible"]
    assert not visible(page, "wifiStep") or not overlay(page)["visible"]

    # still >= 3 s long-press opens the MF-19 menu (2 s is not enough)
    page.call("Input.dispatchMouseEvent", {"type": "mousePressed", "x": 400, "y": 240,
                                           "button": "left", "clickCount": 1})
    page.pump(2.0)
    assert not visible(page, "menuOverlay")
    page.pump(1.5)
    page.call("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": 400, "y": 240,
                                           "button": "left", "clickCount": 1})
    page.pump(1.0)
    assert visible(page, "menuOverlay")
    menu = json.loads(evaluate(page, """JSON.stringify((()=>{const b=document.getElementById('menuBox').getBoundingClientRect();
      const f=[...document.querySelectorAll('#menuBox th,#menuBox td,#menuBox button')].map(e=>parseFloat(getComputedStyle(e).fontSize));
      const t=id=>document.getElementById(id).textContent;
      return {box:[b.left,b.top,b.right,b.bottom], minFont:Math.min(...f), ssid:t('menuSsid'), paired:t('menuPaired'),
              free:t('menuFree'), version:t('menuVersion'), ip:t('menuIp'),
              buttons:[...document.querySelectorAll('#menuBox button')].map(b=>b.textContent)};})())"""))
    assert menu["box"][2] <= 800 and menu["box"][3] <= 480 and menu["box"][0] >= 0 and menu["box"][1] >= 0, menu
    assert menu["minFont"] >= 24, menu                      # legible from 1 m on 800x480
    assert menu["ssid"] == "Frame-WiFi" and menu["paired"] == "1" and "GB" in menu["free"]
    assert menu["version"].startswith("0.2.0") and menu["ip"] != "-"
    assert {"Pair new device", "Sign out all phones", "Close"} <= set(menu["buttons"])

    # "Pair new device" -> pairing overlay with a fresh code (no Wi-Fi QR: a phone is paired)
    evaluate(page, "document.getElementById('menuPair').click()")
    page.pump(1.0)
    o = overlay(page)
    assert o["visible"] and not visible(page, "menuOverlay") and not visible(page, "wifiStep")
    assert o["code"].replace(" ", "") == mf_app.extensions["mf_pairing"].code

    # "Sign out all phones" from the menu -> back to first-run onboarding
    evaluate(page, "document.getElementById('pairClose').click()")
    evaluate(page, "memoryFramePairing.openMenu()")
    page.pump(0.5)
    evaluate(page, "document.getElementById('menuSignOut').click()")
    page.pump(2.5)
    assert overlay(page)["visible"] and visible(page, "wifiStep")
    assert page.exceptions() == [], fmt(page.exceptions())
    assert csp_violations(page) == []


def test_empty_library_screen(page, server, mf_app):
    from memoryframe import auth, db as mfdb
    conn = mfdb.connect(mf_app.config["DB_PATH"])
    auth.create_session(conn, "phone")
    conn.execute("UPDATE media SET hidden = 1")
    conn.commit()
    conn.close()
    page.pump(3)
    assert visible(page, "emptyOverlay") and not overlay(page)["visible"]
    assert "10.42.0.1" in evaluate(page, "document.getElementById('emptyOverlay').textContent")
    assert page.exceptions() == [], fmt(page.exceptions())