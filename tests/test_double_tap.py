"""MF-34: double tap opens the menu (headless Chrome, real pointer events)."""
import time

import pytest

from test_frontend_browser import evaluate, hold, net, page, server, text  # noqa: F401  (fixtures)


def press(page, x, y):
    page.call("Input.dispatchMouseEvent", {"type": "mousePressed", "x": x, "y": y, "button": "left",
                                           "buttons": 1, "clickCount": 1})


def release(page, x, y):
    page.call("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": x, "y": y, "button": "left",
                                           "clickCount": 1})


def move(page, x, y):
    page.call("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": x, "y": y, "button": "left", "buttons": 1})


def tap(page, x=400, y=240, down=0.05):
    """A finger tap (pointerType "touch"): only touch double taps open the menu."""
    page.call("Input.dispatchTouchEvent", {"type": "touchStart", "touchPoints": [{"x": x, "y": y}]})
    time.sleep(down)
    page.call("Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []})


def click(page, x=400, y=240, down=0.05):
    press(page, x, y)
    time.sleep(down)
    release(page, x, y)


def menu_open(page):
    return evaluate(page, "document.getElementById('menuOverlay').classList.contains('visible')")


def close_menu(page):
    evaluate(page, "document.getElementById('menuClose').click()")
    page.pump(0.3)


@pytest.fixture
def ready(page, server, mf_app, net):
    """Slideshow running, a phone paired (no onboarding overlay)."""
    from memoryframe import auth, db as mfdb
    conn = mfdb.connect(mf_app.config["DB_PATH"])
    auth.create_session(conn, "phone")
    conn.close()
    page.pump(2.5)
    assert not evaluate(page, "document.getElementById('pairOverlay').classList.contains('visible')")
    return page


def test_double_tap_opens_menu(ready):
    tap(ready)
    time.sleep(0.1)
    tap(ready, 410, 250)                       # within 40 px
    ready.pump(0.5)
    assert menu_open(ready)


@pytest.mark.parametrize("gap", [0.5, 0.8])
def test_slow_taps_do_nothing(ready, gap):
    tap(ready)
    time.sleep(gap)
    tap(ready)
    ready.pump(0.5)
    assert not menu_open(ready)


def test_single_tap_does_nothing(ready):
    tap(ready)
    ready.pump(0.6)
    assert not menu_open(ready)


def test_far_apart_taps_do_nothing(ready):
    tap(ready, 200, 240)
    time.sleep(0.1)
    tap(ready, 300, 240)                       # 100 px away
    ready.pump(0.5)
    assert not menu_open(ready)


def test_long_press_is_not_a_tap(ready):
    tap(ready, down=0.4)                       # > 250 ms
    time.sleep(0.05)
    tap(ready)
    ready.pump(0.5)
    assert not menu_open(ready)


def test_swipe_then_tap_does_not_open(ready):
    before = evaluate(ready, "document.querySelector('.slide.active, .active, img') ? 1 : 0")
    press(ready, 600, 240)
    for x in range(600, 380, -20):
        move(ready, x, 240)
        time.sleep(0.005)
    release(ready, 380, 240)
    time.sleep(0.1)
    tap(ready, 380, 240)
    ready.pump(0.5)
    assert not menu_open(ready) and before == 1
    assert ready.exceptions() == []


def test_brightness_drag_never_opens_menu(ready, backlight):
    press(ready, 400, 300)
    ready.pump(1.3)
    for i in range(30):
        move(ready, 400, 300 - i * 5)
        time.sleep(0.02)
    release(ready, 400, 150)
    time.sleep(0.1)
    tap(ready, 400, 150)
    ready.pump(1)
    assert not menu_open(ready)
    assert (backlight / "brightness").read_text().strip() != "100"


def test_three_second_hold_still_opens(ready):
    hold(ready, 3.5)
    ready.pump(0.5)
    assert menu_open(ready)


def test_double_tap_while_menu_open_does_not_stack(ready):
    tap(ready)
    time.sleep(0.1)
    tap(ready)
    ready.pump(0.5)
    assert menu_open(ready)
    calls = evaluate(ready, "(()=>{window.__opens=0;const m=document.getElementById('menuOverlay');"
                            "new MutationObserver(()=>window.__opens++).observe(m,{attributes:true});return 0;})()")
    r = evaluate(ready, "(()=>{const b=document.querySelector('#menuBox .menuTitle').getBoundingClientRect();"
                        "return [b.left+b.width/2,b.top+b.height/2];})()")
    tap(ready, r[0], r[1])                     # inside the menu card (not a button)
    time.sleep(0.1)
    tap(ready, r[0], r[1])
    ready.pump(0.5)
    assert evaluate(ready, "window.__opens") == 0 and calls == 0


def test_hint_text(page, server, mf_app, net):
    page.pump(2.5)
    if evaluate(page, "document.getElementById('pairHint').classList.contains('visible')"):
        assert text(page, "pairHint") == "Double-tap for menu to pair a phone"
    html = evaluate(page, "document.body.innerHTML")
    assert "Hold 3 s" not in html



def test_mouse_double_click_does_not_open_menu(ready):
    click(ready)
    time.sleep(0.1)
    click(ready)
    ready.pump(0.5)
    assert not menu_open(ready)
    tap(ready)                                    # a touch double tap still works
    time.sleep(0.1)
    tap(ready)
    ready.pump(0.5)
    assert menu_open(ready)
