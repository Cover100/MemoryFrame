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
def server(request, mf_app, lib, backlight):
    if request.node.get_closest_marker("no_media") is None:
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


@pytest.fixture
def net(monkeypatch):
    """Mock the frame's network detection (what nmcli/ip would report on the Pi)."""
    from memoryframe import frameinfo
    state = {"mode": "hotspot", "lan_ip": "10.42.0.1", "ips": ["10.42.0.1"]}
    monkeypatch.setattr(frameinfo, "network", lambda: dict(state))
    monkeypatch.setenv("MEMORYFRAME_SSID", "Frame-WiFi")
    monkeypatch.setenv("MEMORYFRAME_WIFI_PASSWORD", "correct horse;battery")
    monkeypatch.setenv("MEMORYFRAME_PORTS", "80,5000")
    return state


def text(page, el_id):
    return evaluate(page, f"document.getElementById('{el_id}').textContent")


def hold(page, seconds):
    page.call("Input.dispatchMouseEvent", {"type": "mousePressed", "x": 400, "y": 240,
                                           "button": "left", "clickCount": 1})
    page.pump(seconds)
    page.call("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": 400, "y": 240,
                                           "button": "left", "clickCount": 1})


def menu_info(page):
    import json
    return json.loads(evaluate(page, """JSON.stringify((()=>{const b=document.getElementById('menuBox').getBoundingClientRect();
      const f=[...document.querySelectorAll('#menuBox th,#menuBox td,#menuBox button')].map(e=>parseFloat(getComputedStyle(e).fontSize));
      const t=id=>document.getElementById(id).textContent;
      return {box:[b.left,b.top,b.right,b.bottom], minFont:Math.min(...f), ssid:t('menuSsid'), site:t('menuSite'),
              paired:t('menuPaired'), free:t('menuFree'), version:t('menuVersion'), ip:t('menuIp'),
              buttons:[...document.querySelectorAll('#menuBox button')].map(b=>b.textContent)};})())"""))


@pytest.mark.no_media
def test_state_a_no_photos_no_phone_full_onboarding_on_hotspot(page, server, mf_app, net):
    page.pump(2.5)
    o = overlay(page)
    assert o["visible"] and o["qr"]
    code = mf_app.extensions["mf_pairing"].code
    assert o["code"].replace(" ", "") == code and o["font"] >= 72
    assert (o["vw"], o["vh"]) == (800, 480)
    left, top, right, bottom = o["box"]
    assert left >= 0 and top >= 0 and right <= o["vw"] and bottom <= o["vh"], o   # fits 800x480
    # Wi-Fi join QR first, then the site QR for the hotspot address (port 80 is served)
    assert visible(page, "wifiStep") and evaluate(page, "!!document.querySelector('#wifiQr svg')")
    assert text(page, "siteTitle") == "2. Open site"
    assert text(page, "pairUrl") == "http://10.42.0.1/pair"
    assert text(page, "pairUrlAlt") == "or http://memoryframe.lan/pair"
    assert not visible(page, "pairHint")

    phone_pair(server, code)
    page.pump(2.5)
    assert not overlay(page)["visible"]
    assert visible(page, "emptyOverlay")                   # paired, still no photos -> state C
    assert page.exceptions() == [], fmt(page.exceptions())
    assert csp_violations(page) == []


def test_state_b_photos_no_phone_slideshow_runs_on_home_wifi(page, server, mf_app, net, monkeypatch):
    net.update(mode="lan", lan_ip="192.168.50.124", ips=["192.168.50.124"])
    monkeypatch.setenv("MEMORYFRAME_PORTS", "5000")        # port 80 not served -> :5000 in URLs
    page.pump(2.5)
    # The slideshow is not blocked: no overlay, a photo on screen, only the small hint.
    assert not overlay(page)["visible"] and not visible(page, "emptyOverlay")
    assert evaluate(page, "document.getElementById('image').getAttribute('src')||''").startswith("/uploads/")
    assert visible(page, "pairHint") and "Hold 3 s" in text(page, "pairHint")
    hint = evaluate(page, "JSON.stringify(document.getElementById('pairHint').getBoundingClientRect())")
    import json
    h = json.loads(hint)
    assert h["width"] * h["height"] < 800 * 480 * 0.05          # unobtrusive: < 5 % of the screen
    assert mf_app.extensions.get("mf_pairing") is None or mf_app.extensions["mf_pairing"].current() is None

    # long-press menu -> site for the current network, then "Pair new device"
    hold(page, 2.0)
    page.pump(0.3)
    assert not visible(page, "menuOverlay")                  # 2 s is not enough
    hold(page, 3.5)
    page.pump(1.0)
    assert visible(page, "menuOverlay")
    m = menu_info(page)
    assert m["box"][2] <= 800 and m["box"][3] <= 480 and m["box"][0] >= 0 and m["box"][1] >= 0, m
    assert m["minFont"] >= 24, m
    assert m["ssid"] == "home Wi-Fi" and m["paired"] == "0" and "GB" in m["free"]
    assert m["site"] == "memoryframe.local:5000/  or  192.168.50.124:5000/"
    assert m["version"].startswith("0.2.0") and m["ip"] != "-"
    assert {"Pair new device", "Sign out all phones", "Close"} <= set(m["buttons"])
    evaluate(page, "document.getElementById('menuPair').click()")
    page.pump(1.0)
    o = overlay(page)
    assert o["visible"] and not visible(page, "menuOverlay")
    assert not visible(page, "wifiStep")                    # no hotspot -> no Wi-Fi QR
    assert text(page, "pairUrl") == "http://memoryframe.local:5000/pair"
    assert text(page, "pairUrlAlt") == "or http://192.168.50.124:5000/pair"
    assert o["code"].replace(" ", "") == mf_app.extensions["mf_pairing"].code
    # Close returns to the slideshow
    evaluate(page, "document.getElementById('pairClose').click()")
    page.pump(1.0)
    assert not overlay(page)["visible"] and visible(page, "pairHint")
    assert page.exceptions() == [], fmt(page.exceptions())
    assert csp_violations(page) == []


def test_menu_pair_and_sign_out_with_phone(page, server, mf_app, net):
    from memoryframe import auth, db as mfdb
    conn = mfdb.connect(mf_app.config["DB_PATH"])
    auth.create_session(conn, "phone")
    conn.close()
    page.pump(2.5)
    assert not overlay(page)["visible"] and not visible(page, "pairHint")
    evaluate(page, "memoryFramePairing.openMenu()")
    page.pump(0.5)
    assert menu_info(page)["ssid"] == "Frame-WiFi (hotspot)"
    evaluate(page, "document.getElementById('menuSignOut').click()")
    page.pump(2.5)
    assert not overlay(page)["visible"] and visible(page, "pairHint")   # photos exist -> hint only
    assert page.exceptions() == [], fmt(page.exceptions())


def test_state_c_empty_library_screen(page, server, mf_app, net):
    from memoryframe import auth, db as mfdb
    conn = mfdb.connect(mf_app.config["DB_PATH"])
    auth.create_session(conn, "phone")
    conn.execute("UPDATE media SET hidden = 1")
    conn.commit()
    conn.close()
    page.pump(3)
    assert visible(page, "emptyOverlay") and not overlay(page)["visible"] and not visible(page, "pairHint")
    assert "10.42.0.1" in text(page, "emptyOverlay")
    assert page.exceptions() == [], fmt(page.exceptions())


# ---------------- MF-14 upload page ----------------

def test_upload_page_uploads_files_with_progress(page, server, mf_app, tmp_path):
    import json
    from urllib.parse import urlsplit
    from PIL import Image
    from memoryframe import auth, db as mfdb
    conn = mfdb.connect(mf_app.config["DB_PATH"])
    token = auth.create_session(conn, "test phone")
    conn.close()
    host = urlsplit(server).hostname
    page.call("Network.enable")
    page.call("Network.setCookie", {"name": "mf_session", "value": token, "domain": host, "path": "/",
                                    "httpOnly": True, "sameSite": "Strict"})
    page.call("Page.navigate", {"url": server + "admin/upload"})
    page.pump(2)
    files = []
    for i in range(3):
        p = tmp_path / f"up{i}.jpg"
        Image.new("RGB", (1200, 900), (i * 70, 100, 50)).save(p, quality=90)
        files.append(str(p))
    bad = tmp_path / "notes.jpg"
    bad.write_text("not a photo")
    files.append(str(bad))
    doc = page.call("DOM.getDocument")["result"]["root"]["nodeId"]
    node = page.call("DOM.querySelector", {"nodeId": doc, "selector": "#files"})["result"]["nodeId"]
    page.call("DOM.setFileInputFiles", {"nodeId": node, "files": files})
    page.pump(4)
    rows = json.loads(evaluate(page, "JSON.stringify([...document.querySelectorAll('#list li')].map("
                                     "l=>[l.className,l.querySelector('progress').value,l.querySelector('.msg').textContent]))"))
    assert [r[0] for r in rows] == ["ok", "ok", "ok", "bad"], rows
    assert all(r[1] == 100 for r in rows)
    assert "not a photo" in rows[3][2]
    assert "4 of 4 done" in evaluate(page, "document.getElementById('summary').textContent")
    assert "Free space" in evaluate(page, "document.getElementById('storage').textContent")
    conn = mfdb.connect(mf_app.config["DB_PATH"])
    assert conn.execute("SELECT COUNT(*) FROM media WHERE original_name LIKE 'up_.jpg'").fetchone()[0] == 3
    conn.close()
    assert page.exceptions() == [], fmt(page.exceptions())
    assert csp_violations(page) == []



# ---------------- Close keeps overlays dismissed for the session ----------------

@pytest.mark.no_media
def test_close_dismisses_onboarding_for_the_session(page, server, mf_app, net):
    page.pump(2.5)
    assert overlay(page)["visible"] and visible(page, "pairClose")
    evaluate(page, "document.getElementById('pairClose').click()")
    page.pump(5)                                    # > 2 polls: must not come back
    assert not overlay(page)["visible"] and not visible(page, "emptyOverlay")
    assert visible(page, "pairHint")
    page.call("Page.reload")                        # same browser session -> still dismissed
    page.pump(3)
    assert not overlay(page)["visible"] and visible(page, "pairHint")
    # pairing is still available from the menu
    evaluate(page, "memoryFramePairing.openMenu()")
    page.pump(0.5)
    evaluate(page, "document.getElementById('menuPair').click()")
    page.pump(1.0)
    assert overlay(page)["visible"]
    evaluate(page, "document.getElementById('pairClose').click()")
    page.pump(3)
    assert not overlay(page)["visible"]
    assert page.exceptions() == [], fmt(page.exceptions())


def test_close_dismisses_empty_library_screen(page, server, mf_app, net):
    from memoryframe import auth, db as mfdb
    conn = mfdb.connect(mf_app.config["DB_PATH"])
    auth.create_session(conn, "phone")
    conn.execute("UPDATE media SET hidden = 1")
    conn.commit()
    conn.close()
    page.pump(3)
    assert visible(page, "emptyOverlay")
    evaluate(page, "document.getElementById('emptyClose').click()")
    page.pump(5)
    assert not visible(page, "emptyOverlay") and not overlay(page)["visible"]
    assert page.exceptions() == [], fmt(page.exceptions())
