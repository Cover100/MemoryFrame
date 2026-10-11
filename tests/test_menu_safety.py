"""MF-31 confirm sign-out, MF-32 overlay close rules, MF-33 paired success."""
import html as htmlmod
import time

import pytest

from memoryframe import auth, db as mfdb
from test_double_tap import menu_open, press, release, tap  # noqa: F401
from test_frontend_browser import evaluate, net, page, server, text  # noqa: F401  (fixtures)

PHONE = {"REMOTE_ADDR": "10.42.0.23"}
CONFIRM_BODY = "Every paired phone will need to pair again before it can upload. Photos stay on the frame."


def visible(page, el):
    return evaluate(page, f"document.getElementById('{el}').classList.contains('visible')")


def paired_count(app):
    conn = mfdb.connect(app.config["DB_PATH"])
    try:
        return auth.paired_count(conn)
    finally:
        conn.close()


def add_phone(app):
    conn = mfdb.connect(app.config["DB_PATH"])
    auth.create_session(conn, "phone")
    conn.close()


@pytest.fixture
def ready(page, server, mf_app, net):
    add_phone(mf_app)
    page.pump(2.5)
    return page


def outside_tap(page, x=20, y=20):
    tap(page, x, y)
    page.pump(0.4)


def open_confirm(page):
    evaluate(page, "memoryFramePairing.openMenu()")
    page.pump(0.3)
    evaluate(page, "document.getElementById('menuSignOut').click()")
    page.pump(0.3)


# ---------- MF-31 ----------

def test_confirm_copy_and_size(ready):
    open_confirm(ready)
    assert visible(ready, "confirmOverlay") and not menu_open(ready)
    assert "Sign out all phones?" in text(ready, "confirmBox") and CONFIRM_BODY in text(ready, "confirmBox")
    box = evaluate(ready, "(()=>{const r=document.getElementById('confirmBox').getBoundingClientRect();return [r.width,r.height];})()")
    assert [round(v) for v in box] == [480, 260]
    for b in ("confirmCancel", "confirmSignOut"):
        r = evaluate(ready, f"(()=>{{const r=document.getElementById('{b}').getBoundingClientRect();return [r.width,r.height];}})()")
        assert round(r[0]) >= 200 and round(r[1]) >= 64
    assert evaluate(ready, "getComputedStyle(document.getElementById('confirmSignOut')).backgroundColor") == "rgb(198, 40, 40)"


@pytest.mark.parametrize("how", ["cancel", "outside", "idle"])
def test_confirm_cancel_paths_keep_phones(ready, mf_app, how):
    open_confirm(ready)
    if how == "cancel":
        evaluate(ready, "document.getElementById('confirmCancel').click()")
    elif how == "outside":
        outside_tap(ready)
    else:
        evaluate(ready, "memoryFramePairing.setIdleCloseForTests(1500)")
        ready.pump(3)
    ready.pump(0.5)
    assert not visible(ready, "confirmOverlay") and paired_count(mf_app) == 1


def test_tap_inside_confirm_does_not_close(ready):
    open_confirm(ready)
    tap(ready, 400, 200)                                 # on the card text
    ready.pump(0.4)
    assert visible(ready, "confirmOverlay")


def test_sign_out_then_done_message(ready, mf_app):
    open_confirm(ready)
    evaluate(ready, "document.getElementById('confirmSignOut').click()")
    ready.pump(0.6)
    assert text(ready, "confirmDone") == "All phones signed out"
    assert evaluate(ready, "!document.getElementById('confirmDone').hidden")
    assert paired_count(mf_app) == 0
    ready.pump(2.2)
    assert not visible(ready, "confirmOverlay")


def test_phone_sign_out_asks_first(paired):
    html = paired.get("/admin/", environ_base=PHONE).get_data(as_text=True)
    assert 'id="signOutForm"' in html and "/static/admin.js" in html
    from pathlib import Path
    js = (Path(__file__).resolve().parents[1] / "static" / "admin.js").read_text()
    assert ("Sign out all phones? Every paired phone, including this one, will need to pair again. "
            "Photos stay on the frame.") in js and "confirm(" in js


# ---------- MF-32 ----------

def test_menu_closes_on_outside_tap_not_inside(ready):
    evaluate(ready, "memoryFramePairing.openMenu()")
    ready.pump(0.5)
    tap(ready, 400, 240)                                 # inside the menu box area
    ready.pump(0.4)
    inside_hit = evaluate(ready, "document.elementFromPoint(400,240) !== document.getElementById('menuOverlay')")
    if inside_hit:
        assert menu_open(ready)
    outside_tap(ready, 5, 5)
    assert not menu_open(ready)


def test_menu_idle_timeout_and_touch_resets(ready):
    evaluate(ready, "memoryFramePairing.setIdleCloseForTests(2000)")
    evaluate(ready, "memoryFramePairing.openMenu()")
    ready.pump(1.4)
    tap(ready, 400, 240)                                 # any touch resets the timer
    ready.pump(1.4)
    assert menu_open(ready)
    ready.pump(1.6)
    assert not menu_open(ready)


@pytest.mark.no_media
def test_empty_library_closes_on_outside_tap(page, server, mf_app, net):
    add_phone(mf_app)
    page.pump(2.5)
    assert visible(page, "emptyOverlay")
    outside_tap(page, 5, 5)
    assert not visible(page, "emptyOverlay")


@pytest.mark.no_media
def test_first_run_pairing_ignores_idle_and_outside_tap(page, server, mf_app, net):
    page.pump(2.5)
    assert visible(page, "pairOverlay")
    evaluate(page, "memoryFramePairing.setIdleCloseForTests(500)")
    outside_tap(page, 5, 5)
    page.pump(2)
    assert visible(page, "pairOverlay")


def test_on_demand_pairing_closes_when_code_expires_not_on_outside_tap(ready, mf_app):
    evaluate(ready, "memoryFramePairing.openMenu()")
    ready.pump(0.3)
    evaluate(ready, "document.getElementById('menuPair').click()")
    ready.pump(1.5)
    assert visible(ready, "pairOverlay")
    outside_tap(ready, 5, 5)
    ready.pump(2.5)
    assert visible(ready, "pairOverlay")                 # outside tap doesn't close pairing
    with mf_app.app_context():
        auth.pairing().expires = 0                       # the 120 s code runs out
    ready.pump(4.5)
    assert not visible(ready, "pairOverlay")            # closed, and not renewed


# ---------- MF-33 ----------

def test_frame_shows_phone_paired_for_3s(ready, mf_app, server):
    evaluate(ready, "memoryFramePairing.openMenu()")
    ready.pump(0.3)
    evaluate(ready, "document.getElementById('menuPair').click()")
    ready.pump(1.5)
    with mf_app.app_context():
        code = auth.pairing().current()["code"]
    with mf_app.test_client() as c:
        c.get("/healthz", environ_base=PHONE)
        tok = c.get_cookie("mf_csrf").value
        r = c.post("/pair", data={"code": code, "csrf_token": tok}, environ_base=PHONE)
        assert r.status_code == 303 and r.location.endswith("/admin/?paired=1")
    end = time.time() + 5
    while time.time() < end and evaluate(ready, "document.getElementById('pairSuccess').hidden"):
        ready.pump(0.2)
    assert "✓ Phone paired" in text(ready, "pairSuccess") and "Send photos from your phone." in text(ready, "pairSuccess")
    assert visible(ready, "pairOverlay")
    ready.pump(4)
    assert not visible(ready, "pairOverlay")


def test_phone_banner_once(client):
    from conftest import pair_client
    pair_client(client)
    h = htmlmod.unescape(client.get("/admin/?paired=1", environ_base=PHONE).get_data(as_text=True))
    assert "✓ You're paired!" in h and "This phone can now send photos to Memory Frame." in h
    assert h.index("Upload photos") < h.index("Library")
    assert "You're paired!" not in htmlmod.unescape(client.get("/admin/", environ_base=PHONE).get_data(as_text=True))
