"""#148 (owner's request via Quinn, 12 Nov 2026 5:40 pm): the phone/web app ALWAYS shows Warm paper light (#141),
whatever the phone's OS light/dark setting. Supersedes #146's inverted mapping. Mapping only: no colour/token value
changes, no toggle; the frame screen and slideshow (style.css, index.html, frame tokens) stay dark and untouched.

Checks (Hale's list): (1) no live dark branch: no prefers-color-scheme block in any phone stylesheet; (2) Warm paper
tokens unconditional on .mf-phone; (3) :root color-scheme: light only; (4) meta color-scheme "light only"; (5) exactly
one theme-color meta, no media attribute, the Warm paper bar #FFFFFF; (6) the frame never reads any of it. Then, in
headless Chrome, both emulated OS settings render Warm paper with matching UA colours and browser chrome."""
import json
import re
from pathlib import Path

import pytest

from test_frontend_browser import BROWSER, evaluate, page, server  # noqa: F401  (fixtures)
from test_phone_scheme_141 import LIGHT, PHONE_FILES
from test_restyle_c_phone import PNG, _phone

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "static"
TEMPLATES = ROOT / "templates"
_FLAT = lambda s: re.sub(r"/\*.*?\*/", "", s, flags=re.S)   # noqa: E731
OS_MODES = ("light", "dark")   # emulated phone OS setting (self-contained: also runs against 55c1b15)
PAGES = ("/admin/", "/admin/upload", "/admin/library", "/admin/settings", "/admin/wifi/", "/admin/nope")


def test_no_colour_scheme_media_query_anywhere_on_the_phone():                                          # (1)
    for f in ("tokens.css",) + PHONE_FILES:
        css = (STATIC / f).read_text()
        assert "prefers-color-scheme" not in _FLAT(css), f
        assert not re.search(r"@media[^{]*color-scheme", css), f
    assert "prefers-color-scheme" not in (TEMPLATES / "_phone.html").read_text()


def test_warm_paper_tokens_are_unconditional():                                                       # (2)
    flat = _FLAT((STATIC / "tokens.css").read_text())
    start = flat.index(".mf-phone {")
    block = flat[start:flat.index("}", start)]
    assert flat.count(".mf-phone {") == 1 and "@media" not in flat[flat.index(":root { --mfp-bg:"):]
    defs = dict((k, " ".join(v.split())) for k, v in re.findall(r"(--mfp-[\w-]+):\s*([^;]+);", block))
    assert {k: defs[k] for k in LIGHT} == LIGHT                                    # Wren's map, no value changed
    assert re.findall(r"(?m)^:root \{ --mfp-bg: (#\w+); \}", flat) == ["#F6F1E9"]  # <html> painted paper
    twins = {k: len(re.findall(rf"(?<![\w-]){k}:", flat)) for k in LIGHT}         # one definition each: no dark twin
    assert twins == {k: 2 if k == "--mfp-bg" else 1 for k in LIGHT}, twins       # (--mfp-bg: also :root, for <html>)


def test_root_color_scheme_is_light_only():                                                           # (3)
    phone = _FLAT((STATIC / "phone.css").read_text())
    decls = re.findall(r"(?<![-\w])color-scheme:\s*([^;]+);", phone)
    assert decls == ["light only"], decls
    assert re.search(r"(?m)^:root \{ color-scheme: light only; background-color: var\(--mfp-bg\); \}", phone)


def _metas(html):
    return re.findall(r'<meta name="(color-scheme|theme-color)"([^>]*)>', html)


def test_every_phone_page_declares_light_only_and_one_warm_paper_theme_colour(client, paired):        # (4) (5)
    lan = {"REMOTE_ADDR": "10.42.0.23"}
    for u in PAGES:                                     # (the pairing page: see the browser test below)
        html = client.get(u, environ_base=lan).get_data(as_text=True)
        assert 'class="mf-phone"' in html, u
        assert _metas(html) == [("color-scheme", ' content="light only"'), ("theme-color", ' content="#FFFFFF"')], u
        assert "media=" not in "".join(a for _n, a in _metas(html)), u


def test_frame_screen_and_slideshow_do_not_read_the_phone_scheme():                                   # (6)
    style, kiosk = (STATIC / "style.css").read_text(), (TEMPLATES / "index.html").read_text()
    assert "--mfp-" not in style and "prefers-color-scheme" not in style and ":root { color-scheme" not in style
    assert "color-scheme" not in kiosk and "theme-color" not in kiosk and "prefers-color-scheme" not in kiosk
    tokens = (STATIC / "tokens.css").read_text()
    frame = tokens[:tokens.index("/* ---- Phone:")]
    assert "--mfp-" not in frame and "color-scheme" not in frame
    assert re.search(r"--mf-ink:\s*#0E1311;", frame) and re.search(r"--mf-forest-900:\s*#16261D;", frame)  # still dark


_PROBE = """(()=>{
  const css = s => getComputedStyle(document.querySelector(s));
  const i = document.createElement('input'); i.type = 'text'; i.style.background = 'Canvas'; i.style.color = 'CanvasText';
  document.body.appendChild(i);
  const r = {bg: css('html').backgroundColor, body: css('body').backgroundColor, bar: css('.bar').backgroundColor,
    text: css('body').color, scheme: css('html').colorScheme, canvas: getComputedStyle(i).backgroundColor,
    inputScheme: getComputedStyle(i).colorScheme,
    metas: [...document.querySelectorAll('meta[name="theme-color"], meta[name="color-scheme"]')]
      .map(m => [m.name, m.getAttribute('media'), m.content])};
  i.remove(); return JSON.stringify(r); })()"""


@pytest.mark.skipif(BROWSER is None, reason="no Chromium/Chrome installed")
@pytest.mark.parametrize("os_pref", OS_MODES)
def test_both_os_settings_show_warm_paper(page, server, mf_app, lib, monkeypatch, tmp_path, os_pref):
    monkeypatch.setenv("MEMORYFRAME_NETMODE_STATE", str(tmp_path / "nm.json"))
    (tmp_path / "nm.json").write_text('{"mode": "product"}')
    lib.add("c.jpg", PNG)
    page.call("Emulation.setEmulatedMedia", {"features": [{"name": "prefers-color-scheme", "value": os_pref}]})
    for path in ("admin/", "admin/library", "admin/upload", "admin/settings", "admin/wifi/", "pair"):
        _phone(page, server, mf_app, path)
        assert evaluate(page, "matchMedia('(prefers-color-scheme: %s)').matches" % os_pref), (os_pref, path)
        got = json.loads(evaluate(page, _PROBE))
        assert (got["bg"], got["body"], got["bar"], got["text"]) == (
            "rgb(246, 241, 233)", "rgb(246, 241, 233)", "rgb(255, 255, 255)", "rgb(31, 42, 36)"), (os_pref, path, got)
        assert got["scheme"] == got["inputScheme"] == "light only", (os_pref, path, got)   # UA controls light
        assert got["canvas"] == "rgb(255, 255, 255)", (os_pref, path, got)                 # UA Canvas light
        assert got["metas"] == [["color-scheme", None, "light only"], ["theme-color", None, "#FFFFFF"]], got
