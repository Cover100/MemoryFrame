"""Restyle PR C: the phone site (#70 phone mocks 01-08, 04-settings-v2, 04b; DIRECTION.md), on the frame's dark
theme + Atkinson since 19 Oct (PHONE-DARK.md, design/visual-direction 6621607).
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
    # PHONE-DARK.md: Atkinson on every phone page via the frame's /frame-fonts.css (block), before tokens.css,
    # no preload on the phone (was: system fonts, Settings-only link)
    assert h.count('<link rel="stylesheet" href="/frame-fonts.css">') == 1
    assert h.index("/frame-fonts.css") < h.index("tokens.css") and "woff2" not in h and "preload" not in h
    assert "<style" not in h and " style=" not in h


def test_old_page_stylesheets_removed():
    for old in ("pair.css", "library.css", "upload.css", "wifi.css", "settings.css"):
        assert not (ROOT / "static" / old).exists()


def test_phone_css_is_lean_and_flat():
    flat = re.sub(r"/\*.*?\*/", "", CSS, flags=re.S)
    for bad in ("box-shadow", "gradient", "filter", "backdrop", "@import", "@font-face", "url(", "@keyframes", "animation"):
        assert bad not in flat, bad
    assert len(CSS) < 26 * 1024                                                # #80 library grew it past 24 KB
    assert "var(--mf-paper)" in (ROOT / "static" / "tokens.css").read_text()
    assert ".mf-phone .bar { background: var(--mf-forest-900)" in CSS          # forest header band
    # transitions only on transform (Shuffle knob, library tile inset) and opacity (#80 deleted-tile fade)
    for m in re.finditer(r"transition:\s*([^;]+);", flat):
        assert m.group(1).split()[0] in ("transform", "opacity"), m.group(0)


def test_home_merges_library_preview_and_rows(paired, lib, mf_app):
    for i in range(12):
        lib.add(f"pic{i}.jpg", PNG + bytes([i]))
    conn = mfdb.connect(mf_app.config["DB_PATH"])
    newest = conn.execute("SELECT id FROM media ORDER BY created_at DESC, id DESC LIMIT 1").fetchone()["id"]
    conn.execute("UPDATE media SET hidden = 1 WHERE id = ?", (newest,))
    conn.commit()
    conn.close()
    h = htmlmod.unescape(body(paired, "/admin/"))
    assert h.count('/thumb" alt=""') == 9 and "pic" not in h.split("Newest photos")[1].split("</ul>")[0]   # no file names
    assert 'aria-label="Photo 1"' in h
    assert h.count('/thumb" alt=') == 9 and f'/api/v1/media/{newest}/thumb' in h
    assert "hid" not in h.replace("hidden", "") and "Hidden" not in h      # #80: nothing on the phone hides a photo
    assert '<a class="btn btn-neutral block see-all" href="/admin/library"><span>See all 12 photos</span>' in h
    assert h.index("Upload photos") < h.index("Library") < h.index('Frame <span class="nw">Wi-Fi') < h.index("Settings<small>")
    assert "Connected to the frame</p>" in h and 'role="meter"' in h and " GB free <span>of " in h
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
    assert re.search(r'class="ring q[1-4]" aria-hidden="true"', h) and "Not ready? Do nothing" in h and "<b>Old &lt;Net&gt;</b>" in h
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
        assert evaluate(page, "getComputedStyle(document.body).backgroundColor") == "rgb(14, 19, 17)", path   # --mf-ink
        assert evaluate(page, "getComputedStyle(document.querySelector('.bar')).backgroundColor") == "rgb(22, 38, 29)"
        assert evaluate(page, "getComputedStyle(document.body).fontFamily").startswith('"MF Frame", "Atkinson Hyperlegible", system-ui')
        assert evaluate(page, "document.fonts.check('16px \"MF Frame\"') && document.fonts.check('700 16px \"MF Frame\"')"), path
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
    assert evaluate(page, f"{js}('messageSave').getAttribute('aria-disabled')") == "true"
    assert evaluate(page, f"{js}('messageSave').textContent") == "Saved"
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
    assert not evaluate(page, f"{js}('messageSave').disabled")           # nothing stored: Save is free again
    css = "getComputedStyle(document.getElementById('messageRemoveNo'))"
    evaluate(page, f"{js}('frameMessage').value='x'; {js}('messageSave').click()")
    page.pump(1)
    evaluate(page, f"{js}('messageRemove').click()")
    assert evaluate(page, f"{css}.color") == "rgb(243, 237, 228)" and evaluate(page, f"{css}.borderTopColor") == "rgb(201, 187, 168)"
    assert evaluate(page, "getComputedStyle(document.getElementById('messageRemoveYes')).backgroundColor") == "rgb(168, 53, 42)"
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


def test_phone_pages_use_the_frame_font_css_with_block(client):
    """PHONE-DARK.md: Atkinson on every phone page from the frame's own /frame-fonts.css (font-display: block)."""
    r = client.get("/frame-fonts.css", environ_base=PHONE)
    css = r.get_data(as_text=True)
    assert r.status_code == 200 and css.count("font-display: block") == 2 and "swap" not in css


def test_phone_font_family_and_buttons_inherit():
    tokens = (ROOT / "static" / "tokens.css").read_text()
    assert '--mf-font-phone: "MF Frame", "Atkinson Hyperlegible", system-ui, -apple-system,' in tokens   # system fallback
    assert re.search(r"\.mf-phone \{[^}]*background: var\(--mf-paper\); color: var\(--mf-text\); font-family: var\(--mf-font-phone\); \}", tokens)
    assert re.search(r"\.mf-phone button \{ font: inherit;", tokens)
    assert re.search(r"\.mf-phone \.btn, \.mf-phone button \{[^}]*font: inherit;", CSS)
    assert "frame-fonts.css" not in (ROOT / "templates" / "settings.html").read_text()


def test_frame_font_only_used_by_the_preview():
    flat = re.sub(r"/\*.*?\*/", "", CSS, flags=re.S)
    uses = [m.group(0) for m in re.finditer(r"[^{}]+\{[^}]*--mf-font-frame[^}]*\}", flat)]
    assert len(uses) == 1 and ".msgPreview" in uses[0]


@pytest.mark.skipif(BROWSER is None, reason="no Chromium/Chrome installed")
@pytest.mark.parametrize("zoom", [1.0, 1.3])
def test_360_wide_and_130_percent_text_fit(page, server, mf_app, zoom):
    """Wren M3: 360 px phones with 130 % OS font scale: no sideways scroll, no clipped headings."""
    for path in ("admin/", "admin/library", "admin/upload", "admin/settings", "admin/wifi/", "pair"):
        _phone(page, server, mf_app, path)
        page.call("Emulation.setDeviceMetricsOverride", {"width": 360, "height": 800, "deviceScaleFactor": 1, "mobile": True})
        evaluate(page, f"document.documentElement.style.zoom = '{zoom}'")      # how Android Chrome applies font scale
        page.pump(0.3)
        assert evaluate(page, "document.documentElement.scrollWidth <= document.documentElement.clientWidth"), (path, zoom)
        clipped = evaluate(page, """JSON.stringify([...document.querySelectorAll('h1,h2,button,.seg button,label')]
            .filter(e=>e.offsetParent && !e.closest('.sr-only') && e.scrollWidth > e.clientWidth + 1).map(e=>e.id||e.textContent.trim().slice(0,20)))""")
        assert clipped == "[]", (path, zoom, clipped)
    if True:   # the Wi-Fi sheet's buttons stay on screen
        _phone(page, server, mf_app, "admin/wifi/")
        page.call("Emulation.setDeviceMetricsOverride", {"width": 360, "height": 800, "deviceScaleFactor": 1, "mobile": True})
        evaluate(page, f"document.documentElement.style.zoom = '{zoom}'")
        if evaluate(page, "!!document.getElementById('wifiForm')"):
            evaluate(page, "document.getElementById('confirmStep').hidden = false")
            r = evaluate(page, "document.getElementById('confirmBtn').getBoundingClientRect().right * "
                               "(parseFloat(document.documentElement.style.zoom) || 1)")
            assert r <= 360.5, r


@pytest.mark.skipif(BROWSER is None, reason="no Chromium/Chrome installed")
def test_touch_targets_at_least_48(page, server, mf_app, lib):
    """Wren M2 / DIRECTION §3: phone targets >= 48 px (hit area, incl. ::after / transparent border)."""
    lib.add("c.jpg", PNG)
    def hit(sel):
        return evaluate(page, f"""JSON.stringify([...document.querySelectorAll('{sel}')].filter(e=>e.offsetParent).map(e=>{{
            const r=e.getBoundingClientRect(), a=getComputedStyle(e,'::after');
            const t = parseFloat(a.top) || 0, extra = a.content !== 'none' && a.position === 'absolute' && t < 0 ? -2 * t : 0;
            return [Math.round(r.width), Math.round(r.height + extra)];}}))""")
    import json
    _phone(page, server, mf_app, "admin/settings")
    for sel in ("#slideSeg button", "#transitionSeg button", "#shuffle", "#messageSave", ".bar .back"):
        sizes = json.loads(hit(sel))
        assert sizes and all(h >= 48 for w, h in sizes), (sel, sizes)
    assert json.loads(hit("#shuffle"))[0][0] >= 48
    _phone(page, server, mf_app, "admin/")
    assert all(h >= 48 for w, h in json.loads(hit(".see-all"))), hit(".see-all")
    assert all(h >= 48 for w, h in json.loads(hit(".row-link")))
    _phone(page, server, mf_app, "admin/library")
    page.pump(1)
    for sel in ("#selectBtn", ".lib .tile", ".bar .back"):                      # #80
        sizes = json.loads(hit(sel))
        assert sizes and all(h >= 48 and w >= 48 for w, h in sizes), (sel, sizes)
    evaluate(page, "document.getElementById('selectBtn').click()")
    for sel in ("#selAll", "#selCancel", "#delBtn"):
        sizes = json.loads(hit(sel))
        assert sizes and all(h >= 48 for w, h in sizes), (sel, sizes)


@pytest.mark.skipif(BROWSER is None, reason="no Chromium/Chrome installed")
def test_empty_box_never_saves_over_a_message(page, server, mf_app):
    """Wren M1 / #75: clearing the box while a message is saved disables Save (Remove message is the way)."""
    from memoryframe import message
    message.save(mf_app.config["DATA_DIR"], "Hi Nana")
    _phone(page, server, mf_app, "admin/settings")
    js = "document.getElementById"
    assert evaluate(page, f"{js}('messageSave').getAttribute('aria-disabled')") == "true"      # 04b "Saved"
    evaluate(page, f"{js}('frameMessage').value=''; {js}('frameMessage').dispatchEvent(new Event('input'))")
    assert evaluate(page, f"{js}('messageSave').disabled") is True
    assert evaluate(page, f"{js}('messageDirty').textContent") == "Use Remove message to clear it"
    # the preview keeps showing what the frame shows until Remove is confirmed (Hollis, 48cdae8)
    assert evaluate(page, f"{js}('messagePreviewText').textContent") == "Hi Nana"
    assert not evaluate(page, f"{js}('messagePreview').hidden") and evaluate(page, f"{js}('messagePreviewEmpty').hidden")
    assert evaluate(page, f"{js}('messagePreviewLabel').textContent") == "On the frame now"
    evaluate(page, f"{js}('messageSave').click()")
    page.pump(0.5)
    assert message.get(mf_app.config["DATA_DIR"]) == "Hi Nana"
    # "Saved" is inert too: a tap sends nothing
    evaluate(page, f"{js}('frameMessage').value='Hi Nana'; {js}('frameMessage').dispatchEvent(new Event('input'))")
    evaluate(page, "window.__puts = 0; const f = window.fetch; window.fetch = (u, o) => { window.__puts++; return f(u, o); }")
    evaluate(page, f"{js}('messageSave').click()")
    page.pump(0.3)
    assert evaluate(page, "window.__puts") == 0
    assert "btn-default" in evaluate(page, f"{js}('messageRemoveNo').className")


@pytest.mark.skipif(BROWSER is None, reason="no Chromium/Chrome installed")
def test_wifi_sheet_traps_header_esc_cancels_and_errors_mark_field(page, server, mf_app, monkeypatch, tmp_path):
    monkeypatch.setenv("MEMORYFRAME_NETMODE_STATE", str(tmp_path / "nm.json"))
    (tmp_path / "nm.json").write_text('{"mode": "product"}')
    _phone(page, server, mf_app, "admin/wifi/")
    js = "document.getElementById"
    if not evaluate(page, f"!!{js}('wifiForm')"):
        pytest.skip("Wi-Fi change unavailable in this environment")
    evaluate(page, f"{js}('ssid').value='Garden'; {js}('password').value='short'; {js}('changeBtn').click()")
    assert evaluate(page, f"{js}('password').getAttribute('aria-invalid')") == "true"
    assert evaluate(page, f"{js}('password').getAttribute('aria-describedby')") == "pwHint error"
    assert evaluate(page, f"getComputedStyle({js}('password')).borderTopColor") == "rgb(231, 160, 151)"   # brick-text
    evaluate(page, f"{js}('password').value='long-enough-1'; {js}('changeBtn').click()")
    assert not evaluate(page, f"{js}('confirmStep').hidden")
    assert evaluate(page, f"{js}('password').getAttribute('aria-invalid')") is None
    assert evaluate(page, "document.querySelector('.bar').inert") is True
    evaluate(page, "document.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape'}))")
    assert evaluate(page, f"{js}('confirmStep').hidden") and evaluate(page, "document.querySelector('.bar').inert") is False


@pytest.mark.skipif(BROWSER is None, reason="no Chromium/Chrome installed")
def test_offline_view_takes_focus(page, server, mf_app):
    _phone(page, server, mf_app, "admin/upload")
    evaluate(page, "memoryFramePhone.offline('x.jpg failed')")
    assert evaluate(page, "document.activeElement.tagName + '|' + document.activeElement.textContent") == "H1|Can't reach the frame"
    evaluate(page, "memoryFramePhone.online()")
    assert evaluate(page, "document.activeElement.closest('#page') !== null")


def test_wifi_buttons_keep_wi_fi_whole(paired, monkeypatch, tmp_path):
    """Hollis 48cdae8 / Wren: 'Wi- / Fi' at 360 px. A plain hyphen inside <span class="nw"> keeps 'Wi-Fi' whole
    (some Android fonts have no U+2011); the space before it still wraps at 130 %."""
    monkeypatch.setenv("MEMORYFRAME_NETMODE_STATE", str(tmp_path / "nm.json"))
    (tmp_path / "nm.json").write_text('{"mode": "product"}')
    h = body(paired, "/admin/wifi/")
    if 'id="wifiForm"' not in h:
        pytest.skip("Wi-Fi change unavailable in this environment")
    for i in ("changeBtn", "confirmBtn"):
        assert f'id="{i}" class="' in h and h.split(f'id="{i}"')[1].split("</button>")[0].endswith(
            'Change <span class="nw">Wi-Fi</span>')
    assert "\u2011" not in h and "&#8209;" not in h
    assert ".mf-phone .nw { white-space: nowrap; }" in CSS


def test_script_focused_headings_have_no_outline():
    assert '.mf-phone [tabindex="-1"]:focus { outline: none; }' in CSS


@pytest.mark.skipif(BROWSER is None, reason="no Chromium/Chrome installed")
def test_offline_heading_focus_draws_no_outline(page, server, mf_app):
    _phone(page, server, mf_app, "admin/upload")
    evaluate(page, "memoryFramePhone.offline()")
    assert evaluate(page, "getComputedStyle(document.activeElement).outlineStyle") == "none"


@pytest.mark.skipif(BROWSER is None, reason="no Chromium/Chrome installed")
@pytest.mark.parametrize("zoom", [1.0, 1.3])
def test_wifi_sheet_buttons_keep_wi_fi_whole_at_360(page, server, mf_app, monkeypatch, tmp_path, zoom):
    monkeypatch.setenv("MEMORYFRAME_NETMODE_STATE", str(tmp_path / "nm.json"))
    (tmp_path / "nm.json").write_text('{"mode": "product"}')
    _phone(page, server, mf_app, "admin/wifi/")
    page.call("Emulation.setDeviceMetricsOverride", {"width": 360, "height": 800, "deviceScaleFactor": 1, "mobile": True})
    evaluate(page, f"document.documentElement.style.zoom = '{zoom}'")
    if not evaluate(page, "!!document.getElementById('wifiForm')"):
        pytest.skip("Wi-Fi change unavailable in this environment")
    evaluate(page, "document.getElementById('confirmStep').hidden = false")
    page.pump(0.2)
    # "Wi-Fi" stays whole: its span is one line box (never "Wi-" / "Fi"), in the buttons and the H1 / title
    assert evaluate(page, """[...document.querySelectorAll('#confirmBtn .nw, #changeBtn .nw, main h1 .nw, .bar .nw')]
        .every((n)=> n.getClientRects().length === 1 && n.textContent === 'Wi-Fi')""")
    assert evaluate(page, "document.documentElement.scrollWidth <= document.documentElement.clientWidth")
    assert evaluate(page, "(()=>{const b=document.getElementById('confirmBtn'); return b.scrollWidth <= b.clientWidth + 1;})()")


# ---------------- #79: no text links, styled upload picker, paper theme everywhere, storage meter ----------------

TEMPLATES = ROOT / "templates"
FRAME_ONLY = {"index.html"}                         # the frame's own screen (dark slideshow), not a phone page
PARTIALS = {"_phone.html", "_storage.html"}
LINK_CLASSES = {"btn", "row-link", "back", "icon"}  # buttons, row cards, the header chevron and gear
PHONE_JS = ["admin.js", "library.js", "upload.js", "settings.js", "wifi.js", "phone.js"]


def _phone_templates():
    return sorted(p for p in TEMPLATES.glob("*.html") if p.name not in FRAME_ONLY | PARTIALS)


def test_every_phone_template_uses_the_shared_phone_base():
    names = [p.name for p in _phone_templates()]
    assert "phone_error.html" in names and "upload.html" in names
    for p in _phone_templates():
        assert p.read_text().lstrip().startswith('{% extends "_phone.html" %}'), p.name


@pytest.mark.parametrize("tpl", [p.name for p in sorted(TEMPLATES.glob("*.html")) if p.name not in FRAME_ONLY])
def test_phone_templates_have_no_unstyled_text_links_or_visible_file_inputs(tpl):
    src = (TEMPLATES / tpl).read_text()
    for m in re.finditer(r"<a\b([^>]*)>(.*?)</a>", src, flags=re.S):
        attrs, inner = m.groups()
        cls = set((re.search(r'class="([^"]*)"', attrs) or [None, ""])[1].split())
        image_only = re.fullmatch(r"\s*<img\b[^>]*>\s*", inner) is not None      # tappable thumbnail
        assert cls & LINK_CLASSES or image_only, (tpl, m.group(0)[:120])
    for m in re.finditer(r'<input\b[^>]*type="file"[^>]*>', src):
        before = src[:m.start()]
        assert before.rfind('<label class="pick') > before.rfind("</label>"), (tpl, "file input outside the picker")
    flat = re.sub(r"/\*.*?\*/", "", CSS, flags=re.S)
    assert re.search(r"\.pick input \{[^}]*width: 1px; height: 1px; opacity: 0;", flat)


@pytest.mark.parametrize("js", PHONE_JS)
def test_phone_scripts_never_build_text_links(js):
    src = (ROOT / "static" / js).read_text()
    assert 'createElement("a")' not in src and "createElement('a')" not in src and "<a " not in src, js


def test_phone_error_pages_stay_on_the_paper_theme(client, paired):
    for url, code in (("/admin/nope", 404), ("/pair/nope", 404)):
        r = client.get(url, environ_base=PHONE)
        h = r.get_data(as_text=True)
        assert r.status_code == code and 'class="mf-phone"' in h and '<header class="bar">' in h, url
        assert '<a class="btn block big mt" href="/admin/">Back to Memory Frame</a>' in h
    r = client.post("/admin/library", environ_base=PHONE, headers={"X-CSRF-Token": "x"})
    assert r.status_code in (403, 405)
    r = client.get("/api/v1/nope", environ_base=PHONE)
    assert r.status_code == 404 and "mf-phone" not in r.get_data(as_text=True)     # API keeps its default
    assert "mf-phone" not in client.get("/uploads/nope.jpg").get_data(as_text=True)


def _fake_disk(monkeypatch, total, free):
    import collections
    import shutil
    du = collections.namedtuple("du", "total used free")(total, total - free, free)
    monkeypatch.setattr(shutil, "disk_usage", lambda _p: du)


@pytest.mark.parametrize("url", ["/admin/", "/admin/upload"])
def test_storage_meter_uses_the_frame_menu_numbers(paired, mf_app, monkeypatch, url):
    from memoryframe import frameinfo
    _fake_disk(monkeypatch, int(28.8 * 2**30), int(21.4 * 2**30))
    with mf_app.app_context():
        fs = frameinfo.free_storage(mf_app.config["DATA_DIR"])                 # what /frame/status gives the menu
    used = 1 - fs["free"] / fs["total"]
    h = body(paired, url)
    assert "Free space:" not in h and "Checking free space" not in h            # no plain text alone
    assert '<b class="storeFree">21.4 GB free <span>of 28.8 GB</span></b>' in h
    meter = re.search(r'<div class="track" role="meter"[^>]*>', h).group(0)
    for attr in ('aria-label="Storage used"', 'aria-valuemin="0"', 'aria-valuemax="100"',
                 f'aria-valuenow="{round(used * 100)}"', 'aria-valuetext="21.4 GB free of 28.8 GB"'):
        assert attr in meter, (attr, meter)
    assert f'data-used="{used:.3f}"' in h
    if url == "/admin/":
        strip = h[h.index('<div class="strip'):h.index("<main")]
        assert "has-store" in strip and 'class="store on-dark"' in strip and "GB free" in strip
    else:
        assert 'id="storeRoom"' in h and "Room for about " in h


def test_storage_meter_css_matches_the_frame_menu():
    frame = (ROOT / "static" / "style.css").read_text()
    assert "transform-origin:left;transform:scaleX(0)" in frame.replace(" ", "")             # #74 menu bar
    flat = re.sub(r"/\*.*?\*/", "", CSS, flags=re.S)
    assert re.search(r"\.track \{ height: 8px; border-radius: 4px;[^}]*overflow: hidden; \}", flat)
    assert re.search(r"\.track \.used \{[^}]*transform-origin: left; transform: scaleX\(0\); \}", flat)
    assert "tabular-nums" in flat


@pytest.mark.skipif(BROWSER is None, reason="no Chromium/Chrome installed")
def test_phone_pages_show_no_text_links_or_raw_file_input(page, server, mf_app, lib):
    """#79: in the rendered pages every visible link is a button/row/chevron (>= 48 px, no underline) or a
    photo, and the native file control is never visible."""
    import json
    lib.add("c.jpg", PNG)
    for path in ("admin/", "admin/library", "admin/upload", "admin/settings", "admin/wifi/", "pair", "admin/nope"):
        _phone(page, server, mf_app, path)
        page.pump(0.5)
        bad = json.loads(evaluate(page, """JSON.stringify([...document.querySelectorAll('a')].filter(a=>a.offsetParent).filter(a=>{
            const r=a.getBoundingClientRect(), cs=getComputedStyle(a);
            const photo = a.children.length===1 && a.firstElementChild.tagName==='IMG';
            return !photo && (cs.textDecorationLine !== 'none' || r.height < 48);}).map(a=>a.outerHTML.slice(0,80)))"""))
        assert bad == [], (path, bad)
        files = json.loads(evaluate(page, """JSON.stringify([...document.querySelectorAll('input[type=file]')].map(i=>{
            const r=i.getBoundingClientRect(); return [parseFloat(getComputedStyle(i).opacity), r.width, r.height];}))"""))
        assert all(o == 0 and w <= 1 and h <= 1 for o, w, h in files), (path, files)
        assert evaluate(page, "getComputedStyle(document.body).backgroundColor") == "rgb(14, 19, 17)", path   # --mf-ink
        assert evaluate(page, "getComputedStyle(document.querySelector('.bar')).backgroundColor") == "rgb(22, 38, 29)"
    assert page.exceptions() == []


@pytest.mark.skipif(BROWSER is None, reason="no Chromium/Chrome installed")
@pytest.mark.parametrize("path", ["admin/", "admin/upload"])
def test_storage_meter_fills_like_the_frame_menu(page, server, mf_app, path):
    _phone(page, server, mf_app, path)
    page.pump(0.3)
    got = evaluate(page, """(()=>{const u=document.querySelector('.store .used'); const t=document.querySelector('.store .track');
        const m=getComputedStyle(u).transform.match(/matrix\\(([^,]+)/); return JSON.stringify([parseFloat(m[1]), parseFloat(u.dataset.used),
        t.getAttribute('role'), Math.round(t.getBoundingClientRect().height), getComputedStyle(u).transformOrigin.split(' ')[0]]);})()""")
    import json
    sx, used, role, h, origin = json.loads(got)
    assert role == "meter" and abs(sx - used) < 0.002 and 0 < used < 1 and h in (6, 8) and origin == "0px"
    assert evaluate(page, "document.querySelector('.store').offsetParent !== null")


@pytest.mark.skipif(BROWSER is None, reason="no Chromium/Chrome installed")
def test_drop_area_uploads_and_picker_is_disabled_while_sending(page, server, mf_app):
    """#79: dropped photos use the same pipeline; while a batch is sending the picker looks and is disabled."""
    _phone(page, server, mf_app, "admin/upload")
    assert evaluate(page, "getComputedStyle(document.getElementById('drop')).borderTopStyle") == "dashed"
    evaluate(page, """(()=>{ window.__held = []; const send = XMLHttpRequest.prototype.send;
        XMLHttpRequest.prototype.send = function(b){ window.__held.push([this, b, send]); };
        const dt = new DataTransfer(); dt.items.add(new File(['not a photo'], 'notes.jpg', {type: 'image/jpeg'}));
        document.getElementById('drop').dispatchEvent(new DragEvent('drop', {dataTransfer: dt, bubbles: true, cancelable: true}));
    })()""")
    page.pump(0.3)
    st = evaluate(page, """JSON.stringify([document.getElementById('files').disabled, document.getElementById('drop').getAttribute('aria-disabled'),
        document.getElementById('pickLabel').textContent, getComputedStyle(document.getElementById('pickLabel')).backgroundColor,
        document.querySelectorAll('#list li').length, document.getElementById('summary').textContent])""")
    import json
    disabled, aria, label, bg, rows, summary = json.loads(st)
    assert disabled and aria == "true" and label == "Uploading..." and bg == "rgb(36, 56, 44)", st
    assert rows == 1 and "Sending 1 photo" in summary
    evaluate(page, "(()=>{ const [x, b, send] = window.__held[0]; send.call(x, b); })()")       # let it through
    page.pump(1.5)
    st = evaluate(page, """JSON.stringify([document.getElementById('files').disabled, document.getElementById('drop').hasAttribute('aria-disabled'),
        document.getElementById('pickLabel').textContent, getComputedStyle(document.getElementById('pickLabel')).backgroundColor,
        document.querySelector('#list li').className])""")
    disabled, aria, label, bg, cls = json.loads(st)
    assert not disabled and not aria and label == "Choose more photos" and bg == "rgb(42, 97, 64)" and cls == "bad", st
    assert page.exceptions() == []


@pytest.mark.skipif(BROWSER is None, reason="no Chromium/Chrome installed")
@pytest.mark.parametrize("width,zoom", [(412, 1.0), (360, 1.0), (360, 1.3)])
def test_settings_segments_are_at_least_48_wide(page, server, mf_app, width, zoom):
    import json
    _phone(page, server, mf_app, "admin/settings")
    page.call("Emulation.setDeviceMetricsOverride", {"width": width, "height": 800, "deviceScaleFactor": 1, "mobile": True})
    evaluate(page, f"document.documentElement.style.zoom = '{zoom}'")
    page.pump(0.3)
    w = json.loads(evaluate(page, """JSON.stringify([...document.querySelectorAll('#slideSeg button, #transitionSeg button')]
        .map(b=>[b.textContent.trim(), b.getBoundingClientRect().width, b.scrollWidth <= b.clientWidth + 1]))"""))
    assert len(w) == 7 and all(px >= 48 and fits for _t, px, fits in w), w
    assert evaluate(page, "document.documentElement.scrollWidth <= document.documentElement.clientWidth")


def test_wifi_sheet_warning_keeps_wi_fi_whole(paired, monkeypatch, tmp_path):
    monkeypatch.setenv("MEMORYFRAME_NETMODE_STATE", str(tmp_path / "nm.json"))
    (tmp_path / "nm.json").write_text('{"mode": "product"}')
    src = (TEMPLATES / "wifi.html").read_text()
    sheet = src[src.index('id="confirmStep"'):src.index("</div>\n  </div>", src.index('id="confirmStep"'))]
    assert sheet.count('<span class="nw">Wi-Fi</span>') == 4 and "Wi-Fi" not in sheet.replace('<span class="nw">Wi-Fi</span>', ""), sheet


# ---------------- PHONE-DARK.md (6621607): dark frame theme on every phone page ----------------

DARK_VIEWS = ["admin/", "admin/library", "admin/upload", "admin/settings", "admin/wifi/", "pair", "admin/nope"]
OLD_LIGHT = {"rgb(255, 255, 255)", "rgb(246, 241, 233)", "rgb(227, 238, 230)", "rgb(248, 228, 225)", "rgb(251, 241, 220)",
             "rgb(226, 216, 202)"}
CONTRAST_JS = """JSON.stringify((()=>{
  const lum = c => { const v = c.match(/[\\d.]+/g).slice(0,3).map(Number).map(x=>{x/=255; return x<=0.03928?x/12.92:Math.pow((x+0.055)/1.055,2.4);});
                     return 0.2126*v[0]+0.7152*v[1]+0.0722*v[2]; };
  const alpha = c => { const m = c.match(/[\\d.]+/g); return m.length > 3 ? Number(m[3]) : 1; };
  const bgOf = e => { for(; e; e = e.parentElement){ const b = getComputedStyle(e).backgroundColor; if(alpha(b) === 1) return b; } return 'rgb(14, 19, 17)'; };
  const out = [];
  document.querySelectorAll('body *').forEach(e=>{
    if(!e.offsetParent && getComputedStyle(e).position !== 'fixed') return;
    if(e.closest('.sr-only,[hidden],.msgPreview:not(.empty),.hero') || e.matches('img,input[type=file]')) return;
    const own = [...e.childNodes].some(n=>n.nodeType===3 && n.data.trim()) || (e.matches('input,textarea') && e.value);
    if(!own) return;
    const cs = getComputedStyle(e), a = lum(cs.color), b = lum(bgOf(e));
    const r = (Math.max(a,b)+0.05)/(Math.min(a,b)+0.05);
    if(r < 4.5) out.push([e.tagName + (e.id ? '#' + e.id : '') + '.' + e.className, cs.color, bgOf(e), r.toFixed(2)]);
  });
  return out; })())"""


@pytest.mark.skipif(BROWSER is None, reason="no Chromium/Chrome installed")
def test_dark_theme_every_page_no_light_surfaces_and_text_aa(page, server, mf_app, lib, monkeypatch, tmp_path):
    """Checklist 1, 3, 6, 10: no paper/white surfaces, every visible text >= 4.5:1 (AA) on its real background."""
    import json
    monkeypatch.setenv("MEMORYFRAME_NETMODE_STATE", str(tmp_path / "nm.json"))
    (tmp_path / "nm.json").write_text('{"mode": "product"}')
    lib.add("c.jpg", PNG)
    for path in DARK_VIEWS:
        _phone(page, server, mf_app, path)
        page.pump(0.6)
        states = [None]
        if path == "admin/wifi/" and evaluate(page, "!!document.getElementById('confirmStep')"):
            states.append("document.getElementById('confirmStep').hidden = false")
        if path == "admin/upload":
            states.append("memoryFramePhone.offline('x.jpg did not finish uploading.')")
        if path == "admin/settings":
            states.append("document.querySelector('#aboutSection summary').click()")
        for st in states:
            if st:
                evaluate(page, st)
                page.pump(0.2)
            light = json.loads(evaluate(page, """JSON.stringify([...document.querySelectorAll('body, body *')].filter(e=>e.offsetParent || e===document.body)
                .map(e=>[e.tagName+'.'+e.className, getComputedStyle(e).backgroundColor]))"""))
            bad = [x for x in light if x[1] in OLD_LIGHT]
            assert bad == [], (path, st, bad[:5])
            low = json.loads(evaluate(page, CONTRAST_JS))
            assert low == [], (path, st, low[:6])
    assert page.exceptions() == []


@pytest.mark.skipif(BROWSER is None, reason="no Chromium/Chrome installed")
def test_dark_outlined_buttons_inputs_disabled_and_fills(page, server, mf_app, monkeypatch, tmp_path):
    """Checklist 3, 4, 6, 7: oat 1.5px outlines, forest-800/oat disabled, inputs, sheet + scrim, brick fill vs text."""
    import json
    from memoryframe import message
    monkeypatch.setenv("MEMORYFRAME_NETMODE_STATE", str(tmp_path / "nm.json"))
    (tmp_path / "nm.json").write_text('{"mode": "product"}')
    message.save(mf_app.config["DATA_DIR"], "Hi Nana")
    _phone(page, server, mf_app, "admin/settings")
    g = lambda sel, prop: evaluate(page, f"getComputedStyle(document.querySelector('{sel}'))['{prop}']")   # noqa: E731
    assert g("#frameMessage", "backgroundColor") == "rgb(36, 56, 44)" and g("#frameMessage", "borderTopColor") == "rgb(201, 187, 168)"
    assert g("#frameMessage", "color") == "rgb(243, 237, 228)" and g("#frameMessage", "borderTopWidth") in ("1px", "1.5px")   # 1.5px authored; Chrome snaps to device px
    assert g("#frameMessage", "fontFamily").startswith('"MF Frame"')
    assert g("#messageSave", "backgroundColor") == "rgb(36, 56, 44)" and g("#messageSave", "color") == "rgb(201, 187, 168)"   # Saved
    assert g(".msgRemove", "color") == "rgb(231, 160, 151)"                                        # brick-text, text only
    evaluate(page, "document.getElementById('messageRemove').click()")
    assert g("#messageRemoveYes", "backgroundColor") == "rgb(168, 53, 42)" and g("#messageRemoveYes", "color") == "rgb(255, 255, 255)"
    assert g(".seg button[aria-checked=true]", "backgroundColor") == "rgb(42, 97, 64)"
    assert g(".seg button[aria-checked=true]", "outlineColor") == "rgb(143, 209, 164)"            # leaf selected edge
    assert g("input.switch:checked", "backgroundColor") == "rgb(143, 209, 164)"
    assert g(".bar", "borderBottomColor") == "rgb(47, 74, 58)"
    _phone(page, server, mf_app, "admin/wifi/")
    if evaluate(page, "!!document.getElementById('wifiForm')"):
        evaluate(page, "document.getElementById('confirmStep').hidden = false")
        assert g(".scrim", "backgroundColor") == "rgba(0, 0, 0, 0.6)"
        assert g(".sheet", "backgroundColor") == "rgb(36, 56, 44)" and g(".sheet", "borderTopColor") == "rgb(47, 74, 58)"
        assert g("#cancelBtn", "borderTopColor") == "rgb(201, 187, 168)" and g("#cancelBtn", "borderTopWidth") in ("1px", "1.5px")   # 1.5px authored; Chrome snaps to device px
        assert g("#cancelBtn", "color") == "rgb(243, 237, 228)"
        assert g(".warn", "backgroundColor") == "rgb(58, 44, 34)" and g(".warn", "borderTopColor") == "rgb(214, 179, 106)"
        assert g(".warn", "color") == "rgb(243, 237, 228)"
    _phone(page, server, mf_app, "admin/")
    assert g(".see-all", "borderTopColor") == "rgb(201, 187, 168)" and g(".see-all", "color") == "rgb(143, 209, 164)" or \
        not evaluate(page, "!!document.querySelector('.see-all')")
    assert g(".rows button.row-link", "color") == "rgb(231, 160, 151)"                            # Sign out all phones
    assert g(".store .used", "backgroundColor") == "rgb(143, 209, 164)"
    page.call("Network.clearBrowserCookies")
    page.call("Page.navigate", {"url": server + "pair"})
    page.pump(1.5)
    assert g("#code", "backgroundColor") == "rgb(36, 56, 44)" and g("#code", "borderTopColor") == "rgb(201, 187, 168)"
    assert json.loads(evaluate(page, "JSON.stringify(getComputedStyle(document.querySelector('#code'),'::placeholder').color)")) == "rgb(201, 187, 168)"


@pytest.mark.skipif(BROWSER is None, reason="no Chromium/Chrome installed")
@pytest.mark.parametrize("path,tabs", [("admin/", 8), ("admin/settings", 16), ("admin/upload", 2), ("pair", 3)])
def test_keyboard_focus_rings_are_brass_on_dark(page, server, mf_app, lib, path, tabs):
    """Checklist 5: tab through every control; each focused one draws a >= 3px brass ring (visible on ink)."""
    import json
    lib.add("c.jpg", PNG)
    _phone(page, server, mf_app, path)
    page.pump(0.4)
    seen = []
    for _ in range(tabs):
        page.call("Input.dispatchKeyEvent", {"type": "keyDown", "key": "Tab", "code": "Tab", "windowsVirtualKeyCode": 9})
        page.call("Input.dispatchKeyEvent", {"type": "keyUp", "key": "Tab", "code": "Tab", "windowsVirtualKeyCode": 9})
        page.pump(0.1)
        r = json.loads(evaluate(page, """JSON.stringify((()=>{let e=document.activeElement; if(!e || e===document.body) return null;
            if(e.type==='file') e=e.closest('.pick'); const c=getComputedStyle(e);
            return [e.id||e.className||e.tagName, c.outlineStyle, c.outlineColor, parseFloat(c.outlineWidth)];})())"""))
        if r:
            seen.append(r)
    assert len(seen) >= min(tabs, 2), seen
    bad = [r for r in seen if r[1] == "none" or r[2] != "rgb(214, 179, 106)" or r[3] < 3]
    assert bad == [], bad


def test_dark_tokens_and_outline_rules_in_css():
    """Checklist 3, 4, 11: the PHONE-DARK.md mapping, oat 1.5px outlines, --mf-green only as a fill, flat CSS."""
    tokens = (ROOT / "static" / "tokens.css").read_text()
    block = tokens[tokens.index(".mf-phone {\n"):]
    block = block[:block.index("}")]
    for k, v in (("paper", "ink"), ("card", "forest-800"), ("line", "forest-700"), ("text", "linen"), ("text-2", "oat"),
                 ("green", "forest-600"), ("green-50", "forest-900"), ("accent-text", "leaf"), ("red", "brick"),
                 ("red-text", "brick-text"), ("outline", "oat"), ("focus", "brass")):
        assert re.search(rf"--mf-{k}: var\(--mf-{v}\);", block), k
    assert "--mf-red-50: #3A1F1B;" in block and "--mf-scrim-phone: rgba(0, 0, 0, .6);" in block
    flat = re.sub(r"/\*.*?\*/", "", CSS + tokens[tokens.index("/* ---- Phone"):], flags=re.S)
    assert not re.search(r"(?<![-\w])color: var\(--mf-(green|red)\)", flat)          # fills only
    assert not re.search(r"outline[^;]*var\(--mf-green\)", flat)
    for sel in (r"\.mf-phone \.btn-neutral, \.mf-phone button\.secondary", r"\.mf-phone \.msgKeep", r"\.mf-phone \.field",
                r"\.mf-phone \.code", r"\.mf-phone #frameMessage", r"\.mf-phone \.btn-onbar"):
        assert re.search(sel + r" \{[^}]*border: 1\.5px solid var\(--mf-outline\)", flat), sel
    # white only as text on fills, plus the Library select tick (a ring + check that must read on any photo, per 09-LIBRARY.md)
    assert "#fff;" not in re.sub(r"color: #fff;|border: (2px )?solid #fff;", "", flat)


def test_no_non_breaking_hyphen_and_every_visible_wi_fi_is_wrapped():
    """Wren: no U+2011 anywhere on the phone site; every visible 'Wi-Fi' in the phone templates sits in span.nw."""
    import re
    for name in ("_phone", "admin_home", "settings", "wifi", "wifi_keep"):
        src = (TEMPLATES / f"{name}.html").read_text()
        assert "\u2011" not in src and "&#8209;" not in src, name
        for line in src.splitlines():
            if "<title>" in line or "{% block title" in line or "<!--" in line:
                continue
            assert not re.search(r'(?<!>)Wi-Fi', line), (name, line)
    js = (ROOT / "static" / "phone.js").read_text()
    assert "mfSetText" in js and "innerHTML" not in js     # text built from DOM nodes only
    for name in ("wifi.js", "upload.js"):
        src = (ROOT / "static" / name).read_text()
        assert "innerHTML" not in src and "\u2011" not in src


def test_home_tile_focus_ring_is_inset():
    """Wren's dark re-check M1: the tile clips (overflow:hidden), so the focus ring sits inside it."""
    assert ".mf-phone .grid a:focus-visible { outline-offset: -3px; }" in CSS
    assert "#2a2a2a" not in CSS.lower()                      # tokenised


@pytest.mark.parametrize("url", ["/admin/x", "/pair/x"])
def test_unpaired_error_pages_never_show_the_ssid(client, mf_app, url):
    """Hollis on #77: 404/405 pages inherited the hidden offline panel, which names the SSID."""
    conn = mfdb.connect(mf_app.config["DB_PATH"])
    mfdb.set_setting(conn, "wifi_ssid", "SecretFrameNet")
    mfdb.set_setting(conn, "wifi_password", "password123")
    conn.commit()
    conn.close()
    r = client.get(url, environ_base=PHONE)
    h = r.get_data(as_text=True)
    assert r.status_code == 404 and "mf-phone" in h
    assert "SecretFrameNet" not in h and "password123" not in h and 'id="offline"' not in h
    r = client.post("/admin/library", environ_base=PHONE, headers={"X-CSRF-Token": "x"})
    assert "SecretFrameNet" not in r.get_data(as_text=True)


def test_phone_wifi_is_empty_when_unpaired(client, mf_app):
    conn = mfdb.connect(mf_app.config["DB_PATH"])
    mfdb.set_setting(conn, "wifi_ssid", "SecretFrameNet")
    mfdb.set_setting(conn, "wifi_password", "password123")
    conn.commit()
    conn.close()
    with mf_app.test_request_context("/admin/", environ_base=PHONE):
        assert mf_app.jinja_env.globals.get("phone_wifi") is None
        ctx = {}
        mf_app.update_template_context(ctx)
        assert ctx["phone_wifi"]()["ssid"] == ""


def test_home_storage_meter_is_8px_on_a_forest_700_track():
    flat = re.sub(r"/\*.*?\*/", "", CSS, flags=re.S)
    assert not re.search(r"\.on-dark \.track \{[^}]*(height|background)", flat)
    assert "--mf-line: var(--mf-forest-700)" in (ROOT / "static" / "tokens.css").read_text()
