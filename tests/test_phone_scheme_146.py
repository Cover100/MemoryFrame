"""#146 (owner's request via Quinn, 12 Nov 2026): the phone colour-scheme mapping is INVERTED. A phone set to LIGHT
shows the dark scheme; a phone set to DARK shows Warm paper light (#141). Only the mapping changed: no colour values,
no toggle; the frame screen / slideshow never read a phone token. These tests emulate the raw OS setting (not the
shown-scheme helper), so they pin the mapping itself, plus the browser chrome (theme-color) and the UA's own
colour scheme (form controls, scrollbars: the Canvas system colour) to the scheme actually shown."""
import json

import pytest

from test_frontend_browser import BROWSER, evaluate, page, server  # noqa: F401  (fixtures)
from test_restyle_c_phone import PNG, _phone

# raw OS setting -> what must be shown: page bg, bar bg (= theme-color), body text, root color-scheme, UA Canvas dark?
SHOWN = {
    "light": ("rgb(14, 19, 17)", "rgb(22, 38, 29)", "rgb(243, 237, 228)", "dark", True),          # dark scheme
    "dark": ("rgb(246, 241, 233)", "rgb(255, 255, 255)", "rgb(31, 42, 36)", "light only", False),  # Warm paper
}
_PROBE = """(()=>{
  const css = s => getComputedStyle(document.querySelector(s));
  const metas = [...document.querySelectorAll('meta[name="theme-color"]')].filter(m => matchMedia(m.media).matches);
  const i = document.createElement('input'); i.type = 'text'; i.style.background = 'Canvas'; i.style.color = 'CanvasText';
  document.body.appendChild(i);
  const cv = getComputedStyle(i).backgroundColor, ink = getComputedStyle(i).color, ics = getComputedStyle(i).colorScheme;
  i.remove();
  return JSON.stringify({bg: css('html').backgroundColor, body: css('body').backgroundColor, bar: css('.bar').backgroundColor,
    text: css('body').color, scheme: css('html').colorScheme, theme: metas.map(m => m.content), canvas: cv, canvasText: ink,
    inputScheme: ics});
})()"""


def _rgb(hex_):
    h = hex_.lstrip("#")
    return "rgb(%d, %d, %d)" % tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def _dark(rgb):
    r, g, b = (int(v) for v in rgb[4:-1].split(","))
    return r + g + b < 3 * 64


@pytest.mark.skipif(BROWSER is None, reason="no Chromium/Chrome installed")
@pytest.mark.parametrize("os_pref", ["light", "dark"])
def test_os_setting_shows_the_inverted_scheme_with_matching_chrome_and_controls(page, server, mf_app, lib, monkeypatch,
                                                                                tmp_path, os_pref):
    monkeypatch.setenv("MEMORYFRAME_NETMODE_STATE", str(tmp_path / "nm.json"))
    (tmp_path / "nm.json").write_text('{"mode": "product"}')
    lib.add("c.jpg", PNG)
    bg, bar, text, scheme, ua_dark = SHOWN[os_pref]
    page.call("Emulation.setEmulatedMedia", {"features": [{"name": "prefers-color-scheme", "value": os_pref}]})
    for path in ("admin/", "admin/library", "admin/upload", "admin/settings", "admin/wifi/", "pair"):
        _phone(page, server, mf_app, path)
        assert evaluate(page, "matchMedia('(prefers-color-scheme: %s)').matches" % os_pref), (os_pref, path)
        got = json.loads(evaluate(page, _PROBE))
        assert (got["bg"], got["body"], got["bar"], got["text"]) == (bg, bg, bar, text), (os_pref, path, got)
        assert got["scheme"] == got["inputScheme"] == scheme, (os_pref, path, got)      # UA controls follow the page
        assert [_rgb(c) for c in got["theme"]] == [bar], (os_pref, path, got)            # one theme-color: the bar
        assert _dark(got["canvas"]) is ua_dark and _dark(got["canvasText"]) is not ua_dark, (os_pref, path, got)
