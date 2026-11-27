"""#173: Pair page hero icon — keep a clear, balanced gap between the circle
(including its 3px stroke) and the rectangle, with balanced inner margins inside
the outer rounded frame."""
import json
import re
from pathlib import Path

import pytest

from test_frontend_browser import BROWSER, evaluate, page, server  # noqa: F401

ROOT = Path(__file__).resolve().parents[1]
PHONE_CSS = (ROOT / "static" / "phone.css").read_text()
PAIR_HTML = (ROOT / "templates" / "pair.html").read_text()


def _flat(css):
    return re.sub(r"\s+", " ", re.sub(r"/\*.*?\*/", "", css, flags=re.S))


def test_pair_hero_pseudo_elements_use_border_box():
    """Root cause: `.mf-phone *` does not match ::before/::after, so the
    circle fell back to content-box (46 + 6 = 52px, right edge 62px == rect left 62px)."""
    flat = _flat(PHONE_CSS)
    before = re.search(r"\.mf-phone \.hero span::before \{([^}]+)\}", flat)
    after = re.search(r"\.mf-phone \.hero span::after \{([^}]+)\}", flat)
    assert before and "box-sizing: border-box" in before.group(1)
    assert after and "box-sizing: border-box" in after.group(1)
    assert '<div class="hero" aria-hidden="true"><span></span></div>' in PAIR_HTML


GEOM = """JSON.stringify((()=>{
  const span = document.querySelector('.hero span');
  const sr = span.getBoundingClientRect();
  const px = v => parseFloat(v) || 0;
  const box = cs => {
    const bl = px(cs.borderLeftWidth), br = px(cs.borderRightWidth);
    const bt = px(cs.borderTopWidth), bb = px(cs.borderBottomWidth);
    const w = cs.boxSizing === 'border-box' ? px(cs.width) : px(cs.width) + bl + br;
    const h = cs.boxSizing === 'border-box' ? px(cs.height) : px(cs.height) + bt + bb;
    const left = px(cs.left);
    const top = px(cs.top);
    return {left, right: left + w, top, bottom: top + h, width: w, height: h, boxSizing: cs.boxSizing};
  };
  const b = box(getComputedStyle(span, '::before'));
  const a = box(getComputedStyle(span, '::after'));
  return {frameW: sr.width, frameH: sr.height, circle: b, rect: a};
})())"""


@pytest.mark.skipif(not BROWSER, reason="no headless browser available")
@pytest.mark.parametrize("width,height", [(360, 780), (412, 915)])
def test_pair_hero_circle_and_rect_have_balanced_gap(page, server, width, height):
    page.call("Emulation.setDeviceMetricsOverride",
              {"width": width, "height": height, "deviceScaleFactor": 1, "mobile": True})
    page.call("Page.navigate", {"url": server + "/pair"})
    page.pump(1.5)
    g = json.loads(evaluate(page, GEOM))
    c, r = g["circle"], g["rect"]
    fw, fh = g["frameW"], g["frameH"]

    gap = r["left"] - c["right"]
    left_margin = c["left"]
    right_margin = fw - r["right"]

    # Clear horizontal separation (strictly > 0, balanced with outer margins)
    assert gap >= 8, g
    assert left_margin >= 8 and right_margin >= 8, g
    assert left_margin == right_margin == gap == 10, g
    # Both shapes stay inside the outer rounded frame and are vertically centered
    assert c["top"] > 0 and c["bottom"] < fh, g
    assert r["top"] > 0 and r["bottom"] < fh, g
    assert c["top"] == fh - c["bottom"], g
    assert r["top"] == fh - r["bottom"], g
