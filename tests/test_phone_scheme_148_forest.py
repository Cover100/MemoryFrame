"""#148 option A, "Forest night" (the user's final choice; Wren's 148-scheme-options/index.md section A): the phone/web
app ALWAYS uses the frame's forest-on-ink dark scheme, whatever the phone's OS light/dark setting, no toggle.

Pinned here (self-contained, so it also runs, and fails, against 1d71041, the always-light Warm paper build):
  * Wren's exact A token values on .mf-phone, and <html> painted ink;
  * the declarations: :root { color-scheme: only dark }, meta color-scheme "dark", ONE theme-color #16261D, no media;
  * no prefers-color-scheme switching anywhere on the phone;
  * Wren's A hardening (3.1) ring light band = --mfp-ring-light (oat), third band = --mfp-ring-band (ink), no #fff band;
    (3.2) control outlines drawn as an inset shadow over a transparent border; (3.3) text-2/strip-text #C0B29E;
  * AA (4.5:1 text, 3:1 marks) on the A pairs; the frame's --mf tokens unchanged;
  * headless Chrome: OS light and OS dark render pixel-identical Forest night pages."""
import base64
import io
import json
import re
from pathlib import Path

import pytest
from PIL import Image, ImageChops

from test_frontend_browser import BROWSER, evaluate, page, server  # noqa: F401  (fixtures)
from test_restyle_c_phone import PNG, _phone

ROOT = Path(__file__).resolve().parents[1]
STATIC, TEMPLATES = ROOT / "static", ROOT / "templates"
_FLAT = lambda s: re.sub(r"/\*.*?\*/", "", s, flags=re.S)   # noqa: E731
TOKENS, PHONE = (STATIC / "tokens.css").read_text(), (STATIC / "phone.css").read_text()
PHONE_FILES = ("phone.css", "video.css", "trim.css", "homewifi.css")

# Wren's A table (148-scheme-options/index.md, section A + src/), verbatim
FOREST_A = {
    "bg": "#0E1311", "surface": "#24382C", "header": "#16261D", "header-text": "#F3EDE4", "strip": "#3A2C22",
    "strip-text": "#C0B29E", "line": "#2F4A3A", "outline": "#C9BBA8", "text": "#F3EDE4", "text-2": "#C0B29E",
    "accent": "#2A6140", "on-accent": "#FFFFFF", "accent-text": "#8FD1A4", "ok": "#8FD1A4", "success-bg": "#16261D",
    "danger": "#A8352A", "danger-text": "#E7A097", "danger-bg": "#3A1F1B", "warn-bg": "#3A2C22", "warn-line": "#D6B36A",
    "warn-text": "#F3EDE4", "focus": "#D6B36A", "gold": "#D6B36A", "track": "#2F4A3A", "scrim": "rgba(0, 0, 0, .6)",
    "on-gold": "#0E1311", "panel": "#16261D", "tile": "#16261D", "ring-band": "#0E1311", "ring-light": "#C9BBA8"}
# The frame's own tokens (style.css / index.html read these): #148 must not touch them
FRAME = {"ink": "#0E1311", "forest-900": "#16261D", "forest-800": "#24382C", "forest-700": "#2F4A3A",
         "forest-600": "#2A6140", "linen": "#F3EDE4", "oat": "#C9BBA8", "leaf": "#8FD1A4", "brass": "#D6B36A",
         "brick": "#A8352A", "brick-text": "#E7A097"}
HARDENED = (".btn-neutral", "button.secondary", ".chip", ".field", ".code", ".btn-onbar", "#frameMessage", ".msgKeep",
            ".trim .tcancel", "#list .vcancel")
PAGES = ("/admin/", "/admin/upload", "/admin/library", "/admin/settings", "/admin/wifi/", "/admin/nope")


def _phone_defs():
    flat = _FLAT(TOKENS)
    start = flat.index(".mf-phone {")
    block = flat[start:flat.index("}", start)]
    return {k[6:]: " ".join(v.split()) for k, v in re.findall(r"(--mfp-[\w-]+):\s*([^;]+);", block)}


def _lum(h):
    v = [int(h[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    v = [x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4 for x in v]
    return 0.2126 * v[0] + 0.7152 * v[1] + 0.0722 * v[2]


def ratio(a, b):
    la, lb = sorted((_lum(a), _lum(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def test_phone_tokens_are_wrens_forest_night_values():
    defs = _phone_defs()
    assert {k: defs.get(k) for k in FOREST_A} == FOREST_A
    assert re.findall(r"(?m)^:root \{ --mfp-bg: (#\w+); \}", _FLAT(TOKENS)) == ["#0E1311"]   # <html> painted ink
    assert _FLAT(TOKENS).count(".mf-phone {") == 1                                          # one scheme, no twin


def test_frame_tokens_are_untouched():
    frame = TOKENS[:TOKENS.index("/* ---- Phone:")]
    got = dict(re.findall(r"--mf-([\w-]+):\s*(#[0-9A-Fa-f]{6});", frame))
    assert {k: got.get(k) for k in FRAME} == FRAME
    assert "--mfp-" not in frame and "color-scheme" not in frame
    style, kiosk = (STATIC / "style.css").read_text(), (TEMPLATES / "index.html").read_text()
    assert "--mfp-" not in style and "color-scheme" not in kiosk and "theme-color" not in kiosk


def test_no_prefers_color_scheme_switching_on_the_phone():
    for f in ("tokens.css",) + PHONE_FILES:
        assert "prefers-color-scheme" not in _FLAT((STATIC / f).read_text()), f
    assert "prefers-color-scheme" not in (TEMPLATES / "_phone.html").read_text()


def test_root_declares_only_dark():
    flat = _FLAT(PHONE)
    assert re.findall(r"(?<![-\w])color-scheme:\s*([^;]+);", flat) == ["only dark"]
    assert re.search(r"(?m)^:root \{ color-scheme: only dark; background-color: var\(--mfp-bg\); \}", flat)


def test_every_phone_page_has_meta_dark_and_one_forest_theme_colour(client, paired):
    lan = {"REMOTE_ADDR": "10.42.0.23"}
    for u in PAGES + ("/pair/nope",):
        html = client.get(u, environ_base=lan).get_data(as_text=True)
        assert 'class="mf-phone"' in html, u
        metas = re.findall(r'<meta name="(color-scheme|theme-color)"([^>]*)>', html)
        assert metas == [("color-scheme", ' content="dark"'), ("theme-color", ' content="#16261D"')], (u, metas)


def test_ring_bands_use_the_ring_tokens():                                            # Wren 3.1
    flat = _FLAT(PHONE)
    assert flat.count("inset 0 0 0 5px var(--mfp-ring-light)") == 3
    assert flat.count("inset 0 0 0 7px var(--mfp-ring-band)") == 2
    assert "5px #fff" not in flat and "7px var(--mfp-text)" not in flat
    assert ratio("#C9BBA8", "#0E1311") ** 0.5 >= 3.15          # worst case against any photo: sqrt(9.96) = 3.16:1


def test_control_outlines_are_an_inset_shadow():                                     # Wren 3.2
    flat = _FLAT(PHONE)
    m = re.search(r"([^{}]*)\{\s*border-color: transparent; box-shadow: inset 0 0 0 1\.5px var\(--mfp-outline\); \}", flat)
    assert m, "no hardening rule"
    assert [x.strip() for x in m.group(1).split(",")] == [".mf-phone " + h for h in HARDENED]
    assert flat.rindex("var(--mfp-outline); }") > flat.rindex("border: 1.5px solid var(--mfp-outline)")   # wins by order


def test_forest_night_pairs_meet_aa():                                               # Wren 3.3 + AA guards
    t = FOREST_A
    text = [("text", "bg"), ("text", "surface"), ("text-2", "bg"), ("text-2", "surface"), ("header-text", "header"),
            ("strip-text", "strip"), ("accent-text", "bg"), ("accent-text", "surface"), ("on-accent", "accent"),
            ("danger-text", "bg"), ("danger-text", "surface"), ("danger-text", "danger-bg"), ("warn-text", "warn-bg"),
            ("on-gold", "gold"), ("text", "panel"), ("text", "success-bg")]
    marks = [("outline", "bg"), ("outline", "surface"), ("focus", "bg"), ("focus", "surface"), ("ok", "bg"),
             ("warn-line", "warn-bg"), ("ring-light", "ring-band")]
    low = [(a, b, round(ratio(t[a], t[b]), 2)) for a, b in text if ratio(t[a], t[b]) < 4.5]
    low += [(a, b, round(ratio(t[a], t[b]), 2)) for a, b in marks if ratio(t[a], t[b]) < 3]
    assert low == []


_PROBE = """JSON.stringify({bg: getComputedStyle(document.body).backgroundColor,
  bar: getComputedStyle(document.querySelector('.bar')).backgroundColor, scheme: getComputedStyle(document.documentElement).colorScheme})"""


def _shot(page, server, mf_app, path, os_pref):
    page.call("Emulation.setEmulatedMedia", {"features": [{"name": "prefers-color-scheme", "value": os_pref}]})
    _phone(page, server, mf_app, path)
    page.pump(0.8)                                              # thumbnails and fonts settled
    probe = json.loads(evaluate(page, _PROBE))
    png = base64.b64decode(page.call("Page.captureScreenshot", {"format": "png"})["result"]["data"])
    return probe, Image.open(io.BytesIO(png)).convert("RGB")


@pytest.mark.skipif(BROWSER is None, reason="no Chromium/Chrome installed")
def test_os_light_and_os_dark_render_identically(page, server, mf_app, lib, monkeypatch, tmp_path):
    monkeypatch.setenv("MEMORYFRAME_NETMODE_STATE", str(tmp_path / "nm.json"))
    (tmp_path / "nm.json").write_text('{"mode": "product"}')
    lib.add("c.jpg", PNG)
    for path in ("admin/", "admin/library", "admin/settings", "admin/wifi/"):
        for attempt in range(3):                                # a late thumbnail/font load can land between shots
            (pl, il), (pd, im) = _shot(page, server, mf_app, path, "light"), _shot(page, server, mf_app, path, "dark")
            box = ImageChops.difference(il, im).getbbox()
            if box is None:
                break
        assert pl == pd, (path, pl, pd)
        assert pd["bg"] == "rgb(14, 19, 17)" and pd["bar"] == "rgb(22, 38, 29)", pd
        assert box is None, (path, box)                         # pixel-identical in both OS settings
    assert page.exceptions() == []
