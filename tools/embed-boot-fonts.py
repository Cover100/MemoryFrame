#!/usr/bin/env python3
"""#162: (re)embed the frame font into the self-contained boot pages static/boot/splash.html and error.html.

They are opened as file:// by kiosk.sh before the app is up, where sibling font files can't be loaded reliably, so
each @font-face carries the font as a base64 woff2 data URI. To keep the pages small the font is subset to printable
ASCII plus the few typographic characters the copy uses. Developer tool only (needs fontTools + brotli, which are not
app or test dependencies):  pip install fonttools brotli && python3 tools/embed-boot-fonts.py
Every @font-face block with font-weight:400 or 700 gets the matching static/fonts/atkinson-hyperlegible-*.woff2."""
import base64
import io
import re
import sys
from pathlib import Path

from fontTools import subset

ROOT = Path(__file__).resolve().parents[1]
PAGES = [ROOT / "static" / "boot" / "splash.html", ROOT / "static" / "boot" / "error.html"]
UNICODES = list(range(0x20, 0x7F)) + [0xA0, 0x2014, 0x2019, 0x2026]


def subset_woff2(weight):
    opts = subset.Options()
    opts.flavor = "woff2"
    opts.layout_features = ["kern", "liga"]
    opts.name_IDs = ["*"]            # keep the OFL copyright/licence names
    opts.notdef_outline = True
    font = subset.load_font(str(ROOT / "static" / "fonts" / f"atkinson-hyperlegible-{weight}.woff2"), opts)
    s = subset.Subsetter(opts)
    s.populate(unicodes=UNICODES)
    s.subset(font)
    buf = io.BytesIO()
    subset.save_font(font, buf, opts)
    return base64.b64encode(buf.getvalue()).decode()


def main():
    data = {w: subset_woff2(w) for w in ("400", "700")}
    for page in PAGES:
        text = page.read_text()

        def fix(m):
            block = m.group(0)
            w = re.search(r"font-weight:\s*(\d+)", block).group(1)
            return re.sub(r"url\(data:font/woff2;base64,[A-Za-z0-9+/=]*\)",
                          f"url(data:font/woff2;base64,{data[w]})", block)
        new = re.sub(r"@font-face\{[^}]*\}", fix, text)
        page.write_text(new)
        print(f"{page.relative_to(ROOT)}: {len(new.encode())} bytes")


if __name__ == "__main__":
    sys.exit(main())
