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
        cdp.call("Page.enable")
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


def test_page_runs_without_js_errors(page):
    page.pump(3)
    assert page.exceptions() == [], fmt(page.exceptions())
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
    import urllib.parse
    import urllib.request
    req = urllib.request.Request(server + "pair", data=urllib.parse.urlencode({"code": code}).encode())
    urllib.request.urlopen(req)     # follows the 303 to /admin/


def test_pairing_overlay_shown_until_paired_then_long_press(page, server, mf_app):
    page.pump(2.5)
    o = overlay(page)
    assert o["visible"] and o["qr"]
    code = mf_app.extensions["mf_pairing"].code
    assert o["code"].replace(" ", "") == code
    assert o["font"] >= 72
    left, top, right, bottom = o["box"]
    assert left >= 0 and top >= 0 and right <= o["vw"] and bottom <= o["vh"], o   # fits 800x480

    phone_pair(server, code)
    page.pump(2.5)
    assert not overlay(page)["visible"]

    # still >= 3 s long-press shows it on demand with a fresh code
    page.call("Input.dispatchMouseEvent", {"type": "mousePressed", "x": 400, "y": 240,
                                           "button": "left", "clickCount": 1})
    page.pump(2.0)
    assert not overlay(page)["visible"]          # 2 s is not enough
    page.pump(1.5)
    page.call("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": 400, "y": 240,
                                           "button": "left", "clickCount": 1})
    page.pump(1.0)
    o = overlay(page)
    assert o["visible"] and o["code"].replace(" ", "") == mf_app.extensions["mf_pairing"].code
    assert page.exceptions() == [], fmt(page.exceptions())
