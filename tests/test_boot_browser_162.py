"""#162 browser tests (headless Chromium over CDP, the existing harness): splash.html waits while /healthz is down,
hands over when it is up, rejects a non-local next, E2 after its timer, late line; the app's #bootSplash overlay only
with ?boot=1 and its fade after the first photo, the pairing/empty screen or the 10 s failsafe; CSP unchanged and no
violation; the hold menu after the fade; 800x480 screenshots vs Wren's PNG (ink and lockup centre within 2 px).
MF_SHOTS=<dir> also saves the screenshots."""
import base64
import io
import json
import os
import time
import urllib.request
from pathlib import Path

import pytest

from test_frontend_browser import (BROWSER, csp_violations, evaluate, free_port, hold, page, server,  # noqa: F401
                                   start_browser, _stop)

pytestmark = pytest.mark.skipif(BROWSER is None, reason="no Chromium/Chrome installed")

ROOT = Path(__file__).resolve().parents[1]
BOOT = ROOT / "static" / "boot"
WREN_A = BOOT / "splash-A.png"
INK = (0x0E, 0x13, 0x11)


@pytest.fixture
def bare():
    """A browser on about:blank (file:// pages need no app server)."""
    cdp, proc, profile = start_browser()
    try:
        for m in ("Runtime.enable", "Log.enable", "Page.enable", "Network.enable"):
            cdp.call(m)
        cdp.call("Emulation.setDeviceMetricsOverride", {"width": 800, "height": 480, "deviceScaleFactor": 1,
                                                        "mobile": False})
        yield cdp
    finally:
        _stop(proc, profile)


def nav(cdp, url):
    cdp.call("Page.navigate", {"url": url})


def wait_js(cdp, expr, timeout=10):
    deadline = time.monotonic() + timeout
    while True:
        try:
            v = evaluate(cdp, expr)
        except (TimeoutError, KeyError):
            v = None
        if v:
            return v
        assert time.monotonic() < deadline, f"timed out waiting for {expr}"
        cdp.pump(0.1)


def href(cdp):
    return evaluate(cdp, "location.href")


def requests(cdp):
    return [e["params"]["request"]["url"] for e in cdp.events if e.get("method") == "Network.requestWillBeSent"]


def shot(cdp, name=None):
    from PIL import Image
    png = base64.b64decode(cdp.call("Page.captureScreenshot", {"format": "png"})["result"]["data"])
    out = os.environ.get("MF_SHOTS")
    if out and name:
        os.makedirs(out, exist_ok=True)
        Path(out, name).write_bytes(png)
    return Image.open(io.BytesIO(png)).convert("RGB")


def lockup_box(img, tol=24):
    """Bounding box of everything that is not ink."""
    w, h = img.size
    px = img.load()
    xs, ys = [], []
    for y in range(0, h):
        for x in range(0, w):
            p = px[x, y]
            if max(abs(p[i] - INK[i]) for i in range(3)) > tol:
                xs.append(x)
                ys.append(y)
    return min(xs), min(ys), max(xs), max(ys)


def dead_url():
    return f"http://127.0.0.1:{free_port()}"


def splash_url(next_, **q):
    extra = "".join(f"&{k}={v}" for k, v in q.items())
    return f"file://{BOOT}/splash.html?next={next_}{extra}"


# ---------------- splash.html ----------------

def test_splash_waits_while_healthz_is_down(bare):
    dead = dead_url()
    nav(bare, splash_url(dead, poll=200))
    bare.pump(2.5)
    assert href(bare).startswith(f"file://{BOOT}/splash.html")
    healthz = [u for u in requests(bare) if u.startswith("http")]
    assert healthz and set(healthz) == {dead + "/healthz"}                          # only the /healthz poll
    assert bare.exceptions() == []


def test_splash_hands_over_when_the_app_answers(bare, server):
    base = server.rstrip("/")
    nav(bare, splash_url(base, poll=200))
    wait_js(bare, f"location.href.startsWith({json.dumps(base + '/?boot=1')})")
    assert "&bp=" in href(bare)
    wait_js(bare, "!!document.getElementById('bootSplash') || window.mfBootGone === true")


@pytest.mark.parametrize("bad", ["http://example.com", "http://localhost.evil:5000", "https://localhost:5000",
                                 "http://127.0.0.1:5000/x", "javascript:alert(1)", "file:///etc/passwd",
                                 "http://user@localhost:5000", ""])
def test_splash_rejects_a_non_local_next(bare, bad):
    from urllib.parse import quote
    nav(bare, f"file://{BOOT}/splash.html?next={quote(bad, safe='')}&poll=100&e2=300")
    bare.pump(1.5)
    assert evaluate(bare, "window.mfSplash.next") is None
    assert href(bare).startswith(f"file://{BOOT}/splash.html")                        # no E2 jump with a bad next
    assert [u for u in requests(bare) if not u.startswith(("file:", "data:"))] == []


def test_splash_shows_e2_when_the_app_never_answers(bare):
    dead = dead_url()
    nav(bare, splash_url(dead, poll=100, e2=800))
    wait_js(bare, "location.href.includes('/error.html?e=E2')")
    assert evaluate(bare, "document.getElementById('code').textContent") == "Error E2"
    assert evaluate(bare, "window.mfError.next") == dead


def test_splash_late_line_and_breath(bare):
    nav(bare, splash_url(dead_url(), late=300))
    bare.pump(0.2)
    assert float(evaluate(bare, "getComputedStyle(document.querySelector('.late')).opacity")) < 0.05
    wait_js(bare, "parseFloat(getComputedStyle(document.querySelector('.late')).opacity) > 0.99", timeout=5)
    assert evaluate(bare, "document.querySelector('.late').textContent") == "Getting your photos ready…"
    a = json.loads(evaluate(bare, """JSON.stringify(document.getAnimations().filter(a=>a.animationName==='breathe')
        .map(a=>({d:a.effect.getTiming().duration, delay:a.effect.getTiming().delay, it:a.effect.getTiming().iterations})))"""))
    assert a == [{"d": 2800, "delay": 0, "it": None}] or a == [{"d": 2800, "delay": 0, "it": "Infinity"}]   # Wren: 2.8 s, no delay
    assert evaluate(bare, "getComputedStyle(document.body).cursor") == "none"
    assert evaluate(bare, "document.querySelectorAll('button,a,input').length") == 0


def test_splash_matches_wrens_png(bare):
    """Static frame (reduced motion = the breath's brightest point): same ink, lockup centre within 2 px."""
    from PIL import Image
    bare.call("Emulation.setEmulatedMedia", {"features": [{"name": "prefers-reduced-motion", "value": "reduce"}]})
    nav(bare, splash_url(dead_url()))
    wait_js(bare, "document.fonts.status === 'loaded'")
    bare.pump(0.5)
    got = shot(bare, "splash-html.png")
    want = Image.open(WREN_A).convert("RGB")
    assert got.size == want.size == (800, 480)
    for xy in ((0, 0), (799, 0), (0, 479), (799, 479), (400, 20), (20, 240)):
        assert got.getpixel(xy) == INK == want.getpixel(xy)
    gb, wb = lockup_box(got), lockup_box(want)
    gc = ((gb[0] + gb[2]) / 2, (gb[1] + gb[3]) / 2)
    wc = ((wb[0] + wb[2]) / 2, (wb[1] + wb[3]) / 2)
    assert abs(gc[0] - wc[0]) <= 2 and abs(gc[1] - wc[1]) <= 2, (gb, wb)
    assert abs(gc[0] - 400) <= 2 and abs(gc[1] - 240) <= 2, gb
    assert all(abs(g - w) <= 3 for g, w in zip(gb, wb)), (gb, wb)                    # same size too


# ---------------- the app's #bootSplash overlay ----------------

def test_overlay_only_with_boot_1(page, server):
    page.pump(1)
    assert evaluate(page, "document.getElementById('bootSplash')") is None
    nav(page, server + "?boot=1&bootfs=10000")
    wait_js(page, "document.readyState === 'complete'")
    assert evaluate(page, "!!document.getElementById('bootSplash') || window.mfBootGone === true")


def test_overlay_fades_after_the_first_photo(page, server):
    nav(page, server + "?boot=1&bootfs=10000&bp=900")
    wait_js(page, "window.mfBootWhy !== undefined")
    assert evaluate(page, "window.mfBootWhy") == "image"
    assert evaluate(page, "document.getElementById('image').classList.contains('visible')")
    wait_js(page, "window.mfBootGone === true", timeout=3)
    assert evaluate(page, "document.getElementById('bootSplash')") is None
    assert page.exceptions() == [] and csp_violations(page) == []


@pytest.mark.no_media
def test_overlay_fades_after_the_pairing_or_empty_screen(page, server):
    nav(page, server + "?boot=1&bootfs=10000")
    wait_js(page, "window.mfBootWhy !== undefined")
    assert evaluate(page, "window.mfBootWhy") in ("pairOverlay", "emptyOverlay")
    wait_js(page, "window.mfBootGone === true", timeout=3)
    assert page.exceptions() == [] and csp_violations(page) == []


def test_overlay_failsafe_when_nothing_paints(page, server):
    page.call("Network.enable")
    page.call("Network.setBlockedURLs", {"urls": ["*/uploads/*"]})                   # the photo never loads
    nav(page, server + "?boot=1&bootfs=800")
    t0 = time.monotonic()
    wait_js(page, "window.mfBootWhy !== undefined")
    assert evaluate(page, "window.mfBootWhy") == "failsafe" and time.monotonic() - t0 < 5
    wait_js(page, "window.mfBootGone === true", timeout=3)


def test_overlay_fade_is_600_ms_and_matches_the_splash_lockup(page, server):
    page.call("Network.enable")
    page.call("Network.setBlockedURLs", {"urls": ["*/uploads/*"]})
    page.call("Emulation.setEmulatedMedia", {"features": [{"name": "prefers-reduced-motion", "value": "reduce"}]})
    nav(page, server + "?boot=1&bootfs=10000")
    wait_js(page, "document.fonts.status === 'loaded' && !!document.getElementById('bootSplash')")
    page.pump(0.5)
    t = json.loads(evaluate(page, """JSON.stringify((()=>{const s=getComputedStyle(document.getElementById('bootSplash'));
        return {d:s.transitionDuration, p:s.transitionProperty, z:+s.zIndex, bg:s.backgroundColor, c:s.cursor};})())"""))
    assert t == {"d": "0.6s", "p": "opacity", "z": 20000, "bg": "rgb(14, 19, 17)", "c": "none"}
    from PIL import Image
    got = shot(page, "boot-overlay.png")
    want = Image.open(WREN_A).convert("RGB")
    gb, wb = lockup_box(got), lockup_box(want)
    assert all(abs(g - w) <= 3 for g, w in zip(gb, wb)), (gb, wb)


def wordmark_diff(got):
    """Max per-channel difference between the 282x35 crop of a 800x480 screenshot at (259,289) and wordmark.png on ink."""
    from PIL import Image
    wm = Image.open(ROOT / "static" / "boot" / "wordmark.png").convert("RGBA")
    want = Image.new("RGBA", wm.size, INK + (255,))
    want.alpha_composite(wm)
    crop = got.crop((259, 289, 259 + 282, 289 + 35)).convert("RGBA")
    return max(abs(a - b) for a, b in zip(crop.tobytes(), want.tobytes()))


def test_splash_wordmark_is_wrens_png_1_to_1_at_259_289(bare):
    bare.call("Emulation.setEmulatedMedia", {"features": [{"name": "prefers-reduced-motion", "value": "reduce"}]})
    nav(bare, splash_url(dead_url()))
    wait_js(bare, "document.querySelector('img.word').complete && document.querySelector('img.word').naturalWidth === 282")
    bare.pump(0.3)
    assert json.loads(evaluate(bare, "JSON.stringify(((r)=>[r.x,r.y,r.width,r.height])(document.querySelector('img.word').getBoundingClientRect()))")) == [259, 289, 282, 35]
    assert wordmark_diff(shot(bare)) <= 4


def test_overlay_wordmark_is_wrens_png_1_to_1_at_259_289(page, server):
    page.call("Network.enable")
    page.call("Network.setBlockedURLs", {"urls": ["*/uploads/*"]})
    page.call("Emulation.setEmulatedMedia", {"features": [{"name": "prefers-reduced-motion", "value": "reduce"}]})
    nav(page, server + "?boot=1&bootfs=10000")
    wait_js(page, "!!document.querySelector('#bootSplash img.bsWord') && document.querySelector('#bootSplash img.bsWord').naturalWidth === 282")
    page.pump(0.5)
    assert json.loads(evaluate(page, "JSON.stringify(((r)=>[r.x,r.y,r.width,r.height])(document.querySelector('#bootSplash img.bsWord').getBoundingClientRect()))")) == [259, 289, 282, 35]
    assert wordmark_diff(shot(page)) <= 4


def test_csp_header_unchanged_with_boot_1(server):
    from memoryframe import websec
    for path in ("", "?boot=1"):
        r = urllib.request.urlopen(server + path)
        assert r.headers["Content-Security-Policy"] == websec.CSP
        body = r.read().decode()
        assert "<style" not in body and "style=" not in body.replace("mf-frame-token", "")
        assert ("bootSplash" in body) == (path == "?boot=1")


def test_hold_menu_opens_after_the_fade(page, server):
    nav(page, server + "?boot=1&bootfs=10000")
    wait_js(page, "window.mfBootGone === true")
    hold(page, 3.4)
    page.pump(0.3)
    assert evaluate(page, "document.getElementById('toolsOverlay').classList.contains('visible')")
