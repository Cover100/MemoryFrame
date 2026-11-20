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
WORDMARK_SHA = "43a1c021ec7b897c83d31a9e02bbcd7aaefb42692890b89c48ba1829402a7080"      # wren-wordmark.png, 282x35 RGBA, linen on transparent
WREN = {"splash-A.png": "168b6ab1904006eceab2c9541d7b3094854de96070da22ff0697a1542b607566",   # wren-A-static.png
        "error-e3.png": "a466b812b76291db1c5f894c5c03856222168777cb1e1fbc9bd1b02f9367f9e4"}   # error-e3.png


def test_pngs_are_wrens_files_byte_for_byte():
    from PIL import Image
    for name, sha in WREN.items():
        f = BOOT / name
        assert hashlib.sha256(f.read_bytes()).hexdigest() == sha, name
        with Image.open(f) as im:
            assert im.size == (800, 480) and im.mode == "RGB" and im.format == "PNG"


def test_wordmark_is_wrens_file_1_to_1():
    from PIL import Image
    f = BOOT / "wordmark.png"
    assert hashlib.sha256(f.read_bytes()).hexdigest() == WORDMARK_SHA
    with Image.open(f) as im:
        assert im.size == (282, 35) and im.mode == "RGBA" and im.format == "PNG"


def test_wordmark_data_uri_in_splash_and_overlay_is_the_file(mf_app, client):
    """splash.html and the app overlay draw the wordmark from a data: URI of static/boot/wordmark.png (no font wait)."""
    raw = (BOOT / "wordmark.png").read_bytes()
    uri = "data:image/png;base64," + base64.b64encode(raw).decode()
    splash = (BOOT / "splash.html").read_text()
    assert splash.count(uri) == 1
    assert re.search(r'<img class="word" alt="" src="data:image/png;base64,', splash)
    assert ".word{display:block;width:282px;height:35px;margin-top:35px}" in splash          # 1:1, no scaling
    css = (ROOT / "static" / "boot.css").read_text()
    assert "#bootSplash .bsWord{display:block;width:282px;height:35px;margin-top:35px}" in css
    html = (ROOT / "templates" / "index.html").read_text()
    assert re.search(r'<img class="bsWord" alt="" width="282" height="35" src="\{\{ boot_wordmark \}\}">', html)
    boot = client.get("/?boot=1").get_data(as_text=True)                       # the overlay carries it, the plain page does not
    assert uri in boot and 'id="bootSplash"' in boot and 'class="bsWord"' in boot
    assert uri not in client.get("/").get_data(as_text=True)


def test_total_size_under_140_kb():
    """120 KB before; +4 KB wordmark file, +6 KB of it again as base64 inside splash.html."""
    files = list(BOOT.iterdir()) + OVERLAY
    assert sorted(f.name for f in BOOT.iterdir()) == ["error-e3.png", "error.html", "splash-A.png", "splash.html", "wordmark.png"]
    total = sum(f.stat().st_size for f in files)
    assert total < 140 * 1024, total


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
    # nothing else is loaded: the only src is the data: wordmark image (splash.html), never a URL
    srcs = re.findall(r'src="([^"]*)"', text)
    assert all(x.startswith("data:image/png;base64,") for x in srcs) and len(srcs) == (1 if page == "splash.html" else 0)
    assert not re.search(r"href=", text)


def test_overlay_markup_matches_the_splash():
    """Same lockup sizes and position in the overlay as in splash.html (so the file:// to app cut is invisible)."""
    splash = (BOOT / "splash.html").read_text()
    css = (ROOT / "static" / "boot.css").read_text()
    for prop in ("width:132px;height:100px", "border:5px solid", "border-radius:16px", "width:28px;height:28px",
                 "margin-top:35px", "width:282px;height:35px", "height:172px",
                 "left:50%;top:50%;width:360px;height:172px;transform:translate(-50%,-50%)",
                 "cubic-bezier(.37,0,.63,1) infinite",
                 "@keyframes", "0%,100%{opacity:1;transform:scale(1)}50%{opacity:.3;transform:scale(.75)}"):
        assert prop in splash and prop in css, prop
    assert "--breath:2800ms" in splash and "mfBootBreathe 2800ms" in css       # Wren's 2.8 s period
    assert css.startswith("html,body{background:#0E1311}")                   # ink before anything else has loaded
    assert "transition:opacity 600ms" in css and "Memory Frame" in (ROOT / "templates" / "index.html").read_text()


def test_min_dwell_is_one_constant_off_by_default():
    """A single constant, 0 = no minimum time on the splash page once the app answers; ?dwell=<ms> overrides it for a test."""
    splash = (BOOT / "splash.html").read_text()
    assert len(re.findall(r"const MIN_DWELL_S = 0;", splash)) == 1
    assert 'num("dwell", MIN_DWELL_S * 1000)' in splash
