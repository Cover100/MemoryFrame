"""Restyle PR B part 1 (#70, #65 B): frame overlays, toast and shutdown screen."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CSS = (ROOT / "static" / "style.css").read_text()
HTML = (ROOT / "templates" / "index.html").read_text()


def rule(sel):
    m = re.search(re.escape(sel) + r"\{([^}]*)\}", CSS)
    assert m, sel
    return m.group(1)


def test_ticks_are_svg_not_glyph():
    assert "✓" not in HTML and "&#10003;" not in HTML        # not in the Atkinson subset
    assert HTML.count('class="mf-tick"') == 2                 # paired toast + Wi-Fi updated


def test_dev_toast_flat_and_themed():
    r = rule(".dev-toast")
    assert "box-shadow" not in r and "var(--mf-forest-900)" in r and "#fff" not in r


def test_dev_ring_animates_compositor_only():
    base, filling = rule("#devRing"), rule("#devRing.filling")
    assert "var(--mf-brass)" in base and "opacity:0" in base
    assert re.findall(r"transition:([^;}]*)", filling) == ["opacity 2s linear,transform 2s linear"]
    assert "border-color" not in filling


def test_overlays_use_frame_theme():
    for sel in (".hotspot-error-card", ".wifi-card", "#pairSuccess"):
        r = rule(sel)
        assert "var(--mf-forest-900)" in r and "#fff" not in r, sel
    assert "box-shadow" not in CSS


def test_safe_screen_is_static():
    assert "animation:none" in rule("#safeScreen .safeSpin")
    assert "var(--mf-ink)" in rule("#safeScreen")


def test_dialog_buttons_pinned_to_bottom():
    assert "#devModeBox,#shutdownBox,#confirmBox{width:600px;min-height:272px;padding:28px;display:flex;flex-direction:column}" in CSS
    assert "margin-top:auto" in rule(".shutdownButtons") and "margin-top:auto" in rule(".confirmButtons")
    assert "justify-content:space-between" not in rule("#confirmBox")   # the old no-op rule is gone
    b = rule(".confirmButtons button,.shutdownButtons button")
    assert "width:260px" in b and "font-weight:700" in b


def test_dev_confirm_copy_and_phone_button_font():
    assert "Switch to dev mode?" in HTML and ">Switch</button>" in HTML and "Join developer Wi-Fi?" not in HTML
    assert ".mf-phone button { font: inherit;" in (ROOT / "static" / "tokens.css").read_text()


def _menu(client, mf_app, msg):
    from memoryframe import message
    message.save(mf_app.config["DATA_DIR"], msg or "")
    return client.get("/", environ_base={"REMOTE_ADDR": "127.0.0.1"}).get_data(as_text=True)


def test_menu_v2_message_card_and_empty_state(client, mf_app):
    html = _menu(client, mf_app, None)
    assert '<div class="menuNote empty" id="menuNoteEmpty">' in html and 'id="menuNote" hidden' in html
    for gone in ("menuIp", "menuSsid", "menuSite", "menuPaired", "menuVersion"):
        assert gone not in html
    html = _menu(client, mf_app, "Happy 80th, Nana! <b>xx</b>")
    assert "Happy 80th, Nana! &lt;b&gt;xx&lt;/b&gt;" in html and 'id="menuNoteEmpty" hidden' in html   # text, never HTML
    assert 'role="meter"' in html and html.index('id="menuClose"') < html.index('id="menuShutdown"')
