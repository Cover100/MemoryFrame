"""Restyle PR C: the phone site on the paper theme (#70 phone mocks 01-08, 04-settings-v2, 04b; DIRECTION.md).
Structure, escaping, CSRF and lean-CSS checks, plus a 412x915 headless-Chrome pass over every phone view."""
import html as htmlmod
import re
import time
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from memoryframe import auth, db as mfdb

ROOT = Path(__file__).resolve().parents[1]
PHONE = {"REMOTE_ADDR": "10.42.0.23"}
CSS = (ROOT / "static" / "phone.css").read_text()
PAGES = ["/admin/", "/admin/library", "/admin/upload", "/admin/settings", "/admin/wifi/"]
PNG = (ROOT / "tests" / "fixture_astronaut.jpg").read_bytes()


def body(c, url):
    r = c.get(url, environ_base=PHONE)
    assert r.status_code == 200, (url, r.status_code)
    return r.get_data(as_text=True)


@pytest.mark.parametrize("url", PAGES + ["/pair"])
def test_every_phone_page_uses_paper_theme_and_one_stylesheet(client, url):
    if url != "/pair":
        from conftest import pair_client
        pair_client(client)
    h = body(client, url)
    assert 'class="mf-phone"' in h and '<header class="bar">' in h and 'class="dot"' in h
    assert re.search(r'href="/static/tokens\.css\?v=\w+"', h) and re.search(r'href="/static/phone\.css\?v=\w+"', h)
    for old in ("pair.css", "library.css", "upload.css", "wifi.css", "settings.css"):
        assert old not in h
    # system fonts only: no frame font stylesheet, preload or woff2 on phone pages
    assert "frame-fonts.css" not in h and "woff2" not in h and "preload" not in h
    assert "<style" not in h and " style=" not in h


def test_old_page_stylesheets_removed():
    for old in ("pair.css", "library.css", "upload.css", "wifi.css", "settings.css"):
        assert not (ROOT / "static" / old).exists()


def test_phone_css_is_lean_and_flat():
    flat = re.sub(r"/\*.*?\*/", "", CSS, flags=re.S)
    for bad in ("box-shadow", "gradient", "filter", "backdrop", "@import", "@font-face", "url(", "@keyframes", "animation"):
        assert bad not in flat, bad
    assert len(CSS) < 24 * 1024
    assert "var(--mf-paper)" in (ROOT / "static" / "tokens.css").read_text()
    assert ".mf-phone .bar { background: var(--mf-forest-900)" in CSS          # forest header band
    # transitions only on transform (the Shuffle switch knob)
    for m in re.finditer(r"transition:\s*([^;]+);", flat):
        assert m.group(1).split()[0] == "transform", m.group(0)


def test_home_merges_library_preview_and_rows(paired, lib, mf_app):
    for i in range(12):
        lib.add(f"pic{i}.jpg", PNG + bytes([i]))
    conn = mfdb.connect(mf_app.config["DB_PATH"])
    newest = conn.execute("SELECT id FROM media ORDER BY created_at DESC, id DESC LIMIT 1").fetchone()["id"]
    conn.execute("UPDATE media SET hidden = 1 WHERE id = ?", (newest,))
    conn.commit()
    conn.close()
    h = htmlmod.unescape(body(paired, "/admin/"))
    assert h.count('/thumb" alt=') == 9 and f'/api/v1/media/{newest}/thumb' in h
    assert '<li class="hid">' in h and '<span class="tag">Hidden</span>' in h
    assert "See all 12 photos ›" in h and 'href="/admin/library"' in h
    assert h.index("Upload photos") < h.index("Library") < h.index("Frame Wi-Fi") < h.index("Settings<small>")
    assert "Connected to the frame · " in h and " GB free" in h
    form = h[h.index('id="signOutForm"'):h.index("</form>", h.index('id="signOutForm"'))]
    assert 'name="csrf_token" value="' in form and "Sign out all phones" in form


def test_home_empty_library(paired):
    h = body(paired, "/admin/")
    assert "No photos yet." in h and 'class="grid"' not in h


def test_names_and_ssid_are_escaped(paired, lib, mf_app, monkeypatch):
    lib.add('<img src=x onerror=alert(1)>.jpg', PNG)
    from memoryframe import db
    conn = mfdb.connect(mf_app.config["DB_PATH"])
    db.set_setting(conn, "wifi_ssid", "<b>evil</b>")
    db.set_setting(conn, "wifi_password", "password123")
    conn.commit()
    conn.close()
    for url in ("/admin/", "/admin/settings", "/admin/upload"):
        h = body(paired, url)
        assert "<b>evil</b>" not in h and "<img src=x" not in h
    assert "&lt;b&gt;evil&lt;/b&gt;" in body(paired, "/admin/")
    assert "password123" not in body(paired, "/admin/") + body(paired, "/admin/settings")


def test_offline_panel_on_paired_pages_only(paired, client):
    for url in PAGES:
        h = body(paired, url)
        assert 'id="offline" hidden' in h and "Can't reach the frame" in h and "/static/phone.js" in h
    h = body(paired, "/admin/upload")
    assert 'id="offlineRetry"' in h and "Come back and tap <b>Try again</b>." in h
    js = (ROOT / "static" / "phone.js").read_text()
    assert "textContent" in js and "innerHTML" not in js and '"/healthz"' in js


def test_pair_page_has_no_offline_panel_or_ssid(client):
    h = body(client, "/pair")
    assert 'id="offline"' not in h and "phone.js" not in h
    assert 'name="code" inputmode="numeric" pattern="[0-9]{6}" maxlength="6" autocomplete="one-time-code"' in h
    assert "Can't see a code?" in h and "<b>Pair new device</b>" in h
    assert 'name="csrf_token"' in h


def test_pair_error_and_token_landing(client):
    r = client.post("/pair", data={"code": "000000"}, environ_base=PHONE)
    assert r.status_code == 400 and '<p class="err" role="alert">' in r.get_data(as_text=True)
    h = body(client, "/pair?t=%22%3E%3Cscript%3E")
    assert 'id="tokenForm"' in h and "<script>" not in h.split("</header>")[1].split("<script")[0]


def test_settings_v2_structure(paired):
    h = body(paired, "/admin/settings")
    order = ["Message on the frame", "Slide time", "Transition", "Shuffle", "About this frame"]
    assert [h.index(x) for x in order] == sorted(h.index(x) for x in order)
    assert 'class="switch" role="switch" id="shuffle"' in h
    assert '<details class="rows about" id="aboutSection">' in h
    for i in ("aboutWifi", "aboutIp", "aboutSite", "aboutPhones", "aboutVersion"):
        assert f'id="{i}">' in h
    assert 'id="aboutPhones">1<' in h
    assert "Preview: how it will look on the frame" in h and 'id="messageDirty" hidden>Not saved yet<' in h


def test_upload_returns_media_id_for_the_thumbnail(paired):
    import io
    r = paired.post("/api/v1/media", data={"file": (io.BytesIO(PNG), "a.jpg")}, environ_base=PHONE,
                    content_type="multipart/form-data")
    j = r.get_json()
    assert r.status_code == 201 and isinstance(j["media_id"], int) and isinstance(j["id"], str)
    assert paired.get(f"/api/v1/media/{j['media_id']}/thumb", environ_base=PHONE).status_code == 200


def test_keep_page_names_the_old_wifi(paired, mf_app):
    conn = mfdb.connect(mf_app.config["DB_PATH"])
    conn.execute("INSERT OR REPLACE INTO wifi_change (id, state, old_ssid, new_ssid, new_password, old_profile, "
                 "new_profile, deadline, updated) VALUES (1, 'pending', 'Old <Net>', 'Garden Frame', 'pw12345678', "
                 "'a', 'b', ?, ?)", (time.time() + 200, time.time()))
    conn.commit()
    conn.close()
    h = body(paired, "/admin/wifi/keep")
    assert 'class="ring" aria-hidden="true"' in h and "Not ready? Do nothing" in h and "<b>Old &lt;Net&gt;</b>" in h
    assert 'name="csrf_token"' in h and "pw12345678" not in h


def test_wifi_form_has_warning_hint_and_sheet(paired, monkeypatch, tmp_path):
    monkeypatch.setenv("MEMORYFRAME_NETMODE_STATE", str(tmp_path / "nm.json"))
    (tmp_path / "nm.json").write_text('{"mode": "product"}')
    h = body(paired, "/admin/wifi/")
    if 'id="wifiForm"' not in h:
        pytest.skip("Wi-Fi change unavailable in this environment")
    assert "All phones will disconnect." in h and "8 to 63 letters, numbers or symbols." in h
    assert 'id="confirmStep" class="scrim" hidden' in h and 'role="alertdialog" aria-modal="true"' in h
    assert 'id="cancelBtn" class="secondary big btn-default"' in h


# ---------------- 412x915 headless Chrome ----------------

from test_frontend_browser import BROWSER, evaluate, page, server  # noqa: E402,F401  (fixtures)


def _phone(page, server, mf_app, path):
    conn = mfdb.connect(mf_app.config["DB_PATH"])
    token = auth.create_session(conn, "test phone")
    conn.close()
    page.call("Emulation.setDeviceMetricsOverride", {"width": 412, "height": 915, "deviceScaleFactor": 1, "mobile": True})
    page.call("Network.enable")
    page.call("Network.setCookie", {"name": "mf_session", "value": token, "domain": urlsplit(server).hostname,
                                    "path": "/", "httpOnly": True, "sameSite": "Strict"})
    page.call("Page.navigate", {"url": server + path})
    page.pump(2)


@pytest.mark.skipif(BROWSER is None, reason="no Chromium/Chrome installed")
def test_phone_views_fit_412_and_run_clean(page, server, mf_app):
    for path in ("admin/", "admin/library", "admin/upload", "admin/settings", "admin/wifi/", "pair"):
        _phone(page, server, mf_app, path)
        assert evaluate(page, "document.documentElement.scrollWidth") <= 412, path
        assert evaluate(page, "getComputedStyle(document.body).backgroundColor") == "rgb(246, 241, 233)", path
        assert evaluate(page, "getComputedStyle(document.querySelector('.bar')).backgroundColor") == "rgb(22, 38, 29)"
        assert "Atkinson" not in evaluate(page, "getComputedStyle(document.body).fontFamily")
        assert evaluate(page, "document.getAnimations().length") == 0, path
    assert page.exceptions() == []


@pytest.mark.skipif(BROWSER is None, reason="no Chromium/Chrome installed")
def test_settings_message_states_and_remove_confirm(page, server, mf_app):
    _phone(page, server, mf_app, "admin/settings")
    js = "document.getElementById"
    evaluate(page, f"{js}('frameMessage').value='Hi Nana'; {js}('frameMessage').dispatchEvent(new Event('input'))")
    assert not evaluate(page, f"{js}('messageDirty').hidden") and not evaluate(page, f"{js}('messageSave').disabled")
    assert evaluate(page, f"{js}('messageRemove').hidden")
    evaluate(page, f"{js}('messageSave').click()")
    page.pump(1)
    assert evaluate(page, f"{js}('messageSave').disabled") and evaluate(page, f"{js}('messageSave').textContent") == "Saved"
    assert evaluate(page, f"{js}('messageDirty').hidden") and not evaluate(page, f"{js}('messageRemove').hidden")
    assert evaluate(page, f"{js}('messagePreviewLabel').textContent") == "On the frame now"
    evaluate(page, f"{js}('messageRemove').click()")
    assert not evaluate(page, f"{js}('messageConfirm').hidden")
    assert evaluate(page, "document.activeElement.id") == "messageRemoveNo"
    evaluate(page, f"{js}('messageRemoveNo').click()")
    assert evaluate(page, f"{js}('messageConfirm').hidden") and not evaluate(page, f"{js}('messageRemove').hidden")
    evaluate(page, f"{js}('messageRemove').click()")
    evaluate(page, f"{js}('messageRemoveYes').click()")
    page.pump(1)
    assert evaluate(page, f"{js}('frameMessage').value") == "" and not evaluate(page, f"{js}('messagePreviewEmpty').hidden")
    assert evaluate(page, f"{js}('messageRemove').hidden") and evaluate(page, f"{js}('messageConfirm').hidden")
    assert page.exceptions() == []


@pytest.mark.skipif(BROWSER is None, reason="no Chromium/Chrome installed")
def test_network_failure_shows_offline_view_and_try_again_recovers(page, server, mf_app):
    _phone(page, server, mf_app, "admin/settings")
    evaluate(page, "window.__f = window.fetch; window.fetch = () => Promise.reject(new TypeError('Failed to fetch'))")
    evaluate(page, "document.querySelector('#transitionSeg button[data-value=\"none\"]').click()")
    page.pump(1)
    assert not evaluate(page, "document.getElementById('offline').hidden")
    assert evaluate(page, "document.getElementById('page').hidden")
    # still down: stays on the offline view
    evaluate(page, "document.getElementById('offlineRetry').click()")
    page.pump(1)
    assert not evaluate(page, "document.getElementById('offlineStill').hidden")
    evaluate(page, "window.fetch = window.__f; document.getElementById('offlineRetry').click()")
    page.pump(1)
    assert evaluate(page, "document.getElementById('offline').hidden") and not evaluate(page, "document.getElementById('page').hidden")
    # the failed tap was reverted, not silently kept
    assert evaluate(page, "document.querySelector('#transitionSeg button[data-value=\"fade\"]').getAttribute('aria-checked')") == "true"
    assert page.exceptions() == []
