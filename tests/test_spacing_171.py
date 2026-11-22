"""#171: Settings card gap above Paired phones, Upload intro spacing, and the
frame-message placeholder. Guards are template/CSS token reads plus computed
style at phone widths, in the same style as the restyle phone tests."""
import re
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from memoryframe import auth, db as mfdb
from test_frontend_browser import BROWSER, evaluate, page, server  # noqa: F401

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "static"
TOKENS = (STATIC / "tokens.css").read_text()
PHONE_CSS = (STATIC / "phone.css").read_text()
VIDEO_CSS = (STATIC / "video.css").read_text()
SETTINGS = (ROOT / "templates" / "settings.html").read_text()
UPLOAD = (ROOT / "templates" / "upload.html").read_text()


def _flat(css):
    return re.sub(r"\s+", " ", re.sub(r"/\*.*?\*/", "", css, flags=re.S))


def test_shared_spacing_tokens_exist():
    flat = _flat(TOKENS)
    for token, value in (
        ("--mf-s-page-y", "20px"),
        ("--mf-s-page-x", "var(--mf-s-4)"),
        ("--mf-s-page-bottom", "var(--mf-s-6)"),
        ("--mf-s-page-dense-y", "var(--mf-s-3)"),
        ("--mf-s-copy", "14px"),
        ("--mf-s-card", "var(--mf-s-4)"),
        ("--mf-s-card-dense", "10px"),
    ):
        assert f"{token}: {value}" in flat, token


def test_about_uses_the_shared_card_stack_gap():
    """Root cause: About is details.rows.about, not a .card, so it had no
    margin-bottom and sat flush on Paired phones. Both now share the card tokens."""
    flat = _flat(PHONE_CSS)
    assert "margin: 0 0 var(--mf-s-card)" in flat
    assert "main.dense .card { margin-bottom: var(--mf-s-card-dense)" in flat
    assert "details.about { padding: 0; margin-top: 0; margin-bottom: var(--mf-s-card); }" in flat
    assert "main.dense details.about { margin-bottom: var(--mf-s-card-dense); }" in flat
    assert '<details class="rows about" id="aboutSection">' in SETTINGS
    assert '<section class="card" id="phonesSection">' in SETTINGS


def test_upload_intro_uses_shared_copy_spacing_not_one_off_pixels():
    """upnote used margin: 8px 2px 0 (no token, no gap before the storage card).
    It now uses the same --mf-s-copy bottom margin as other page intro paragraphs,
    and main's --mf-s-page-y for the space under the header divider."""
    flat_v = _flat(VIDEO_CSS)
    flat_p = _flat(PHONE_CSS)
    assert ".mf-phone .upnote {" in flat_v
    assert "margin: 0 0 var(--mf-s-copy)" in flat_v
    assert "8px 2px 0" not in VIDEO_CSS
    assert "padding: var(--mf-s-page-y) var(--mf-s-page-x) var(--mf-s-page-bottom)" in flat_p
    assert "p { margin: 0 0 var(--mf-s-copy); }" in flat_p
    assert '<p class="upnote">' in UPLOAD


def test_frame_message_placeholder_is_exactly_type_a_message():
    assert 'placeholder="Type a message"' in SETTINGS
    assert SETTINGS.count("placeholder=") == 1
    assert "e.g. Happy birthday" not in SETTINGS
    assert "Love from all of us" not in SETTINGS


def _phone(page, server, mf_app, path, width=412, height=915):
    conn = mfdb.connect(mf_app.config["DB_PATH"])
    token = auth.create_session(conn, "test phone")
    conn.close()
    page.call("Emulation.setDeviceMetricsOverride",
              {"width": width, "height": height, "deviceScaleFactor": 1, "mobile": True})
    page.call("Network.enable")
    page.call("Network.setCookie", {"name": "mf_session", "value": token,
                                    "domain": urlsplit(server).hostname,
                                    "path": "/", "httpOnly": True, "sameSite": "Strict"})
    page.call("Page.navigate", {"url": server + path})
    page.pump(2)


GAPS = """JSON.stringify((()=>{
  const box = el => { const r = el.getBoundingClientRect(); return {t:r.top,b:r.bottom}; };
  const cards = [...document.querySelectorAll('main > .card')];
  const about = document.getElementById('aboutSection');
  const phones = document.getElementById('phonesSection');
  const cardGap = cards[1].getBoundingClientRect().top - cards[0].getBoundingClientRect().bottom;
  return {
    cardGap,
    aboutPhones: phones.getBoundingClientRect().top - about.getBoundingClientRect().bottom,
    aboutMargin: getComputedStyle(about).marginBottom,
    cardMargin: getComputedStyle(cards[0]).marginBottom,
    placeholder: document.getElementById('frameMessage').getAttribute('placeholder'),
  };
})())"""


INTRO = """JSON.stringify((()=>{
  const bar = document.querySelector('header.bar').getBoundingClientRect();
  const note = document.querySelector('.upnote');
  const card = document.getElementById('storage').getBoundingClientRect();
  const r = note.getBoundingClientRect();
  const cs = getComputedStyle(note);
  const main = getComputedStyle(document.querySelector('main'));
  return {
    top: r.top - bar.bottom,
    bottom: card.top - r.bottom,
    margin: cs.margin,
    marginBottom: cs.marginBottom,
    mainPadTop: main.paddingTop,
  };
})())"""


@pytest.mark.skipif(BROWSER is None, reason="no Chromium/Chrome installed")
@pytest.mark.parametrize("width", [360, 412])
def test_settings_about_gap_matches_other_cards_and_placeholder(page, server, mf_app, width):
    import json
    _phone(page, server, mf_app, "admin/settings", width=width)
    got = json.loads(evaluate(page, GAPS))
    assert got["placeholder"] == "Type a message", got
    assert got["aboutMargin"] == got["cardMargin"] == "10px", got
    assert got["aboutPhones"] == pytest.approx(got["cardGap"], abs=0.5), got
    assert got["cardGap"] == pytest.approx(10, abs=0.5), got


@pytest.mark.skipif(BROWSER is None, reason="no Chromium/Chrome installed")
@pytest.mark.parametrize("width", [360, 412])
def test_upload_intro_matches_shared_page_and_copy_spacing(page, server, mf_app, width):
    import json
    mf_app.config["VIDEOS"] = True
    _phone(page, server, mf_app, "admin/upload", width=width)
    got = json.loads(evaluate(page, INTRO))
    assert got["mainPadTop"] == "20px", got
    assert got["marginBottom"] == "14px", got
    assert got["top"] == pytest.approx(20, abs=0.5), got
    assert got["bottom"] == pytest.approx(14, abs=0.5), got
