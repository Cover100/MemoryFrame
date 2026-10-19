"""Restyle polish follow-up: Wren's #76 nice-to-haves (frame) and #77 re-check leftovers N2/N4/N5/N6 (phone)."""
import json
import re
import time
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from memoryframe import auth, db as mfdb, wifi
from test_frontend_browser import BROWSER, evaluate, net, page, server, visible  # noqa: F401  (fixtures)
from test_wifi_change import NEW_PW, helper  # noqa: F401  (fixture)

ROOT = Path(__file__).resolve().parents[1]
STYLE = (ROOT / "static" / "style.css").read_text()
PHONE_CSS = (ROOT / "static" / "phone.css").read_text()
TOKENS = (ROOT / "static" / "tokens.css").read_text()
PHONE = {"REMOTE_ADDR": "10.42.0.23"}
PNG = (ROOT / "tests" / "fixture_astronaut.jpg").read_bytes()
HIDE_UNDER = ("body:has(.overlay.visible,#shuttingDown:not([hidden]),#safeScreen:not([hidden]),.hotspot-error:not([hidden]),"
              ".wifi-overlay:not([hidden]),.dev-toast:not([hidden]))")


def _flat(css):
    return re.sub(r"/\*.*?\*/", "", css, flags=re.S)


# ---------------- frame (Wren's #76 review nice-to-haves) ----------------

def test_no_text_shadow_left_on_the_frame():
    flat = _flat(STYLE)
    assert "text-shadow" not in flat                              # DIRECTION: no shadows (was #clockOverlay)
    clock = re.search(r"#clockOverlay\s*\{([^}]*)\}", flat).group(1)
    assert "var(--mf-pill)" in clock and "display: none" in clock  # flat pill instead; still hidden by default


def test_site_only_caption_is_balanced():
    assert re.search(r"\.codeWrap \.stepText\{text-wrap:balance\}", STYLE)


def test_pair_hint_hides_under_the_same_overlays_as_the_dev_banner():
    flat = _flat(STYLE)
    assert HIDE_UNDER + " .dev-banner{opacity:0}" in flat
    assert HIDE_UNDER + " #pairHint{opacity:0}" in flat
    assert "#pairHint{" in flat and "transition" not in re.search(r"#pairHint\{([^}]*)\}", flat).group(1)


def test_pair_hint_hidden_behind_shutdown_confirm_and_back_after(page, server, lib, net, backlight):
    lib.add("p.jpg", PNG)                                        # photos, no phone paired -> hint shows
    page.call("Page.reload", {})
    page.pump(3)
    op = "getComputedStyle(document.getElementById('pairHint')).opacity"
    assert visible(page, "pairHint") and evaluate(page, op) == "1"
    evaluate(page, "memoryFramePairing.openMenu()")
    page.pump(0.5)
    assert evaluate(page, op) == "0"                             # menu
    evaluate(page, "document.getElementById('menuShutdown').click()")
    page.pump(0.5)
    assert evaluate(page, "document.getElementById('shutdownOverlay').classList.contains('visible')")
    assert evaluate(page, op) == "0"                             # shutdown confirm
    evaluate(page, "document.getElementById('shutdownCancel').click()")
    page.pump(0.5)
    assert evaluate(page, op) == "1"
    assert page.exceptions() == []


def test_site_only_caption_never_leaves_code_alone(page, server, mf_app, net, monkeypatch):
    net.update(mode="lan", lan_ip="192.168.50.124", ips=["192.168.50.124"])
    monkeypatch.setenv("MEMORYFRAME_PORTS", "5000")
    page.pump(2.5)
    evaluate(page, "memoryFramePairing.openMenu()")
    page.pump(0.5)
    evaluate(page, "document.getElementById('menuPair').click()")
    page.pump(1.5)
    assert evaluate(page, "document.getElementById('wifiStep').hidden")         # site-only
    widths = json.loads(evaluate(page, "(()=>{const e=document.querySelector('#codeStep .stepText');const r=document.createRange();"
                                       "r.selectNodeContents(e);return JSON.stringify([...r.getClientRects()].map(x=>x.width));})()"))
    assert len(widths) == 2 and min(widths) > 0.5 * max(widths), widths   # balanced, not "... a / code"


# ---------------- phone (Wren's #77 re-check leftovers) ----------------

def test_n2_last_hard_coded_colours_are_tokens():
    flat = _flat(PHONE_CSS)
    assert "#2a2a2a" not in flat.lower() and "rgba(" not in flat and "rgb(" not in flat
    assert "border: 3px solid var(--mf-forest-800)" in flat
    assert "background: var(--mf-scrim-phone)" in flat
    assert re.search(r"--mf-scrim-phone:\s*rgba\(31, 42, 36, 0\.55\);", TOKENS)
    others = set(re.findall(r"#[0-9a-fA-F]{3,6}\b", flat))
    assert others <= {"#fff"}, others                              # white on fills only (matches tokens.css)


def test_n4_delete_is_a_separate_red_text_button():
    js = (ROOT / "static" / "library.js").read_text()
    assert 'del.className = "del"' in js and "actions.append(show, hide, del)" in js
    rule = re.search(r"\.mf-phone #grid \.actions button\.del \{([^}]*)\}", _flat(PHONE_CSS)).group(1)
    for part in ("flex: 1 0 100%", "background: none", "border: 0", "border-top: 1px solid var(--mf-line)", "color: var(--mf-red)"):
        assert part in rule, part
    assert "button:last-child" not in PHONE_CSS


@pytest.mark.parametrize("left,q", [(300, 4), (299, 4), (226, 4), (225, 3), (151, 3), (150, 2), (76, 2), (75, 1), (1, 1),
                                    (0.4, 1), (0, 0), (-5, 0)])
def test_n6_ring_quarters(left, q):
    assert wifi._ring_q(left, 300) == q


def _pending(app, left):
    conn = mfdb.connect(app.config["DB_PATH"])
    conn.execute("INSERT OR REPLACE INTO wifi_change (id, state, old_ssid, new_ssid, new_password, old_profile, "
                 "new_profile, deadline, updated) VALUES (1, 'pending', 'Old', 'Garden Frame', 'pw12345678', "
                 "'a', 'b', ?, ?)", (time.time() + left, time.time()))
    conn.commit()
    conn.close()


@pytest.mark.parametrize("left,q", [(290, 4), (200, 3), (130, 2), (40, 1)])
def test_n6_keep_page_ring_tracks_time_on_each_refresh(paired, mf_app, left, q):
    _pending(mf_app, left)
    h = paired.get("/admin/wifi/keep", environ_base=PHONE).get_data(as_text=True)
    assert f'<div class="ring q{q}" aria-hidden="true">' in h
    assert '<meta http-equiv="refresh" content="5">' in h          # the existing 5 s refresh moves it on


def test_n6_ring_css_quarters_static():
    flat = _flat(PHONE_CSS)
    for n in (1, 2, 3, 4):
        assert f".ring.q{n}::before" in flat
    ring = flat[flat.index(".mf-phone .ring {"):flat.index(".mf-phone .done-ico")]
    assert "animation" not in ring and "transition" not in ring and "@keyframes" not in flat


def test_n5_sheet_locks_the_page_scroll_source():
    js = (ROOT / "static" / "wifi.js").read_text()
    assert js.count('document.body.classList.add("sheet-open")') == 1
    assert js.count('document.body.classList.remove("sheet-open")') == 1
    assert "body.mf-phone.sheet-open { overflow: hidden; }" in PHONE_CSS


def _phone(page, server, mf_app, path, w=412, h=915):
    conn = mfdb.connect(mf_app.config["DB_PATH"])
    token = auth.create_session(conn, "test phone")
    conn.close()
    page.call("Emulation.setDeviceMetricsOverride", {"width": w, "height": h, "deviceScaleFactor": 1, "mobile": True})
    page.call("Network.enable")
    page.call("Network.setCookie", {"name": "mf_session", "value": token, "domain": urlsplit(server).hostname,
                                    "path": "/", "httpOnly": True, "sameSite": "Strict"})
    page.call("Page.navigate", {"url": server + path})
    page.pump(2)


@pytest.mark.skipif(BROWSER is None, reason="no Chromium/Chrome installed")
def test_n5_background_does_not_scroll_under_the_wifi_sheet(page, server, mf_app, net, helper):
    _phone(page, server, mf_app, "admin/wifi", h=380)               # short viewport so the form can scroll
    js = "document.getElementById"
    assert evaluate(page, "document.scrollingElement.scrollHeight > innerHeight")
    evaluate(page, f"{js}('ssid').value='Frame Home'; {js}('password').value='{NEW_PW}'; {js}('changeBtn').click()")
    page.pump(0.3)
    assert not evaluate(page, f"{js}('confirmStep').hidden")
    assert evaluate(page, "getComputedStyle(document.body).overflowY") == "hidden"
    evaluate(page, "scrollTo(0, 0)")
    page.call("Input.dispatchMouseEvent", {"type": "mouseWheel", "x": 200, "y": 100, "deltaX": 0, "deltaY": 600})
    page.pump(0.5)
    assert evaluate(page, "scrollY") == 0
    page.call("Input.dispatchKeyEvent", {"type": "keyDown", "key": "Escape", "code": "Escape", "windowsVirtualKeyCode": 27})
    page.pump(0.3)
    assert evaluate(page, f"{js}('confirmStep').hidden")
    assert evaluate(page, "getComputedStyle(document.body).overflowY") == "visible"
    assert "sheet-open" not in evaluate(page, "document.body.className")
    assert page.exceptions() == []


@pytest.mark.skipif(BROWSER is None, reason="no Chromium/Chrome installed")
def test_n4_library_actions_row(page, server, mf_app, lib):
    lib.add("c.jpg", PNG)
    _phone(page, server, mf_app, "admin/library")
    page.pump(1)
    st = json.loads(evaluate(page, """(()=>{const li=document.querySelector('#grid li');
        const [s,h,d]=li.querySelectorAll('.actions button'); const c=getComputedStyle(d);
        const rs=s.getBoundingClientRect(), rd=d.getBoundingClientRect(), card=li.getBoundingClientRect();
        return JSON.stringify({text:d.textContent, cls:d.className, bg:c.backgroundColor, color:c.color,
          bl:c.borderLeftWidth, bt:c.borderTopWidth, below: rd.top >= rs.bottom, h: rd.height,
          full: Math.abs(rd.left-card.left) <= 2 && Math.abs(rd.right-card.right) <= 2,
          showBorder:getComputedStyle(s).borderLeftWidth});})()"""))
    assert st["text"] == "Delete" and st["cls"] == "del" and st["below"] and st["full"] and st["h"] >= 48, st
    assert st["bg"] == "rgba(0, 0, 0, 0)" and st["bl"] == "0px" and st["bt"] == "1px", st
    assert st["color"] == "rgb(168, 53, 42)" and float(st["showBorder"][:-2]) >= 1, st   # --mf-red; Show now/Hide stay outlined


# ---------------- Juniper's #77 security review: offline-panel SSID ----------------

def _wifi_hint(mf_app, cookie=None):
    from flask import render_template_string
    headers = {"Cookie": f"mf_session={cookie}"} if cookie else {}
    with mf_app.test_request_context("/admin/", headers=headers, environ_base=PHONE):
        return render_template_string("{% set w = phone_wifi() %}[{{ w.ssid }}]")


@pytest.fixture
def product(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMORYFRAME_SSID", "Secret-Hotspot-4F2A")
    monkeypatch.setenv("MEMORYFRAME_NETMODE_STATE", str(tmp_path / "nm.json"))
    (tmp_path / "nm.json").write_text('{"mode": "product"}')
    return tmp_path / "nm.json"


def test_ssid_lookup_is_empty_for_an_unpaired_visitor(mf_app, product):
    assert _wifi_hint(mf_app) == "[]"                                    # no cookie
    assert _wifi_hint(mf_app, "not-a-real-token") == "[]"                # bogus cookie
    conn = mfdb.connect(mf_app.config["DB_PATH"])
    tok = auth.create_session(conn, "phone")
    conn.close()
    assert _wifi_hint(mf_app, tok) == "[Secret-Hotspot-4F2A]"            # paired: the name to rejoin
    conn = mfdb.connect(mf_app.config["DB_PATH"])
    auth.sign_out_all(conn)
    conn.close()
    assert _wifi_hint(mf_app, tok) == "[]"                               # revoked session: nothing again


def test_unpaired_pages_never_carry_the_ssid(client, product):
    for url in ("/pair", "/pair?t=x", "/admin/", "/admin/settings"):
        r = client.get(url, environ_base=PHONE)
        assert "Secret-Hotspot-4F2A" not in r.get_data(as_text=True), url


def test_dev_mode_offline_hint_does_not_name_the_hotspot(paired, product):
    h = offline_panel(paired, "/admin/")
    assert "Join <b>Secret-Hotspot-4F2A</b>" in h                         # product mode: named
    product.write_text('{"mode": "dev"}')
    for url in ("/admin/", "/admin/settings", "/admin/upload", "/admin/library"):
        h = offline_panel(paired, url)
        assert "Secret-Hotspot-4F2A" not in h, url
        assert "<li>Join the frame's Wi-Fi.</li>" in h and "scan the Wi-Fi QR" not in h, url
    product.unlink()                                                     # no netmode file = dev (safe default)
    assert "Secret-Hotspot-4F2A" not in offline_panel(paired, "/admin/")


def offline_panel(c, url):
    h = body_ok(c, url)
    return h[h.index('<div id="offline" hidden>'):h.index('id="offlineRetry"')]


def body_ok(c, url):
    r = c.get(url, environ_base=PHONE)
    assert r.status_code == 200, (url, r.status_code)
    return r.get_data(as_text=True)
