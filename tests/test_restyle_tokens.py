"""Restyle PR A (DIRECTION.md, #70): design tokens as CSS variables, Atkinson Hyperlegible bundled locally
(woff2 + OFL licence), shared button/card styles. No external URLs; Pi-cheap CSS."""
import re
from pathlib import Path

import pytest

from test_frontend_browser import evaluate, net, page, server  # noqa: F401  (fixtures)

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "static"
TOKENS = (STATIC / "tokens.css").read_text()
FONTS_CSS = (ROOT / "templates" / "frame_fonts.css").read_text()
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
        assert f"static('fonts/atkinson-hyperlegible-{w}.woff2')" in FONTS_CSS
    lic = (STATIC / "fonts" / "OFL.txt").read_text()
    assert "SIL Open Font License" in lic and "Braille Institute" in lic


def test_no_external_urls_in_css_or_templates():
    for f in list(STATIC.glob("*.css")) + list((ROOT / "templates").glob("*.html")) + [ROOT / "templates" / "frame_fonts.css"]:
        s = f.read_text()
        assert not re.search(r"url\(\s*['\"]?(https?:)?//", s), f.name
        assert not re.search(r"(href|src)=\"(https?:)?//", s), f.name
        assert "fonts.googleapis" not in s and "@import" not in s


def test_kiosk_preloads_font_and_links_tokens_first(client):
    html = client.get("/", environ_base=LOCAL).get_data(as_text=True)
    assert re.search(r'<link rel="stylesheet" href="/static/tokens\.css\?v=[0-9a-f]{10}">', html)
    assert html.index("/frame-fonts.css") < html.index("tokens.css") < html.index("style.css")


def test_font_preload_and_font_face_urls_match_and_are_hashed(client):
    """#71: every /static/ URL carries ?v=<hash>, fonts too. The preload and the @font-face src must be the
    same URL (same hash) or Chromium downloads each font twice and the preload is wasted."""
    html = client.get("/", environ_base=LOCAL).get_data(as_text=True)
    preloads = re.findall(r'<link rel="preload" href="([^"]+)" as="font" type="font/woff2" crossorigin>', html)
    r = client.get("/frame-fonts.css", environ_base=LOCAL)
    assert r.status_code == 200 and r.mimetype == "text/css" and r.headers["Cache-Control"] == "no-cache"
    srcs = re.findall(r'src: url\("([^"]+)"\) format\("woff2"\)', r.get_data(as_text=True))
    assert len(preloads) == 2 and preloads == srcs, (preloads, srcs)
    for w, u in zip((400, 700), preloads):
        assert re.fullmatch(rf"/static/fonts/atkinson-hyperlegible-{w}\.woff2\?v=[0-9a-f]{{10}}", u), u
        assert client.get(u, environ_base=LOCAL).data[:4] == b"wOF2"
    assert "@font-face" not in re.sub(r"/\*.*?\*/", "", TOKENS, flags=re.S)   # one source of truth for font URLs


@pytest.mark.parametrize("w", [400, 700])
def test_font_served_locally(client, w):
    r = client.get(f"/static/fonts/atkinson-hyperlegible-{w}.woff2", environ_base=LOCAL)
    assert r.status_code == 200 and r.data[:4] == b"wOF2"
    assert client.get("/static/fonts/OFL.txt", environ_base=LOCAL).status_code == 200


def test_phone_pages_use_tokens(client, paired):
    html = client.get("/admin/", environ_base={"REMOTE_ADDR": "10.42.0.23"}).get_data(as_text=True)
    assert "/static/tokens.css" in html and 'class="mf-phone"' in html


def _rules(sel_regex):
    css = (STATIC / "style.css").read_text()
    return [m.group(0) for m in re.finditer(sel_regex + r"[^{]*\{[^}]*\}", css)]


def test_font_display_block():
    assert FONTS_CSS.count("font-display: block") == 2 and "swap" not in FONTS_CSS


def test_shutdown_cover_and_errors_use_tokens_no_hex():
    rules = _rules(r"#shuttingDown") + _rules(r"\.confirmError") + _rules(r"#shutdownError") + _rules(r"#devModeError")
    assert len(rules) >= 6
    for r in rules:
        assert not re.search(r"#[0-9a-fA-F]{3,8}\b(?![-\w])", r.split("{", 1)[1]), r
    css = (STATIC / "style.css").read_text()
    assert "ff8a80" not in css.lower() and css.count("var(--mf-brick-text)") >= 3


def test_menu_buttons_use_shared_classes(client):
    html = client.get("/", environ_base=LOCAL).get_data(as_text=True)
    for i, c in (("menuPair", "mf-btn"), ("menuSignOut", "mf-btn mf-btn-secondary"),
                 ("menuShutdown", "mf-btn mf-btn-danger"), ("menuClose", "mf-btn mf-btn-secondary mf-btn-default")):
        assert f'id="{i}" class="{c}"' in html
    css = (STATIC / "style.css").read_text()
    assert ".overlay button.mf-btn{min-height:var(--mf-touch-frame)" in css
    assert "#confirmCancel,#shutdownCancel,#devModeCancel{outline:var(--mf-ring-width) solid var(--mf-brass)" in css


def test_menu_buttons_render_in_palette(page, server, net):
    page.pump(2.5)
    evaluate(page, "memoryFramePairing.openMenu()")
    got = evaluate(page, "['menuPair','menuSignOut','menuShutdown','menuClose'].map(i=>{const e=document.getElementById(i);"
                         "const c=getComputedStyle(e);return [c.backgroundColor, Math.round(e.getBoundingClientRect().height)>=56];})")
    assert got == [["rgb(42, 97, 64)", True], ["rgb(36, 56, 44)", True], ["rgb(168, 53, 42)", True], ["rgb(36, 56, 44)", True]]


def test_kiosk_loads_frame_font_once_each(page, server, net):
    page.pump(2.5)
    evaluate(page, "memoryFramePairing.openMenu()")      # menu text uses the frame font
    page.pump(1.0)
    ok, fonts = evaluate(page, "[document.fonts.check('700 22px \"MF Frame\"'),"
                               "performance.getEntriesByType('resource').map(e=>new URL(e.name).pathname)"
                               ".filter(n=>n.endsWith('.woff2'))]")
    assert ok
    for w in (400, 700):   # preload and @font-face share one URL, so at most one fetch per font
        assert fonts.count(f"/static/fonts/atkinson-hyperlegible-{w}.woff2") <= 1, fonts
    assert "/static/fonts/atkinson-hyperlegible-700.woff2" in fonts


# ---- #83: declare the dark colour scheme so force-dark browsers (Samsung Internet) leave the colours alone ----
def _phone_pages(client, paired):
    lan = {"REMOTE_ADDR": "10.42.0.23"}
    pages = [client.get(u, environ_base=lan) for u in
             ("/admin/", "/admin/upload", "/admin/library", "/admin/settings", "/admin/wifi/",
              "/admin/nope")]
    pages.append(client.get("/pair", environ_base=lan))
    pages.append(client.post("/pair", data={"code": "x"}, environ_base=lan))
    return pages


def test_every_phone_page_declares_dark_scheme_and_theme_colour(client, paired):
    pages = _phone_pages(client, paired)
    assert len(pages) >= 8
    for r in pages:
        html = r.get_data(as_text=True)
        assert 'class="mf-phone"' in html, r.request.path
        assert '<meta name="color-scheme" content="dark">' in html, r.request.path
        assert '<meta name="supported-color-schemes" content="dark">' in html, r.request.path
        assert '<meta name="theme-color" content="#16261D">' in html, r.request.path


def test_unpaired_phone_pages_declare_dark_scheme(client):
    lan = {"REMOTE_ADDR": "10.42.0.23"}
    for u in ("/pair", "/admin/nope", "/pair/nope"):
        html = client.get(u, environ_base=lan).get_data(as_text=True)
        assert '<meta name="color-scheme" content="dark">' in html, u


def test_theme_colour_is_the_header_token():
    css = (STATIC / "tokens.css").read_text()
    forest900 = re.search(r"--mf-forest-900:\s*(#[0-9A-Fa-f]{6})", css).group(1).upper()
    tmpl = (STATIC.parent / "templates" / "_phone.html").read_text()
    assert re.search(r'name="theme-color" content="%s"' % forest900, tmpl)
    phone = (STATIC / "phone.css").read_text()
    assert re.search(r"\.mf-phone \.bar \{[^}]*background: var\(--mf-forest-900\)", phone)


def test_phone_css_root_is_dark_and_painted():
    phone = (STATIC / "phone.css").read_text()
    root = re.search(r"(?m)^:root \{([^}]*)\}", phone).group(1)
    assert "color-scheme: dark" in root
    assert "background-color: var(--mf-ink)" in root
    assert "style=" not in (STATIC.parent / "templates" / "_phone.html").read_text()   # CSP: no inline style


def test_kiosk_page_is_not_touched():
    kiosk = (STATIC.parent / "templates" / "index.html").read_text()
    assert "color-scheme" not in kiosk
    assert ":root { color-scheme" not in (STATIC / "style.css").read_text()
