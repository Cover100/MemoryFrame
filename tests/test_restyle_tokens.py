"""Restyle PR A (DIRECTION.md, #70): design tokens as CSS variables, Atkinson Hyperlegible bundled locally
(woff2 + OFL licence), shared button/card styles. No external URLs; Pi-cheap CSS."""
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "static"
TOKENS = (STATIC / "tokens.css").read_text()
LOCAL = {"REMOTE_ADDR": "127.0.0.1"}

DIRECTION = {"--mf-ink": "#0E1311", "--mf-forest-900": "#16261D", "--mf-forest-800": "#24382C",
             "--mf-forest-700": "#2F4A3A", "--mf-forest-600": "#2A6140", "--mf-brick": "#A8352A",
             "--mf-brass": "#D6B36A", "--mf-walnut-900": "#3A2C22", "--mf-walnut": "#5A4535",
             "--mf-linen": "#F3EDE4", "--mf-oat": "#C9BBA8", "--mf-leaf": "#8FD1A4", "--mf-brick-text": "#E7A097",
             "--mf-paper": "#F6F1E9", "--mf-card": "#FFFFFF", "--mf-line": "#E2D8CA", "--mf-text": "#1F2A24",
             "--mf-text-2": "#5E5146", "--mf-green": "#2A6140", "--mf-green-50": "#E3EEE6", "--mf-red": "#A8352A",
             "--mf-red-50": "#F8E4E1", "--mf-warn-bg": "#FBF1DC", "--mf-warn-text": "#6E4A10", "--mf-brown": "#6B4F3A"}


@pytest.mark.parametrize("name,hexv", DIRECTION.items())
def test_tokens_match_direction(name, hexv):
    assert re.search(rf"{re.escape(name)}:\s*{hexv}\b", TOKENS, re.I), name


def test_scale_and_shape_tokens():
    flat = re.sub(r"\s+", " ", TOKENS)
    for t in ("--mf-f-body: 22px", "--mf-f-small: 18px", "--mf-r-lg: 16px", "--mf-touch-frame: 56px", "--mf-s-7: 48px"):
        assert t in flat, t


def test_shared_components_and_pi_rules():
    for c in (".mf-card", ".mf-btn", ".mf-btn-secondary", ".mf-btn-danger", ".mf-btn-accent", ".mf-btn-default", ".mf-phone"):
        assert c in TOKENS
    for css in (TOKENS, (STATIC / "style.css").read_text()):
        assert "backdrop-filter" not in css and "filter: blur" not in css and "filter:blur" not in css
    code = re.sub(r"/\*.*?\*/", "", TOKENS, flags=re.S)
    assert "gradient(" not in code and "box-shadow" not in code


def test_font_files_local_small_and_licensed():
    for w in (400, 700):
        f = STATIC / "fonts" / f"atkinson-hyperlegible-{w}.woff2"
        assert f.read_bytes()[:4] == b"wOF2" and f.stat().st_size < 40_000
        assert f'url("/static/fonts/atkinson-hyperlegible-{w}.woff2")' in TOKENS
    lic = (STATIC / "fonts" / "OFL.txt").read_text()
    assert "SIL Open Font License" in lic and "Braille Institute" in lic


def test_no_external_urls_in_css_or_templates():
    for f in list(STATIC.glob("*.css")) + list((ROOT / "templates").glob("*.html")):
        s = f.read_text()
        assert not re.search(r"url\(\s*['\"]?(https?:)?//", s), f.name
        assert not re.search(r"(href|src)=\"(https?:)?//", s), f.name
        assert "fonts.googleapis" not in s and "@import" not in s


def test_kiosk_preloads_font_and_links_tokens_first(client):
    html = client.get("/", environ_base=LOCAL).get_data(as_text=True)
    assert 'rel="preload" href="/static/fonts/atkinson-hyperlegible-400.woff2" as="font"' in html
    assert html.index("tokens.css") < html.index("style.css")


@pytest.mark.parametrize("w", [400, 700])
def test_font_served_locally(client, w):
    r = client.get(f"/static/fonts/atkinson-hyperlegible-{w}.woff2", environ_base=LOCAL)
    assert r.status_code == 200 and r.data[:4] == b"wOF2"
    assert client.get("/static/fonts/OFL.txt", environ_base=LOCAL).status_code == 200


def test_phone_pages_use_tokens(client, paired):
    html = client.get("/admin/", environ_base={"REMOTE_ADDR": "10.42.0.23"}).get_data(as_text=True)
    assert "/static/tokens.css" in html and 'class="mf-phone"' in html
