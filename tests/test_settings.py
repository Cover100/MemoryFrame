"""MF-17 (slim): settings page, API, and live application on the frame."""
import html as htmlmod
import time

import pytest

from test_frontend_browser import evaluate, net, page, server  # noqa: F401  (fixtures)

PHONE = {"REMOTE_ADDR": "10.42.0.23"}


def test_defaults_and_page(paired):
    assert paired.get("/api/v1/settings", environ_base=PHONE).get_json() == \
        {"slide_seconds": 10, "transition": "fade", "shuffle": True}
    h = htmlmod.unescape(paired.get("/admin/settings", environ_base=PHONE).get_data(as_text=True))
    for lbl in ("5 s", "10 s", "30 s", "1 min", "5 min", "Fade", "None"):
        assert f">{lbl}</button>" in h
    assert 'data-value="10" role="radio"\n      aria-checked="true"' in h
    assert 'data-value="fade" role="radio" aria-checked="true"' in h
    assert 'id="shuffle" data-key="shuffle" checked' in h
    assert h.count('data-key="') == 8                       # 5 + 2 + 1: exactly three controls
    home = paired.get("/admin/", environ_base=PHONE).get_data(as_text=True)
    assert 'class="row-link" href="/admin/settings"' in home and "<small>10 s · Fade · Shuffle on</small>" in home


def test_unpaired(client):
    assert client.get("/admin/settings", environ_base=PHONE).status_code == 302
    assert client.get("/api/v1/settings", environ_base=PHONE).status_code == 401
    assert client.put("/api/v1/settings", json={"shuffle": False}, environ_base=PHONE).status_code == 401


def test_partial_update_persists(paired, mf_app):
    r = paired.put("/api/v1/settings", json={"slide_seconds": 5}, environ_base=PHONE)
    assert r.status_code == 200 and r.get_json()["slide_seconds"] == 5
    paired.put("/api/v1/settings", json={"transition": "none", "shuffle": False}, environ_base=PHONE)
    assert paired.get("/api/v1/settings", environ_base=PHONE).get_json() == \
        {"slide_seconds": 5, "transition": "none", "shuffle": False}
    from memoryframe import create_app                      # survives a restart (same DB)
    app2 = create_app({"TESTING": True, "DATA_DIR": mf_app.config["DATA_DIR"], "DB_PATH": mf_app.config["DB_PATH"]})
    with app2.test_client() as c:
        assert c.get("/show-now", environ_base={"REMOTE_ADDR": "127.0.0.1"}).get_json()["settings"] == \
            {"slide_seconds": 5, "transition": "none", "shuffle": False}


@pytest.mark.parametrize("body", [{"slide_seconds": 7}, {"slide_seconds": "10"}, {"slide_seconds": True},
                                  {"transition": "wipe"}, {"shuffle": "yes"}, {"shuffle": 1}, {"colour": "red"},
                                  {}, [], "x"])
def test_invalid_400(paired, body):
    r = paired.put("/api/v1/settings", json=body, environ_base=PHONE)
    assert r.status_code == 400
    assert paired.get("/api/v1/settings", environ_base=PHONE).get_json()["slide_seconds"] == 10


def test_csrf_required(paired):
    r = paired.put("/api/v1/settings", json={"shuffle": False}, environ_base=PHONE, csrf=False)
    assert r.status_code == 403


def test_show_now_carries_settings(client):
    s = client.get("/show-now", environ_base=PHONE).get_json()["settings"]
    assert s == {"slide_seconds": 10, "transition": "fade", "shuffle": True}


# ---------- frame (headless) ----------

def put(app, **kw):
    from memoryframe import db as mfdb, settings
    conn = mfdb.connect(app.config["DB_PATH"])
    settings.save(conn, kw)
    conn.close()


def fs(page):
    return evaluate(page, "window.memoryFrameSettings || null")


def test_frame_applies_settings_live(page, server, mf_app, net):
    page.pump(3)
    s = fs(page)
    assert s["imageDuration"] == 10000 and s["fadeTime"] == 800 and s["shuffle"] is True
    put(mf_app, slide_seconds=5, transition="none", shuffle=False)
    page.pump(2.6)
    s = fs(page)
    assert s["imageDuration"] == 5000 and s["fadeTime"] == 0 and s["shuffle"] is False
    assert evaluate(page, "document.documentElement.classList.contains('mf-no-fade')")
    assert evaluate(page, "getComputedStyle(document.querySelector('.media')).transitionDuration") == "0s"
    # shuffle off -> library (name) order, same photo still current
    with mf_app.test_client() as c:
        names = [m["filename"] for m in c.get("/media", environ_base={"REMOTE_ADDR": "127.0.0.1"}).get_json()]
    assert s["playlist"] == names
    put(mf_app, transition="fade", shuffle=True)
    page.pump(2.6)
    s = fs(page)
    assert s["fadeTime"] == 800 and s["shuffle"] is True
    assert not evaluate(page, "document.documentElement.classList.contains('mf-no-fade')")


def test_slide_time_rearms_from_now(page, server, mf_app, net):
    page.pump(3)
    put(mf_app, slide_seconds=5)
    t0 = time.time()
    first = evaluate(page, "document.getElementById('image') ? document.getElementById('image').src : ''")
    changed = None
    while time.time() - t0 < 12:
        page.pump(0.25)
        cur = evaluate(page, "document.getElementById('image') ? document.getElementById('image').src : ''")
        if cur != first:
            changed = time.time() - t0
            break
    assert changed is not None and changed < 9, changed       # ~5 s (+2 s poll), not the old 10 s
