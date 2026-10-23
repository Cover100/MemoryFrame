"""#80 phone Library in headless Chrome: select mode, Select all, confirm sheet, bulk delete, Back, long press."""
import json

import pytest

from memoryframe import db as mfdb
from test_frontend_browser import BROWSER, evaluate  # noqa: F401
from test_restyle_c_phone import PNG, _phone, page, server  # noqa: F401  (fixtures)

pytestmark = pytest.mark.skipif(BROWSER is None, reason="no Chromium/Chrome installed")

def js(page, expr):
    return evaluate(page, expr)


def state(page):
    return json.loads(js(page, """JSON.stringify({
      selecting: document.body.classList.contains('selecting'),
      pressed: [...document.querySelectorAll('.lib .tile')].map(t => t.getAttribute('aria-pressed')),
      count: document.getElementById('selCount').textContent,
      del: document.getElementById('delBtn').textContent, delOff: document.getElementById('delBtn').disabled,
      all: document.getElementById('selAll').textContent,
      sheet: !document.getElementById('confirm').hidden,
      err: !document.getElementById('delErr').hidden,
      tiles: document.querySelectorAll('.lib .tile').length,
      head: document.getElementById('count').textContent,
      focus: document.activeElement.id})"""))


def click(page, expr):
    js(page, f"{expr}.click()")
    page.pump(0.2)


def fresh(app):
    """The test app seeds sample photos; start from an empty library so counts are exact."""
    conn = mfdb.connect(app.config["DB_PATH"])
    conn.execute("DELETE FROM media")
    conn.commit()
    conn.close()


def rows(app):
    conn = mfdb.connect(app.config["DB_PATH"])
    try:
        return conn.execute("SELECT COUNT(*) FROM media").fetchone()[0]
    finally:
        conn.close()


def test_library_select_delete_flow(page, server, mf_app, lib):
    fresh(mf_app)
    for i in range(5):
        lib.add(f"pic{i}.jpg", PNG + bytes([i]))
    _phone(page, server, mf_app, "admin/library")
    page.pump(1)
    T = "document.querySelectorAll('.lib .tile')"
    # normal mode: plain grid, no aria-pressed, selection chrome hidden, 56 px Upload, 48 px Select
    s = state(page)
    assert s["tiles"] == 5 and not s["selecting"] and set(s["pressed"]) == {None} and s["head"] == "5 photos on the frame"
    assert js(page, "getComputedStyle(document.getElementById('toolbar')).display") == "none"
    assert js(page, "getComputedStyle(document.getElementById('selbar')).display") == "none"
    assert js(page, "document.querySelector('a.btn[href=\"/admin/upload\"]').getBoundingClientRect().height") >= 56
    assert js(page, "document.getElementById('selectBtn').getBoundingClientRect().height") >= 48
    # one tap = select mode with that photo selected (no preview, no navigation)
    click(page, f"{T}[0]")
    s = state(page)
    assert s["selecting"] and s["pressed"] == ["true", "false", "false", "false", "false"]
    assert s["count"] == "1 selected" and s["del"] == "Delete (1)" and not s["delOff"]
    assert js(page, "location.pathname") == "/admin/library"
    # toggle on and off; selecting the last one off keeps the mode with Delete disabled
    click(page, f"{T}[1]")
    click(page, f"{T}[1]")
    click(page, f"{T}[0]")
    s = state(page)
    assert s["selecting"] and s["count"] == "0 selected" and s["del"] == "Delete (0)" and s["delOff"]
    click(page, "document.getElementById('delBtn')")                    # disabled: nothing opens
    assert not state(page)["sheet"]
    # Select all / Unselect all
    click(page, "document.getElementById('selAll')")
    s = state(page)
    assert s["pressed"] == ["true"] * 5 and s["count"] == "5 selected" and s["all"] == "Unselect all"
    click(page, "document.getElementById('selAll')")
    assert state(page)["pressed"] == ["false"] * 5
    # confirm sheet: right wording, Cancel focused, page behind inert, Esc cancels and keeps the selection
    click(page, f"{T}[2]")
    click(page, f"{T}[3]")
    click(page, "document.getElementById('delBtn')")
    s = state(page)
    assert s["sheet"] and s["focus"] == "confirmCancel"
    assert js(page, "document.getElementById('confirmTitle').textContent") == "Delete 2 photos?"
    assert js(page, "document.getElementById('grid').inert && document.getElementById('toolbar').inert")
    assert js(page, "document.getElementById('confirm').querySelector('[role=alertdialog]').getAttribute('aria-modal')") == "true"
    js(page, "document.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape', bubbles: true}))")
    page.pump(0.3)
    s = state(page)
    assert not s["sheet"] and s["focus"] == "delBtn" and s["pressed"].count("true") == 2
    assert rows(mf_app) == 5
    # a failed request keeps the selection and shows the error
    js(page, "window.__f = window.fetch; window.fetch = () => Promise.resolve(new Response('{}', {status: 500}))")
    click(page, "document.getElementById('delBtn')")
    click(page, "document.getElementById('confirmDelete')")
    page.pump(0.4)
    s = state(page)
    assert s["err"] and s["selecting"] and s["tiles"] == 5 and s["pressed"].count("true") == 2 and rows(mf_app) == 5
    # the error sits in the fixed toolbar (on screen wherever the list is scrolled); focus is back on Delete
    assert js(page, "(()=>{const r=document.getElementById('delErr').getBoundingClientRect();return r.top>=0&&r.bottom<=innerHeight&&r.height>0})()")
    assert js(page, "document.getElementById('delErr').closest('#toolbar') !== null")
    assert s["focus"] == "delBtn" and not s["delOff"]
    # a network failure (fetch rejects with TypeError) shows "Can't reach the frame"; the selection stays
    js(page, "window.fetch = () => Promise.reject(new TypeError('Failed to fetch'))")
    click(page, "document.getElementById('delBtn')")
    click(page, "document.getElementById('confirmDelete')")
    page.pump(0.4)
    assert js(page, "!document.getElementById('offline').hidden && document.getElementById('page').hidden")
    assert rows(mf_app) == 5
    js(page, "window.fetch = window.__f")
    click(page, "document.getElementById('offlineRetry')")        # Try again pings /healthz and restores the page
    page.pump(0.5)
    s = state(page)
    assert js(page, "document.getElementById('offline').hidden") and s["selecting"] and s["pressed"].count("true") == 2
    # success: one request, tiles go, mode exits, header count follows
    click(page, "document.getElementById('delBtn')")
    assert js(page, "document.getElementById('confirmTitle').textContent") == "Delete 2 photos?"
    click(page, "document.getElementById('confirmDelete')")
    page.pump(0.8)
    s = state(page)
    assert s["tiles"] == 3 and not s["selecting"] and not s["err"] and s["head"] == "3 photos on the frame"
    assert js(page, "[...document.querySelectorAll('.lib .tile')].map(t => t.getAttribute('aria-label')).join('|')") \
        == "Photo 1 of 3|Photo 2 of 3|Photo 3 of 3"                  # labels renumbered after a delete
    assert set(s["pressed"]) == {None} and rows(mf_app) == 3
    # Select (nothing selected) then Cancel; singular wording for one photo
    click(page, "document.getElementById('selectBtn')")
    s = state(page)
    assert s["selecting"] and s["pressed"] == ["false"] * 3 and s["focus"] == "selAll"
    click(page, f"{T}[0]")
    click(page, "document.getElementById('delBtn')")
    assert js(page, "document.getElementById('confirmTitle').textContent") == "Delete this photo?"
    click(page, "document.getElementById('confirmCancel')")
    click(page, "document.getElementById('selCancel')")
    s = state(page)
    assert not s["selecting"] and set(s["pressed"]) == {None} and rows(mf_app) == 3
    # delete everything: the empty state shows straight away; while the request runs Delete stays disabled
    click(page, "document.getElementById('selectBtn')")
    click(page, "document.getElementById('selAll')")
    click(page, "document.getElementById('delBtn')")
    js(page, "window.__f2 = window.fetch; window.fetch = (...a) => new Promise(r => setTimeout(() => r(window.__f2(...a)), 400))")
    click(page, "document.getElementById('confirmDelete')")
    t = js(page, "document.querySelector('.lib .tile').click(); document.getElementById('delBtn').disabled")
    assert t is True                                                  # a tap during the request can't re-enable Delete
    page.pump(1.2)
    assert rows(mf_app) == 0
    assert js(page, "!document.getElementById('emptyLib').hidden && document.getElementById('grid').hidden")
    assert js(page, "document.getElementById('count').textContent") == "0 photos on the frame"
    assert page.exceptions() == []


def test_library_back_and_long_press(page, server, mf_app, lib):
    fresh(mf_app)
    for i in range(3):
        lib.add(f"pic{i}.jpg", PNG + bytes([i]))
    _phone(page, server, mf_app, "admin/library")
    page.pump(1)
    T = "document.querySelectorAll('.lib .tile')"
    ev = lambda t, x=5, y=5: js(page, f"{T}[1].dispatchEvent(new PointerEvent('{t}', {{bubbles: true, clientX: {x}, clientY: {y}, button: 0}}))")
    # a long press (500 ms) selects that photo; the click that follows the release does not undo it
    ev("pointerdown")
    page.pump(0.7)
    ev("pointerup")
    js(page, f"{T}[1].click()")
    page.pump(0.2)
    s = state(page)
    assert s["selecting"] and s["pressed"] == ["false", "true", "false"]
    click(page, "document.getElementById('selCancel')")
    # moving more than 10 px first is a scroll: no selection
    ev("pointerdown")
    ev("pointermove", 5, 40)
    page.pump(0.7)
    assert not state(page)["selecting"]
    ev("pointerup")
    # Back (Android) leaves selection mode and stays on the page; Back again closes the sheet first
    click(page, f"{T}[0]")
    assert state(page)["selecting"]
    js(page, "history.back()")
    page.pump(0.4)
    s = state(page)
    assert not s["selecting"] and js(page, "location.pathname") == "/admin/library"
    click(page, f"{T}[0]")
    click(page, "document.getElementById('delBtn')")
    js(page, "history.back()")
    page.pump(0.4)
    s = state(page)
    assert not s["sheet"] and s["selecting"] and s["pressed"].count("true") == 1
    assert page.exceptions() == []


def test_library_plain_tile_for_unreadable_photo(page, server, mf_app, lib):
    fresh(mf_app)
    lib.add("broken.jpg", b"not really a jpeg")
    lib.add("ok.jpg", PNG)
    _phone(page, server, mf_app, "admin/library")
    page.pump(1.5)
    assert js(page, "document.querySelectorAll('.lib .tile.plain').length") == 1
    assert js(page, "document.querySelectorAll('.lib .tile img').length") == 1
    assert js(page, "document.querySelectorAll('.lib .tile.plain .ph').length") == 1


@pytest.mark.parametrize("w,zoom", [(412, 1.0), (360, 1.3)])
def test_library_fits_and_is_3_columns(page, server, mf_app, lib, w, zoom):
    fresh(mf_app)
    for i in range(7):
        lib.add(f"pic{i}.jpg", PNG + bytes([i]))
    _phone(page, server, mf_app, "admin/library")
    page.call("Emulation.setDeviceMetricsOverride", {"width": w, "height": 800, "deviceScaleFactor": 1, "mobile": True})
    js(page, f"document.documentElement.style.zoom = '{zoom}'")
    page.pump(0.8)
    assert js(page, "document.documentElement.scrollWidth <= document.documentElement.clientWidth")
    cols = json.loads(js(page, """JSON.stringify([...new Set([...document.querySelectorAll('.lib li')]
        .map(l => Math.round(l.getBoundingClientRect().left)))].length)"""))
    assert cols == 3
    sq = json.loads(js(page, """JSON.stringify([...document.querySelectorAll('.lib li')].map(l => {
        const r = l.getBoundingClientRect(); return Math.abs(r.width - r.height) < 1.5 && r.width >= 48;}))"""))
    assert all(sq)
    js(page, "document.getElementById('selectBtn').click()")
    js(page, "document.querySelectorAll('.lib .tile')[0].click()")
    page.pump(0.3)
    # the bottom toolbar stays compact and the Delete label is not clipped
    assert js(page, "document.getElementById('toolbar').getBoundingClientRect().height") < 100 * zoom
    assert js(page, "(()=>{const b=document.getElementById('delBtn'); return b.scrollWidth <= b.clientWidth + 1;})()")
    assert page.exceptions() == []


@pytest.mark.parametrize("w,zoom", [(412, 1.0), (360, 1.3)])
def test_failed_delete_error_is_visible_when_scrolled_down(page, server, mf_app, lib, w, zoom):
    """Wren M1: the error lives in the fixed toolbar, so it is on screen however far the list is scrolled, and the
    last row still clears the (taller) toolbar. MF_SHOTS=<dir> also saves a screenshot."""
    import base64
    import os
    fresh(mf_app)
    for i in range(40):
        lib.add(f"pic{i}.jpg", PNG + bytes([i]))
    _phone(page, server, mf_app, "admin/library")
    page.call("Emulation.setDeviceMetricsOverride", {"width": w, "height": 800, "deviceScaleFactor": 1, "mobile": True})
    js(page, f"document.documentElement.style.zoom = '{zoom}'")
    page.pump(0.5)
    click(page, "document.getElementById('selectBtn')")
    js(page, "window.scrollTo(0, document.documentElement.scrollHeight)")
    page.pump(0.3)
    js(page, "window.fetch = () => Promise.resolve(new Response('{}', {status: 500}))")
    click(page, "document.querySelectorAll('.lib .tile')[39]")
    click(page, "document.getElementById('delBtn')")
    click(page, "document.getElementById('confirmDelete')")
    page.pump(0.5)
    js(page, "window.scrollTo(0, document.documentElement.scrollHeight)")   # the taller toolbar adds padding: scroll to the end
    page.pump(0.3)
    r = json.loads(js(page, """(()=>{const e=document.getElementById('delErr').getBoundingClientRect(),
      t=document.getElementById('toolbar').getBoundingClientRect(),
      l=document.querySelectorAll('.lib .tile')[39].getBoundingClientRect(), z=parseFloat(document.documentElement.style.zoom)||1;
      return JSON.stringify({errTop:e.top, errBottom:e.bottom, vh:innerHeight, tbTop:t.top, lastBottom:l.bottom,
        scrolled: scrollY > 100, focus: document.activeElement.id})})()"""))
    assert r["scrolled"] and r["focus"] == "delBtn"
    assert r["errTop"] >= r["tbTop"] - 0.5 and 0 <= r["errTop"] and r["errBottom"] <= r["vh"] + 0.5, r   # inside the toolbar, on screen
    assert r["lastBottom"] <= r["tbTop"] + 0.5, r                    # last photo not hidden under the error + buttons
    out = os.environ.get("MF_SHOTS")
    if out:
        os.makedirs(out, exist_ok=True)
        png = page.call("Page.captureScreenshot", {"format": "png"})["result"]["data"]
        with open(os.path.join(out, f"{w}{'-130' if zoom != 1 else ''}-failed-delete-scrolled.png"), "wb") as f:
            f.write(base64.b64decode(png))
    assert page.exceptions() == []
