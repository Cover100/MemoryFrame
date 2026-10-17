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
    """A finger tap (pointerType "touch")."""
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


def dev_confirm_open(page):
    return evaluate(page, "document.getElementById('devModeOverlay').classList.contains('visible')")


def test_three_second_hold_opens_dev_confirm_not_menu(ready):   # Tester #52: only double tap opens the menu
    hold(ready, 3.5)
    ready.pump(0.5)
    assert dev_confirm_open(ready) and not menu_open(ready)


def test_brightness_drag_never_opens_dev_confirm(ready, backlight):
    press(ready, 400, 300)
    ready.pump(1.3)
    for i in range(30):                        # slow vertical drag, longer than 3 s in total
        move(ready, 400, 300 - i * 5)
        time.sleep(0.08)
    ready.pump(1.0)
    release(ready, 400, 150)
    ready.pump(0.5)
    assert not dev_confirm_open(ready) and not menu_open(ready)
    assert (backlight / "brightness").read_text().strip() != "100"


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



def test_mouse_double_tap_opens_menu(ready):
    """On-device: the labwc/Wayland panel reports taps as pointerType "mouse"."""
    click(ready)
    time.sleep(0.1)
    click(ready, 405, 245)
    ready.pump(0.5)
    assert menu_open(ready) and not dev_confirm_open(ready)


def test_brightness_starts_immediately_without_hold(ready, backlight):
    press(ready, 400, 300)                     # no hold: move right away
    for i in range(1, 30):
        move(ready, 400, 300 - i * 5)
        time.sleep(0.02)
    release(ready, 400, 155)
    ready.pump(1.0)
    assert (backlight / "brightness").read_text().strip() != "100"
    assert not menu_open(ready) and not dev_confirm_open(ready)
    assert ready.exceptions() == []


def test_small_vertical_wobble_is_not_brightness(ready, backlight):
    press(ready, 400, 300)
    move(ready, 400, 292)                      # 8 px < 12 px
    release(ready, 400, 292)
    ready.pump(0.8)
    assert (backlight / "brightness").read_text().strip() == "100"


def test_horizontal_swipe_never_changes_brightness(ready, backlight):
    press(ready, 500, 240)
    for i in range(1, 20):
        move(ready, 500 - i * 15, 240 - i)     # mostly horizontal, some vertical drift
        time.sleep(0.02)
    release(ready, 215, 221)
    ready.pump(0.8)
    assert (backlight / "brightness").read_text().strip() == "100"
    assert not menu_open(ready) and not dev_confirm_open(ready)


def test_brightness_drag_then_still_hold_never_opens_dev_confirm(ready, backlight):
    press(ready, 400, 300)
    for i in range(1, 21):                     # slow 80 px drag (past the 72 px start)
        move(ready, 400, 300 - i * 4)
        time.sleep(0.05)
    ready.pump(3.5)                            # then rests past 3 s
    release(ready, 400, 220)
    ready.pump(0.5)
    assert not dev_confirm_open(ready) and not menu_open(ready)


def test_slow_drag_past_hold_tolerance_below_start_never_opens_dev(ready, backlight):
    press(ready, 400, 300)
    for i in range(1, 11):                     # 40 px: cancels the hold (20 px), no brightness (72 px)
        move(ready, 400, 300 - i * 4)
        time.sleep(0.1)
    ready.pump(3.5)
    release(ready, 400, 260)
    ready.pump(0.5)
    assert not dev_confirm_open(ready) and bright(backlight) == "100"


def test_50px_move_leaves_brightness(ready, backlight):
    press(ready, 400, 300)
    for i in range(1, 11):
        move(ready, 400, 300 - i * 5)
        time.sleep(0.02)
    release(ready, 400, 250)
    ready.pump(0.8)
    assert bright(backlight) == "100"


def test_80px_move_changes_brightness(ready, backlight):
    press(ready, 400, 300)
    for i in range(1, 17):
        move(ready, 400, 300 - i * 5)
        time.sleep(0.02)
    release(ready, 400, 220)
    ready.pump(0.8)
    assert bright(backlight) != "100"


def bright(backlight):
    return (backlight / "brightness").read_text().strip()


def tap_el(page, el_id, kind="touch"):
    r = evaluate(page, f"(()=>{{const b=document.getElementById('{el_id}').getBoundingClientRect();"
                       "return [b.left+b.width/2,b.top+b.height/2];})()")
    (tap if kind == "touch" else click)(page, r[0], r[1])
    page.pump(0.4)


@pytest.mark.parametrize("kind", ["touch", "mouse"])
def test_menu_and_dialog_taps_never_change_brightness(ready, backlight, mf_app, kind):
    mf_app.config["POWEROFF_CMD"] = ["true"]
    before = bright(backlight)
    tap(ready, 400, 400); time.sleep(0.1); tap(ready, 400, 400)      # double tap low on the screen
    ready.pump(0.5)
    assert menu_open(ready)
    tap_el(ready, "menuShutdown", kind)                              # button far above the tap point
    assert evaluate(ready, "document.getElementById('shutdownOverlay').classList.contains('visible')")
    tap_el(ready, "shutdownCancel", kind)
    evaluate(ready, "memoryFramePairing.openMenu()"); ready.pump(0.3)
    tap_el(ready, "menuSignOut", kind)
    tap_el(ready, "confirmCancel", kind)
    evaluate(ready, "memoryFramePairing.openMenu()"); ready.pump(0.3)
    tap_el(ready, "menuClose", kind)
    ready.pump(0.8)
    assert bright(backlight) == before


def test_drag_inside_open_menu_never_changes_brightness(ready, backlight):
    before = bright(backlight)
    evaluate(ready, "memoryFramePairing.openMenu()"); ready.pump(0.3)
    press(ready, 400, 350)
    for i in range(1, 25):
        move(ready, 400, 350 - i * 8)
        time.sleep(0.02)
    release(ready, 400, 158)
    ready.pump(0.8)
    assert bright(backlight) == before


def test_slideshow_drag_still_changes_brightness(ready, backlight):
    before = bright(backlight)
    press(ready, 400, 300)
    for i in range(1, 30):
        move(ready, 400, 300 - i * 5)
        time.sleep(0.02)
    release(ready, 400, 155)
    ready.pump(1.0)
    assert bright(backlight) != before
