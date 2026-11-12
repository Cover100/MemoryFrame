"""#141: Wren's "Warm paper" map (light-tokens.md, 11 Nov 2026); #146 inverted the OS mapping; PR149 made Warm paper the
only scheme. #148 (user's final choice, option A "Forest night", Wren's 148-scheme-options): the phone ALWAYS shows the
frame's forest-on-ink dark scheme, whatever the phone's OS setting, no colour-scheme media query, no toggle.
PREVIOUS is the PHONE-DARK.md mapping (the pre-#141 dark look); FOREST is PREVIOUS plus Wren's A changes (text-2 /
strip-text nudged to #C0B29E, ring-light / ring-band added). LIGHT (Warm paper) is kept as the retired reference.
Media overlays / mini frame note / pair hero are unchanged; the frame never reads a phone token."""
import json
import re
from pathlib import Path

import pytest

from test_frontend_browser import BROWSER, evaluate, page, server  # noqa: F401  (fixtures)
from test_restyle_c_phone import OS_MODES, PNG, _phone, _scheme

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
# #148 A (Wren): text-2 / strip-text out of Samsung's dim zone (luma 0.72-0.85), and the #141 ring's light / dark bands
FOREST_CHANGES = {"--mfp-text-2": "#C0B29E", "--mfp-strip-text": "#C0B29E",
                  "--mfp-ring-light": "#C9BBA8", "--mfp-ring-band": "#0E1311"}
FOREST = {**PREVIOUS, **FOREST_CHANGES}
SAME_IN_BOTH = {k for k in PREVIOUS if k.startswith(("--mfp-media-", "--mfp-note-", "--mfp-illo-"))} | {"--mfp-tile-ring-fill"}


def _block(css, start):
    i = css.index(start) + len(start)
    return css[i:css.index("}", i)]


def _defs(block):
    return dict((k, " ".join(v.split())) for k, v in re.findall(r"(--mfp-[\w-]+):\s*([^;]+);", block))


PHONE_DEFS = _defs(_block(_FLAT(TOKENS), ".mf-phone {"))    # #148: the one (unconditional) phone scheme


def test_phone_values_are_warm_paper_and_otherwise_the_previous_colours():
    """#148 A token equivalence: every --mfp-* is exactly the PHONE-DARK.md value (PREVIOUS, the frame's own colours),
    except Wren's A changes (FOREST_CHANGES). (Name kept from the Warm paper era; the Warm paper values are retired.)"""
    assert PHONE_DEFS == FOREST
    frame = dict(re.findall(r"(--mf-[\w-]+):\s*(#[0-9A-Fa-f]{6})", TOKENS[:TOKENS.index("/* ---- Phone:")]))
    for k, frame_tok in (("bg", "ink"), ("surface", "forest-800"), ("header", "forest-900"), ("header-text", "linen"),
                         ("strip", "walnut-900"), ("strip-text", "oat"), ("line", "forest-700"), ("outline", "oat"),
                         ("text", "linen"), ("text-2", "oat"), ("accent", "forest-600"), ("accent-text", "leaf"),
                         ("ok", "leaf"), ("success-bg", "forest-900"), ("danger", "brick"), ("danger-text", "brick-text"),
                         ("warn-bg", "walnut-900"), ("warn-line", "brass"), ("warn-text", "linen"), ("focus", "brass"),
                         ("gold", "brass"), ("track", "forest-700")):
        assert PREVIOUS["--mfp-" + k] == frame["--mf-" + frame_tok], k   # the frame's own colour (frame unchanged)


def test_light_values_are_wrens_map_and_unconditional():
    """#148 A: Warm paper (LIGHT) is retired: none of its own values is left; Forest night is unconditional."""
    assert set(LIGHT) | SAME_IN_BOTH == set(PREVIOUS) and not set(LIGHT) & SAME_IN_BOTH   # media etc. never switch
    flat = _FLAT(TOKENS)
    assert "prefers-color-scheme" not in flat and "@media" not in flat[flat.index(".mf-phone {"):]   # #148: no switching
    assert flat.count(".mf-phone {") == 1 and len(re.findall(r"(?m)^:root \{ --mfp-bg:", flat)) == 1
    assert re.search(r"(?m)^:root \{ --mfp-bg: #0E1311; \}", flat)                     # <html> painted ink
    for k, v in LIGHT.items():                       # no retired Warm paper value is left behind
        if FOREST[k] != v:
            assert not re.search(rf"{k}:\s*{re.escape(v)};", flat), (k, v)
    for f in PHONE_FILES:                            # phone CSS sets no colour of its own (tokens only)
        css = _FLAT((STATIC / f).read_text())
        assert not re.search(r"rgba?\(|hsla?\(", css), f
        assert set(re.findall(r"#[0-9a-fA-F]{3,8}\b", css)) <= {"#fff"}, f   # #fff: the Library select ring only


def test_phone_css_uses_no_frame_or_old_remapped_tokens():
    old = r"var\(--mf-(ink|forest-\d+|leaf|linen|oat|walnut[\w-]*|brass|brick[\w-]*|scrim|pill|paper|card|line|text[\w-]*|green[\w-]*|accent-text|red[\w-]*|warn-[\w]+|outline|focus|tile-ring-fill|qr-\w+)\)"
    for f in PHONE_FILES:
        assert not re.findall(old, (STATIC / f).read_text()), f
    phone_rules = TOKENS[TOKENS.index("/* ---- Phone:"):]
    assert not re.findall(old, phone_rules)
    used = set(re.findall(r"var\((--mfp-[\w-]+)\)", phone_rules + "".join((STATIC / f).read_text() for f in PHONE_FILES)))
    assert used <= set(FOREST), used - set(FOREST)                # every phone token used is defined


def test_frame_never_reads_phone_tokens():
    for f in ("style.css",):
        assert "--mfp-" not in (STATIC / f).read_text()
    frame = TOKENS[:TOKENS.index("/* ---- Phone:")]
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
                  ("warn-line", "warn-bg"), ("outline", "surface"), ("outline", "bg"), ("ok", "strip"),
                  ("ok", "bg"), ("ring-light", "ring-band")]          # #148 A (Wren's table)
TEXT_PAIRS += [("text", "success-bg"), ("text", "panel")]          # #148 A (Wren's table)


def test_map_pairs_meet_wcag_aa():
    """Forest night AA guard (#148 A: the only phone scheme): text 4.5:1, focus / outlines / marks 3:1, and the
    #141 ring's light band vs its dark band 3:1."""
    defs, scheme = PHONE_DEFS, "forest night"
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
    light = phone[phone.index(".mf-phone :is(.bar a, .seg button"):]   # #148: the #141 ring section, unconditional
    rule = re.search(r"\.mf-phone :is\(([^)]*)\):focus-visible \{\s*box-shadow: inset 0 0 0 5px var\(--mfp-ring-light\); \}", light)
    assert rule, light
    assert [x.strip() for x in rule.group(1).split(",")] == [".bar a", ".seg button", ".tile .acts button", ".lib .vx",
                                                             ".tplay", ".trim .bar .back"]
    # photos (Wren): an overlay above the img with gold 3px + light 2px + dark 2px (#148 A: oat / ink, >= 3.16:1 on any photo)
    assert ("box-shadow: inset 0 0 0 3px var(--mfp-focus), inset 0 0 0 5px var(--mfp-ring-light), "
            "inset 0 0 0 7px var(--mfp-ring-band); }") in light
    assert "#fff" not in light                                                # #148 A: no white band left (forced dark inverts it)
    for sel in (".mf-phone .grid a:focus-visible::after", ".mf-phone:not(.selecting) .tile:focus-visible::after",
                '.mf-phone.selecting .tile:not([aria-pressed="true"]):focus-visible::before'):
        assert sel in light, sel
    assert "focus-visible img" not in phone                                   # the c8e730c img outline is gone
    assert "box-shadow" not in phone[:phone.index(".mf-phone :is(.bar a, .seg button")]   # no shadow anywhere else
    assert "prefers-color-scheme" not in phone                                  # #148: in every OS setting
    assert (PHONE_DEFS["--mfp-ring-light"], PHONE_DEFS["--mfp-ring-band"]) == ("#C9BBA8", "#0E1311")   # oat / ink
    assert round(ratio("#C9BBA8", "#0E1311"), 2) == 9.96 and ratio(PHONE_DEFS["--mfp-ring-light"], "#000000") > 3


def _two_tone_jpeg():
    """A photo with a clear light half (left, near white) and dark half (right, near black)."""
    import io
    from PIL import Image
    im = Image.new("RGB", (800, 800), (235, 232, 225))
    im.paste((25, 25, 30), (400, 0, 800, 800))
    b = io.BytesIO()
    im.save(b, "JPEG", quality=95)
    return b.getvalue()


def _tab_to(page, sel):
    js = ("(()=>{const t=document.querySelector(%s); return [...document.querySelectorAll('a,button,input,[tabindex]')]"
          ".filter(e=>!e.disabled && e.offsetParent).indexOf(t)+1;})()" % json.dumps(sel))
    for _ in range(evaluate(page, js)):
        for kind in ("keyDown", "keyUp"):
            page.call("Input.dispatchKeyEvent", {"type": kind, "key": "Tab", "code": "Tab", "windowsVirtualKeyCode": 9})
    page.pump(0.3)
    assert evaluate(page, "document.activeElement === document.querySelector(%s) && "
                          "document.activeElement.matches(':focus-visible')" % json.dumps(sel)), sel


def _bands(page, sel):
    """Pixels 1, 4 and 6 px inside the focused element's top edge, over the light (left) and dark (right) half."""
    import base64, io
    from PIL import Image
    r = json.loads(evaluate(page, "JSON.stringify(document.querySelector(%s).getBoundingClientRect())" % json.dumps(sel)))
    im = Image.open(io.BytesIO(base64.b64decode(page.call("Page.captureScreenshot", {"format": "png"})["result"]["data"]))).convert("RGB")
    y = int(r["y"])
    return {side: [im.getpixel((int(r["x"] + r["width"] * f), y + d)) for d in (1, 4, 6)]
            for side, f in (("light", .25), ("dark", .75))}


# #148 A: brass (--mfp-focus), oat (--mfp-ring-light), ink (--mfp-ring-band). (WHITE kept as the light band's name.)
GOLD, WHITE, DARK = (214, 179, 106), (201, 187, 168), (14, 19, 17)


@pytest.mark.no_media
@pytest.mark.skipif(BROWSER is None, reason="no Chromium/Chrome installed")
@pytest.mark.parametrize("os_pref", OS_MODES)
@pytest.mark.parametrize("path,sel", [("admin/library", ".lib .tile"), ("admin/", ".grid a")])
def test_light_focus_on_a_photo_is_gold_white_dark_over_light_and_dark_areas(page, server, mf_app, lib, path, sel, os_pref):
    """#141 (Wren): on a focused Library photo tile and a focused home grid link (#148 A Forest night, in both OS
    settings), the ring sits above the photo: gold 0-3 px, light band 3-5 px, dark band 5-7 px from the edge (brass /
    oat / ink), the same over a light and a dark part of the photo."""
    lib.add("split.jpg", _two_tone_jpeg())
    _scheme(page, os_pref)
    _phone(page, server, mf_app, path)
    page.pump(0.5)
    _tab_to(page, sel)
    bands = _bands(page, sel)
    assert bands == {"light": [GOLD, WHITE, DARK], "dark": [GOLD, WHITE, DARK]}, bands


@pytest.mark.no_media
@pytest.mark.skipif(BROWSER is None, reason="no Chromium/Chrome installed")
@pytest.mark.parametrize("os_pref", OS_MODES)
def test_dark_focus_on_a_photo_is_unchanged(page, server, mf_app, lib, os_pref):
    """Was: dark keeps the single brass ring (no overlay). #148 A: Forest night is the only scheme and keeps the #141
    overlay, so in BOTH OS settings a focused photo gets brass / oat / ink bands above the photo."""
    lib.add("split.jpg", _two_tone_jpeg())
    _scheme(page, os_pref)
    _phone(page, server, mf_app, "admin/library")
    page.pump(0.5)
    _tab_to(page, ".lib .tile")
    assert evaluate(page, "getComputedStyle(document.querySelector('.lib .tile'), '::after').content") == '""'
    bands = _bands(page, ".lib .tile")
    assert bands == {"light": [GOLD, WHITE, DARK], "dark": [GOLD, WHITE, DARK]}, bands


def _rgb(css):
    return tuple(int(v) for v in re.findall(r"\d+", css)[:3])


@pytest.mark.no_media
@pytest.mark.skipif(BROWSER is None, reason="no Chromium/Chrome installed")
@pytest.mark.parametrize("os_pref", OS_MODES)
def test_light_selected_and_focused_tile_gets_the_three_bands_and_keeps_its_tick(page, server, mf_app, lib, os_pref):
    """#141 (Wren polish): a tile that is selected AND focused in select mode gets the same gold/white/dark ring,
    inside the 3 px green selected edge (3-6 / 6-8 / 8-10 px), over the light and the dark half; the green edge, the
    filled select circle and the white tick still render."""
    import base64, io
    from PIL import Image
    lib.add("split.jpg", _two_tone_jpeg())
    _scheme(page, os_pref)
    _phone(page, server, mf_app, "admin/library")
    page.pump(0.5)
    evaluate(page, "document.querySelector('.lib .tile').click(); document.activeElement.blur(); 1")   # select it
    page.pump(0.3)
    assert evaluate(page, "document.body.classList.contains('selecting') || "
                          "!!document.querySelector('.mf-phone.selecting')") is True
    assert evaluate(page, "document.querySelector('.lib .tile').getAttribute('aria-pressed')") == "true"
    _tab_to(page, ".lib .tile")
    r = json.loads(evaluate(page, "JSON.stringify(document.querySelector('.lib .tile').getBoundingClientRect())"))
    green = _rgb(evaluate(page, "getComputedStyle(document.querySelector('.lib .tile')).borderTopColor"))
    disc = _rgb(evaluate(page, "getComputedStyle(document.querySelector('.lib .tile'), '::after').backgroundColor"))
    tick = _rgb(evaluate(page, "getComputedStyle(document.querySelector('.lib .tile'), '::before').borderLeftColor"))
    im = Image.open(io.BytesIO(base64.b64decode(page.call("Page.captureScreenshot", {"format": "png"})["result"]["data"]))).convert("RGB")
    x0, y0, w = int(r["x"]), int(r["y"]), int(r["width"])
    bands = {side: [im.getpixel((x0 + int(w * f), y0 + d)) for d in (1, 4, 7, 9)] for side, f in (("light", .5), ("dark", .8))}
    assert bands == {"light": [green, GOLD, WHITE, DARK], "dark": [green, GOLD, WHITE, DARK]}, bands
    assert im.getpixel((x0 + 1, y0 + int(r["height"]) // 2)) == green               # green edge on the side too
    box = [im.getpixel((x0 + i, y0 + j)) for i in range(9, 35) for j in range(9, 35)]   # circle 26 px at 6 px + border
    assert box.count(disc) > 150, (disc, box.count(disc))                           # filled circle, above the ring
    assert sum(1 for c in box if c == tick) > 10                                    # the tick
    assert evaluate(page, "getComputedStyle(document.querySelector('.lib .tile')).outlineColor") == "rgba(0, 0, 0, 0)"


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
    bg, bar, text = "rgb(14, 19, 17)", "rgb(22, 38, 29)", "rgb(243, 237, 228)"   # #148 A: Forest night in both
    for scheme in OS_MODES:                              # the emulated OS setting
        _scheme(page, scheme)
        for path in ("admin/", "admin/library", "admin/upload", "admin/settings", "admin/wifi/", "pair"):
            _phone(page, server, mf_app, path)
            assert evaluate(page, "matchMedia('(prefers-color-scheme: %s)').matches" % scheme), (scheme, path)
            assert _css(page, "html", "backgroundColor") == bg and _css(page, "body", "backgroundColor") == bg, (scheme, path)
            assert _css(page, ".bar", "backgroundColor") == bar and _css(page, "body", "color") == text, (scheme, path)
            assert _css(page, "html", "colorScheme") in ("dark only", "only dark"), (scheme, path)
        seen[scheme] = _vars(page, media)                 # media overlays, mini frame note, pair hero
        assert all(seen[scheme].values()), seen[scheme]
    assert seen["light"] == seen["dark"]
    assert page.exceptions() == []
