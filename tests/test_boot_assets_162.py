"""#162 static checks: the boot pages and overlay assets make no external request, stay small (< 120 KB together), embed
the frame font as woff2 data URIs, and the PNGs are Wren's files byte-for-byte (sha256)."""
import base64
import hashlib
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BOOT = ROOT / "static" / "boot"
OVERLAY = [ROOT / "static" / n for n in ("boot.css", "boot.js")]
ERROR_APP = [ROOT / "static" / n for n in ("error.css", "errorhold.js")] + [ROOT / "templates" / "frame_error.html"]
WREN = {"splash-A.png": "168b6ab1904006eceab2c9541d7b3094854de96070da22ff0697a1542b607566",   # wren-A-static.png
        "error-e3.png": "a466b812b76291db1c5f894c5c03856222168777cb1e1fbc9bd1b02f9367f9e4"}   # error-e3.png


def test_pngs_are_wrens_files_byte_for_byte():
    from PIL import Image
    for name, sha in WREN.items():
        f = BOOT / name
        assert hashlib.sha256(f.read_bytes()).hexdigest() == sha, name
        with Image.open(f) as im:
            assert im.size == (800, 480) and im.mode == "RGB" and im.format == "PNG"


def test_total_size_under_120_kb():
    files = list(BOOT.iterdir()) + OVERLAY
    assert sorted(f.name for f in BOOT.iterdir()) == ["error-e3.png", "error.html", "splash-A.png", "splash.html"]
    total = sum(f.stat().st_size for f in files)
    assert total < 120 * 1024, total


@pytest.mark.parametrize("f", [BOOT / "splash.html", BOOT / "error.html"] + OVERLAY + ERROR_APP,
                         ids=lambda f: f.name)
def test_no_external_urls(f):
    text = f.read_text()
    # only the frame's own app may be named (the validated `next`, comments); nothing else, no protocol-relative URL
    rest = re.sub(r"https?://(localhost|127\.0\.0\.1)\b", "", text)
    assert not re.search(r"(?i)https?://", rest), f.name
    assert not re.search(r"""["'(]//[a-z0-9]""", rest), f.name
    for m in re.finditer(r"url\(([^)]*)\)", text):
        assert m.group(1).startswith("data:font/woff2;base64,"), m.group(0)[:60]
    assert "@import" not in text


@pytest.mark.parametrize("page", ["splash.html", "error.html"])
def test_fonts_are_embedded_woff2_for_both_weights(page):
    text = (BOOT / page).read_text()
    faces = re.findall(r"@font-face\{[^}]*\}", text)
    weights = sorted(re.search(r"font-weight:(\d+)", f).group(1) for f in faces)
    assert weights == ["400", "700"]
    for f in faces:
        data = re.search(r"url\(data:font/woff2;base64,([A-Za-z0-9+/=]+)\)", f).group(1)
        raw = base64.b64decode(data)
        assert raw[:4] == b"wOF2" and 4000 < len(raw) < 20000                   # a real (subset) woff2
    assert not re.search(r"src=|href=", text)                                    # nothing else is loaded


def test_overlay_markup_matches_the_splash():
    """Same lockup sizes and position in the overlay as in splash.html (so the file:// to app cut is invisible)."""
    splash = (BOOT / "splash.html").read_text()
    css = (ROOT / "static" / "boot.css").read_text()
    for prop in ("width:132px;height:100px", "border:5px solid", "border-radius:16px", "width:28px;height:28px",
                 "margin-top:28px;font-weight:700;font-size:40px;line-height:1.1;letter-spacing:.01em",
                 "left:50%;top:50%;width:360px;transform:translate(-50%,-50%)",
                 "cubic-bezier(.45,0,.55,1) -1800ms infinite", "{opacity:.55;transform:scale(.9)}",
                 "{opacity:1;transform:scale(1.12)}"):
        assert prop in splash and prop in css, prop
    assert "--breath:3600ms" in splash and "mfBootBreathe 3600ms" in css
    assert "transition:opacity 600ms" in css and "Memory Frame" in (ROOT / "templates" / "index.html").read_text()
