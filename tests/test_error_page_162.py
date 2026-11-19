"""#162 (13.3) error screens. App: GET /frame/error?code=E1|E3 is localhost-only (403 from a phone), renders the frame
token (only inside its meta tag), Wren's strings, the external CSS/JS (CSP unchanged), never a secret, address or Wi-Fi
name. Chromium: a tap does nothing; the 3 s hold opens Frame tools (Join developer Wi-Fi…, Close default); Join posts
/frame/dev-mode once (stubbed DEV_MODE_CMD) and shows the plain reason; the page returns to the slideshow by itself.
error.html (file://, E2): no hold, and no network request but the /healthz poll."""
import json
import re
import time
from pathlib import Path

import pytest

from test_boot_browser_162 import BOOT, bare, dead_url, href, nav, requests, shot, wait_js  # noqa: F401
from test_frontend_browser import BROWSER, csp_violations, evaluate, net, page, server  # noqa: F401

ROOT = Path(__file__).resolve().parents[1]
PHONE = {"REMOTE_ADDR": "10.42.0.23"}
HEAD = "The frame couldn't start"
HOW = "It will keep trying.<br>If that doesn't help, unplug the frame<br>for 10 seconds, then plug it back in."
HOLD = "Hold anywhere for 3 seconds for developer options."
CAUSE = {"E1": "Your photos didn't appear in time.", "E2": "The photo app didn't answer.",
         "E3": "The screen keeps restarting."}
browser = pytest.mark.skipif(BROWSER is None, reason="no Chromium/Chrome installed")


def test_frame_error_is_localhost_only(client):
    assert client.get("/frame/error?code=E1", environ_base=PHONE).status_code == 403
    assert client.get("/frame/error?code=E1").status_code == 200


@pytest.mark.parametrize("code", ["", "E2", "E4", "e1", "E1<script>", "../E1"])
def test_unknown_codes_are_404(client, code):
    assert client.get("/frame/error", query_string={"code": code}).status_code == 404


@pytest.mark.parametrize("code", ["E1", "E3"])
def test_page_has_wrens_copy_the_token_only_in_the_meta_and_no_secrets(client, mf_app, monkeypatch, code):
    from memoryframe import websec
    monkeypatch.setenv("MEMORYFRAME_SSID", "Frame-WiFi")
    monkeypatch.setenv("MEMORYFRAME_WIFI_PASSWORD", "correct horse;battery")
    r = client.get(f"/frame/error?code={code}")
    html = r.get_data(as_text=True)
    assert r.headers["Content-Security-Policy"] == websec.CSP
    assert r.headers["Cache-Control"] == "no-store"
    import html as htmllib
    text = htmllib.unescape(html)
    for s in (HEAD, CAUSE[code], HOW, HOLD, f"Error {code}", "Frame tools", "Join developer Wi-Fi…", ">Close<"):
        assert s in text, s
    with mf_app.app_context():
        from memoryframe.display import frame_token
        tok = frame_token()
    assert html.count(tok) == 1 and f'<meta name="mf-frame-token" content="{tok}">' in html
    for bad in ("Frame-WiFi", "correct horse", "10.42.0.1", "192.168.", "password", "<style", " style=",
                "<script>", "onclick", "http://", "https://"):
        assert bad not in html, bad
    assert re.findall(r'<script src="([^"]+)"', html) == [re.search(r'/static/errorhold\.js\?v=\w+', html).group(0)]
    assert "/static/error.css?v=" in html


def test_error_file_page_strings_match_wren():
    html = (BOOT / "error.html").read_text()
    assert HEAD in html and HOW in html and "Error E2" in html and CAUSE["E2"] in html and CAUSE["E3"] in html
    assert HOLD not in html and "toolsOverlay" not in html and "devRing" not in html     # no hold on E2
    assert "dev-mode" not in html and "X-Frame-Token" not in html


# ---------------- in Chromium ----------------

def pressed(cdp, x, y, kind):
    cdp.call("Input.dispatchMouseEvent", {"type": kind, "x": x, "y": y, "button": "left", "clickCount": 1})


def click_el(cdp, el_id):
    r = json.loads(evaluate(cdp, f"JSON.stringify(document.getElementById('{el_id}').getBoundingClientRect())"))
    x, y = r["x"] + r["width"] / 2, r["y"] + r["height"] / 2
    pressed(cdp, x, y, "mousePressed")
    pressed(cdp, x, y, "mouseReleased")


def tools_visible(cdp):
    return evaluate(cdp, "document.getElementById('toolsOverlay').classList.contains('visible')")


@browser
def test_hold_opens_frame_tools_and_join_posts_once_with_the_plain_reason(page, server, mf_app, tmp_path, net):
    count = tmp_path / "devmode.calls"
    mf_app.config["DEV_MODE_CMD"] = ["sh", "-c", f"echo x >> {count}; echo 'Error: no matching NetworkManager "
                                     "Wi-Fi profile'; exit 1"]
    nav(page, server + "frame/error?code=E1&poll=60000&retry=600000")
    wait_js(page, "document.readyState === 'complete' && !!window.mfErrorPage")
    assert evaluate(page, "document.getElementById('code').textContent") == "Error E1"
    pressed(page, 400, 240, "mousePressed")                                           # a tap does nothing
    pressed(page, 400, 240, "mouseReleased")
    page.pump(3.3)
    assert not tools_visible(page) and href(page).endswith("code=E1&poll=60000&retry=600000")
    pressed(page, 300, 200, "mousePressed")                                           # hold, 3 s
    page.pump(1.5)
    assert evaluate(page, "!document.getElementById('devRing').hidden")               # ring after 1 s
    assert not tools_visible(page)
    page.pump(1.8)
    pressed(page, 300, 200, "mouseReleased")
    assert tools_visible(page)
    labels = json.loads(evaluate(page, "JSON.stringify([...document.querySelectorAll('#toolsMenu button')]"
                                       ".map(b=>b.textContent))"))
    assert labels == ["Join developer Wi-Fi…", "Close"]
    assert evaluate(page, "document.activeElement.id") == "toolsClose"                # Close is the default
    shot(page, "error-E1-tools.png")
    click_el(page, "toolsDev")
    wait_js(page, "!document.getElementById('devModeAfter').hidden", timeout=15)
    # #167 copy (merged in PR169): the server's no-profile title, never a raw reason
    assert evaluate(page, "document.getElementById('devModeStatus').textContent") == "Developer Wi-Fi not found"
    assert evaluate(page, "document.getElementById('devModeDetail').textContent") == \
        "The frame is back on its own Wi-Fi. Phones can connect as usual."
    assert evaluate(page, "window.mfErrorPage.posts()") == 1 and count.read_text() == "x\n"   # posted once
    shot(page, "error-E1-devmode-failed.png")
    click_el(page, "devModeOk")
    page.pump(0.3)
    assert not tools_visible(page)
    assert page.exceptions() == [] and csp_violations(page) == []


@browser
def test_moving_cancels_the_hold(page, server):
    nav(page, server + "frame/error?code=E3&poll=60000&retry=600000")
    wait_js(page, "!!window.mfErrorPage")
    pressed(page, 300, 200, "mousePressed")
    page.pump(0.5)
    page.call("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": 330, "y": 200, "button": "left", "buttons": 1})
    page.pump(3)
    pressed(page, 330, 200, "mouseReleased")
    assert not tools_visible(page)


@browser
def test_error_page_returns_to_the_slideshow_by_itself(page, server):
    nav(page, server + "frame/error?code=E1&poll=200&retry=800")
    wait_js(page, "location.pathname === '/' && location.search === '?boot=1'", timeout=10)


@browser
def test_error_file_page_has_no_hold_and_only_polls_healthz(bare):
    dead = dead_url()
    nav(bare, f"file://{BOOT}/error.html?e=E2&next={dead}&poll=200")
    wait_js(bare, "!!window.mfError")
    pressed(bare, 400, 240, "mousePressed")
    bare.pump(3.4)
    pressed(bare, 400, 240, "mouseReleased")
    assert evaluate(bare, "document.querySelectorAll('#toolsOverlay,#devRing,button').length") == 0
    assert evaluate(bare, "document.body.innerText").count("Hold") == 0
    net = [u for u in requests(bare) if not u.startswith(("file:", "data:"))]
    assert net and set(net) == {dead + "/healthz"}
    assert href(bare).startswith(f"file://{BOOT}/error.html")
    assert bare.exceptions() == []


@browser
def test_error_file_page_opens_the_app_when_it_answers(bare, server):
    base = server.rstrip("/")
    nav(bare, f"file://{BOOT}/error.html?e=E2&next={base}&poll=200")
    wait_js(bare, f"location.href === {json.dumps(base + '/?boot=1')}")


@browser
@pytest.mark.parametrize("e,code", [("E3", "E3"), ("bogus", "E2")])
def test_error_file_page_codes(bare, e, code):
    nav(bare, f"file://{BOOT}/error.html?e={e}&next={dead_url()}")
    wait_js(bare, "!!window.mfError")
    assert evaluate(bare, "document.getElementById('code').textContent") == f"Error {code}"
    assert evaluate(bare, "document.getElementById('cause').textContent") == CAUSE[code]
