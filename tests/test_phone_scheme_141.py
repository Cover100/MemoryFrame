"""#141: the phone follows the phone's own light/dark setting with Wren's "Warm paper" map (light-tokens.md, 11 Nov 2026).
Dark (the default) must stay exactly the PHONE-DARK.md look; light values live only under prefers-color-scheme: light;
media overlays stay dark in both; the frame never reads a phone token."""
import json
import re
from pathlib import Path

import pytest

from test_frontend_browser import BROWSER, evaluate, page, server  # noqa: F401  (fixtures)
from test_restyle_c_phone import PNG, _phone, _scheme

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "static"
TOKENS = (STATIC / "tokens.css").read_text()
PHONE_FILES = ("phone.css", "video.css", "trim.css", "homewifi.css")
_FLAT = lambda s: re.sub(r"/\*.*?\*/", "", s, flags=re.S)   # noqa: E731

# The colours each phone use resolved to before #141 (main 9b438aa: .mf-phone remap / literal), i.e. today's dark look.
PREVIOUS = {
    "--mfp-bg": "#0E1311", "--mfp-surface": "#24382C", "--mfp-header": "#16261D", "--mfp-header-text": "#F3EDE4",
    "--mfp-strip": "#3A2C22", "--mfp-strip-text": "#C9BBA8", "--mfp-line": "#2F4A3A", "--mfp-outline": "#C9BBA8",
    "--mfp-text": "#F3EDE4", "--mfp-text-2": "#C9BBA8", "--mfp-accent": "#2A6140", "--mfp-on-accent": "#FFFFFF",
    "--mfp-accent-text": "#8FD1A4", "--mfp-ok": "#8FD1A4", "--mfp-success-bg": "#16261D", "--mfp-danger": "#A8352A",
    "--mfp-danger-text": "#E7A097", "--mfp-danger-bg": "#3A1F1B", "--mfp-warn-bg": "#3A2C22", "--mfp-warn-line": "#D6B36A",
    "--mfp-warn-text": "#F3EDE4", "--mfp-focus": "#D6B36A", "--mfp-gold": "#D6B36A", "--mfp-track": "#2F4A3A",
    "--mfp-scrim": "rgba(0, 0, 0, .6)", "--mfp-tile-ring-fill": "rgba(14, 19, 17, .5)",
    "--mfp-on-gold": "#0E1311", "--mfp-panel": "#16261D", "--mfp-tile": "#16261D",
    "--mfp-media-veil": "rgba(14, 19, 17, .72)", "--mfp-media-pill": "rgba(14, 19, 17, .82)", "--mfp-media-bg": "#0E1311",
    "--mfp-media-black": "#000", "--mfp-media-text": "#F3EDE4", "--mfp-media-text-2": "#C9BBA8",
    "--mfp-media-line": "#C9BBA8", "--mfp-media-track": "#2F4A3A", "--mfp-media-ok": "#8FD1A4",
    "--mfp-media-danger-text": "#E7A097", "--mfp-note-bg": "#3A2C22", "--mfp-note-line": "#5A4535",
    "--mfp-note-text": "#F3EDE4", "--mfp-note-quote": "#D6B36A", "--mfp-illo-wood": "#5A4535",
    "--mfp-illo-screen": "#0E1311", "--mfp-illo-rim": "#24382C", "--mfp-illo-panel": "#16261D",
}
# Wren's light column, exactly (light-tokens.md "Token map"), plus the three flagged additions (last line)
LIGHT = {
    "--mfp-bg": "#F6F1E9", "--mfp-surface": "#FFFFFF", "--mfp-header": "#FFFFFF", "--mfp-header-text": "#1F2A24",
    "--mfp-strip": "#EDE4D7", "--mfp-strip-text": "#5E5146", "--mfp-line": "#E2D8CA", "--mfp-outline": "#857565",
    "--mfp-text": "#1F2A24", "--mfp-text-2": "#5E5146", "--mfp-accent": "#2A6140", "--mfp-on-accent": "#FFFFFF",
    "--mfp-accent-text": "#2A6140", "--mfp-ok": "#2F7A4B", "--mfp-success-bg": "#E3EEE6", "--mfp-danger": "#A8352A",
    "--mfp-danger-text": "#A8352A", "--mfp-danger-bg": "#F8E4E1", "--mfp-warn-bg": "#FBF1DC", "--mfp-warn-line": "#8A6A1E",
    "--mfp-warn-text": "#6E4A10", "--mfp-focus": "#8A6A1E", "--mfp-gold": "#8A6A1E", "--mfp-track": "#E2D8CA",
    "--mfp-scrim": "rgba(31, 42, 36, .45)",
    "--mfp-on-gold": "#FFFFFF", "--mfp-panel": "#FFFFFF", "--mfp-tile": "#EDE4D7",
}
SAME_IN_BOTH = {k for k in PREVIOUS if k.startswith(("--mfp-media-", "--mfp-note-", "--mfp-illo-"))} | {"--mfp-tile-ring-fill"}


def _block(css, start):
    i = css.index(start) + len(start)
    return css[i:css.index("}", i)]


def _defs(block):
    return dict((k, " ".join(v.split())) for k, v in re.findall(r"(--mfp-[\w-]+):\s*([^;]+);", block))


DARK_DEFS = _defs(_block(_FLAT(TOKENS), ".mf-phone {"))
LIGHT_MEDIA = _FLAT(TOKENS)[_FLAT(TOKENS).index("@media (prefers-color-scheme: light)"):]
LIGHT_DEFS = _defs(_block(LIGHT_MEDIA, ".mf-phone {"))


def test_dark_values_are_exactly_the_previous_phone_colours():
    """Token equivalence: every dark --mfp-* is what that use resolved to before #141, so dark renders identically."""
    assert DARK_DEFS == PREVIOUS
    frame = dict(re.findall(r"(--mf-[\w-]+):\s*(#[0-9A-Fa-f]{6})", TOKENS[:TOKENS.index("/* ---- Phone: follows")]))
    for k, frame_tok in (("bg", "ink"), ("surface", "forest-800"), ("header", "forest-900"), ("header-text", "linen"),
                         ("strip", "walnut-900"), ("strip-text", "oat"), ("line", "forest-700"), ("outline", "oat"),
                         ("text", "linen"), ("text-2", "oat"), ("accent", "forest-600"), ("accent-text", "leaf"),
                         ("ok", "leaf"), ("success-bg", "forest-900"), ("danger", "brick"), ("danger-text", "brick-text"),
                         ("warn-bg", "walnut-900"), ("warn-line", "brass"), ("warn-text", "linen"), ("focus", "brass"),
                         ("gold", "brass"), ("track", "forest-700")):
        assert DARK_DEFS["--mfp-" + k] == frame["--mf-" + frame_tok], k   # still the frame's own colour


def test_light_values_are_wrens_map_and_only_inside_the_light_media_query():
    assert LIGHT_DEFS == LIGHT
    assert set(LIGHT) | SAME_IN_BOTH == set(PREVIOUS) and not set(LIGHT) & SAME_IN_BOTH   # media etc. never switch
    flat = _FLAT(TOKENS)
    outside = flat[:flat.index("@media (prefers-color-scheme: light)")]
    for k, v in LIGHT.items():                       # no light-only colour leaks into the dark default
        if v not in PREVIOUS.values():
            assert not re.search(rf"--mfp-[\w-]+:\s*{re.escape(v)};", outside), (k, v)
    assert flat.count("@media (prefers-color-scheme: light)") == 1
    for f in PHONE_FILES:                            # phone CSS sets no colour of its own (tokens only)
        css = _FLAT((STATIC / f).read_text())
        assert not re.search(r"rgba?\(|hsla?\(", css), f
        assert set(re.findall(r"#[0-9a-fA-F]{3,8}\b", css)) <= {"#fff"}, f


def test_phone_css_uses_no_frame_or_old_remapped_tokens():
    old = r"var\(--mf-(ink|forest-\d+|leaf|linen|oat|walnut[\w-]*|brass|brick[\w-]*|scrim|pill|paper|card|line|text[\w-]*|green[\w-]*|accent-text|red[\w-]*|warn-[\w]+|outline|focus|tile-ring-fill|qr-\w+)\)"
    for f in PHONE_FILES:
        assert not re.findall(old, (STATIC / f).read_text()), f
    phone_rules = TOKENS[TOKENS.index("/* ---- Phone: follows"):]
    assert not re.findall(old, phone_rules)
    used = set(re.findall(r"var\((--mfp-[\w-]+)\)", phone_rules + "".join((STATIC / f).read_text() for f in PHONE_FILES)))
    assert used <= set(PREVIOUS), used - set(PREVIOUS)            # every phone token used is defined


def test_frame_never_reads_phone_tokens():
    for f in ("style.css",):
        assert "--mfp-" not in (STATIC / f).read_text()
    frame = TOKENS[:TOKENS.index("/* ---- Phone: follows")]
    assert "--mfp-" not in frame and "prefers-color-scheme" not in frame
    assert "prefers-color-scheme" not in (ROOT / "templates" / "index.html").read_text()


def _lum(h):
    h = h.lstrip("#")
    h = "".join(c * 2 for c in h) if len(h) == 3 else h
    v = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    v = [x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4 for x in v]
    return 0.2126 * v[0] + 0.7152 * v[1] + 0.0722 * v[2]


def ratio(a, b):
    x, y = sorted((_lum(a), _lum(b)), reverse=True)
    return (x + 0.05) / (y + 0.05)


# (foreground, background, minimum): text 4.5:1, focus rings / control outlines / meaningful marks 3:1 (WCAG 2.x)
TEXT_PAIRS = [("text", "bg"), ("text", "surface"), ("text", "warn-bg"), ("text-2", "bg"), ("text-2", "surface"),
              ("strip-text", "strip"), ("text", "strip"), ("header-text", "header"), ("accent-text", "bg"),
              ("accent-text", "surface"), ("accent-text", "success-bg"), ("on-accent", "accent"), ("on-accent", "danger"),
              ("danger-text", "surface"), ("danger-text", "bg"), ("danger-text", "danger-bg"), ("warn-text", "warn-bg"),
              ("on-gold", "warn-line")]
# --mfp-gold is only a mark here (bar dot, countdown ring): light #8A6A1E on bg is 4.49:1, a hair under the 4.5 text minimum
# (light-tokens.md rounds it to 4.5), so it must not become text colour without Wren re-checking (see test below).
NON_TEXT_PAIRS = [("focus", "bg"), ("focus", "surface"), ("focus", "success-bg"), ("focus", "header"), ("gold", "bg"),
                  ("gold", "surface"), ("gold", "strip"), ("gold", "header"),
                  ("warn-line", "warn-bg"), ("outline", "surface"), ("outline", "bg"), ("ok", "strip")]


@pytest.mark.parametrize("scheme", ["dark", "light"])
def test_map_pairs_meet_wcag_aa(scheme):
    defs = DARK_DEFS if scheme == "dark" else {**DARK_DEFS, **LIGHT_DEFS}
    c = lambda k: defs["--mfp-" + k]   # noqa: E731
    low = [(f, b, round(ratio(c(f), c(b)), 2)) for f, b in TEXT_PAIRS if ratio(c(f), c(b)) < 4.5]
    low += [(f, b, round(ratio(c(f), c(b)), 2)) for f, b in NON_TEXT_PAIRS if ratio(c(f), c(b)) < 3]
    assert low == [], (scheme, low)


def test_gold_is_never_text_colour():
    """light --mfp-gold #8A6A1E is 4.49:1 on paper: keep it to non-text marks (dot, ring) unless Wren re-checks it."""
    css = TOKENS + "".join((STATIC / f).read_text() for f in PHONE_FILES)
    assert not re.search(r"(?<![-\w])color:\s*var\(--mfp-gold\)", _FLAT(css))
    assert round(ratio(LIGHT["--mfp-gold"], LIGHT["--mfp-bg"]), 2) == 4.49


def test_light_inset_focus_rings_get_a_white_inner_line():
    phone = _FLAT((STATIC / "phone.css").read_text())
    light = phone[phone.index("@media (prefers-color-scheme: light)"):]
    rule = re.search(r"\.mf-phone :is\(([^)]*)\):focus-visible \{\s*box-shadow: inset 0 0 0 5px #fff; \}", light)
    assert rule, light
    assert [x.strip() for x in rule.group(1).split(",")] == [
        ".bar a", ".grid a", ".tile", ".seg button", ".tile .acts button", ".lib .vx", ".tplay", ".trim .bar .back"]
    # a photo <img> paints over an inset shadow, so on photo tiles the white line is an outline on the img itself
    assert ".mf-phone :is(.grid a, .tile):focus-visible img { outline: 2px solid var(--mfp-on-accent); outline-offset: -5px; }" in light
    assert "box-shadow" not in phone[:phone.index("@media (prefers-color-scheme: light)")]   # dark rings unchanged


@pytest.mark.skipif(BROWSER is None, reason="no Chromium/Chrome installed")
def test_light_focus_on_a_photo_tile_shows_gold_ring_and_white_inner_line(page, server, mf_app, lib):
    """#141: Tab onto a Library photo tile in light: gold 3px ring at the edge, then a white line on the photo."""
    import base64, io
    from PIL import Image
    lib.add("c.jpg", PNG)
    _scheme(page, "light")
    _phone(page, server, mf_app, "admin/library")
    js = "(()=>{const t=document.querySelector('.lib .tile'); return [...document.querySelectorAll('a,button,input,[tabindex]')].filter(e=>!e.disabled && e.offsetParent).indexOf(t)+1;})()"
    for _ in range(evaluate(page, js)):
        for kind in ("keyDown", "keyUp"):
            page.call("Input.dispatchKeyEvent", {"type": kind, "key": "Tab", "code": "Tab", "windowsVirtualKeyCode": 9})
    page.pump(0.3)
    assert evaluate(page, "document.activeElement === document.querySelector('.lib .tile') && document.activeElement.matches(':focus-visible')")
    r = json.loads(evaluate(page, "JSON.stringify(document.querySelector('.lib .tile').getBoundingClientRect())"))
    shot = page.call("Page.captureScreenshot", {"format": "png"})["result"]["data"]
    im = Image.open(io.BytesIO(base64.b64decode(shot))).convert("RGB")
    x, y = int(r["x"] + r["width"] / 2), int(r["y"])
    assert im.getpixel((x, y + 1)) == (138, 106, 30)          # gold ring (#8A6A1E), 0-3 px inside the tile
    assert im.getpixel((x, y + 4)) == (255, 255, 255)         # white inner line, 3-5 px, on top of the photo


# ---------------- headless Chrome: both schemes on real pages ----------------

def _vars(page, names):
    return json.loads(evaluate(page, "JSON.stringify(Object.fromEntries(%s.map(n=>[n, getComputedStyle(document.body)"
                                     ".getPropertyValue(n).trim()])))" % json.dumps(names)))


def _css(page, sel, prop):
    return evaluate(page, f"getComputedStyle(document.querySelector('{sel}'))['{prop}']")


@pytest.mark.skipif(BROWSER is None, reason="no Chromium/Chrome installed")
def test_browser_dark_and_light_on_every_phone_page(page, server, mf_app, lib, monkeypatch, tmp_path):
    monkeypatch.setenv("MEMORYFRAME_NETMODE_STATE", str(tmp_path / "nm.json"))
    (tmp_path / "nm.json").write_text('{"mode": "product"}')
    lib.add("c.jpg", PNG)
    media = sorted(SAME_IN_BOTH)
    seen = {}
    for scheme, bg, bar, text in (("dark", "rgb(14, 19, 17)", "rgb(22, 38, 29)", "rgb(243, 237, 228)"),
                                  ("light", "rgb(246, 241, 233)", "rgb(255, 255, 255)", "rgb(31, 42, 36)")):
        _scheme(page, scheme)
        for path in ("admin/", "admin/library", "admin/upload", "admin/settings", "admin/wifi/", "pair"):
            _phone(page, server, mf_app, path)
            assert evaluate(page, "matchMedia('(prefers-color-scheme: %s)').matches" % scheme), (scheme, path)
            assert _css(page, "html", "backgroundColor") == bg and _css(page, "body", "backgroundColor") == bg, (scheme, path)
            assert _css(page, ".bar", "backgroundColor") == bar and _css(page, "body", "color") == text, (scheme, path)
            assert _css(page, "html", "colorScheme") == "light dark", (scheme, path)
        seen[scheme] = _vars(page, media)                 # media overlays, mini frame note, pair hero
        assert all(seen[scheme].values()), seen[scheme]
    assert seen["light"] == seen["dark"]
    assert page.exceptions() == []
