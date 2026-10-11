"""Designer's MF-26 framing rules - no-face part (test matrix rows 4, 5, 12, 13-no-face, 16, 17, 18)."""
import os

import pytest
from PIL import Image

from memoryframe import framing, imaging

T = (1000, 750)       # generic 4:3 target (Option B); the default 1000x600 is tested below


def W(w, h):
    return framing.fallback_window(w, h)


def test_4_portrait_no_face_window_top_300():
    left, top, ww, wh = W(3000, 4000)
    assert (left, ww, round(wh)) == (0, 3000, 1800) and top == pytest.approx(300)


def test_5_landscape_4x3_no_face_top_210():
    left, top, ww, wh = W(4000, 3000)
    assert (ww, wh) == (4000, 2400) and top == pytest.approx(210)


def test_12_tall_screenshot_starts_15_percent_down():
    left, top, ww, wh = W(1080, 2400)
    assert top == pytest.approx(0.15 * 2400) and ww == 1080


def test_panorama_no_face_centred():
    left, top, ww, wh = W(8000, 2000)
    assert left == pytest.approx((8000 - 2000 * 5 / 3) / 2) and top == 0 and wh == 2000


def test_wide_landscape_between_5x3_and_pano_centred():
    left, top, ww, wh = W(4000, 2000)                  # 2:1
    assert top == 0 and left == pytest.approx((4000 - 2000 * 5 / 3) / 2)


@pytest.mark.parametrize("size", [(3000, 4000), (4000, 3000), (1080, 2400), (8000, 2000), (4000, 2250),
                                  (3024, 4032), (1000, 1000)])
def test_16_window_is_inside_css_visible_band(size):
    """Option B: the 4:3 copy box shown with object-fit: cover must show the chosen window
    (or, when clamping shifts the box, stay inside the image)."""
    w, h = size
    win = W(w, h)
    box = framing.expand_to(win, w, h, T[0] / T[1])
    l, t, r, b = box
    assert 0 <= l < r <= w and 0 <= t < b <= h
    assert (r - l) / (b - t) == pytest.approx(4 / 3, rel=0.01)
    band = framing.visible_band(box)
    assert (band[2] - band[0]) / (band[3] - band[1]) == pytest.approx(5 / 3, rel=0.01)
    if size == (3000, 4000):                         # not clamped: visible band == window
        assert band[1] == pytest.approx(win[1], abs=1) and band[3] == pytest.approx(win[1] + win[3], abs=1)


def test_portrait_copy_rows_75_675_are_the_window(tmp_path):
    """3000x4000 with a marker band exactly where the window is (rows 300..2100):
    in the 1000x750 copy that band must be rows 75..675 (the CSS-visible part)."""
    im = Image.new("RGB", (3000, 4000), (255, 0, 0))
    im.paste((0, 255, 0), (0, 300, 3000, 2100))
    src, dst = tmp_path / "s.jpg", tmp_path / "d.jpg"
    im.save(src, quality=95)
    imaging.make_display_copy(str(src), str(dst), T)
    out = Image.open(dst).convert("RGB")
    assert out.size == T
    for y in (80, 375, 670):
        assert out.getpixel((500, y))[1] > 200, y
    for y in (10, 740):
        assert out.getpixel((500, y))[0] > 200, y


def test_17_small_source_no_upscale(tmp_path):
    src, dst = tmp_path / "s.jpg", tmp_path / "d.jpg"
    Image.new("RGB", (640, 480), (9, 9, 9)).save(src)
    w, h, _ = imaging.make_display_copy(str(src), str(dst), T)
    assert (w, h) == (640, 480)


def test_18_deterministic(tmp_path):
    src = tmp_path / "s.jpg"
    Image.effect_noise((1200, 1800), 50).convert("RGB").save(src, quality=90)
    a, b = tmp_path / "a.jpg", tmp_path / "b.jpg"
    imaging.make_display_copy(str(src), str(a), T)
    imaging.make_display_copy(str(src), str(b), T)
    assert open(a, "rb").read() == open(b, "rb").read()
    assert framing.crop_box((1200, 1800), T) == framing.crop_box((1200, 1800), T)


@pytest.mark.parametrize("size", [(3000, 4000), (4000, 3000), (1080, 2312), (1000, 5000), (8000, 1000),
                                  (4032, 3024), (3024, 4032), (1920, 1080)])
def test_every_copy_is_exactly_target_and_small(tmp_path, size):
    src, dst = tmp_path / "s.jpg", tmp_path / "d.jpg"
    Image.new("RGB", size, (30, 60, 90)).save(src, quality=90)
    w, h, n = imaging.make_display_copy(str(src), str(dst), T)
    assert (w, h) == T and n <= imaging.MAX_BYTES


def test_screen_aspect_single_constant_flip(monkeypatch):
    """Switching to 1000x600 (5:3) output makes stage 2 a no-op: copy == window."""
    w, h = 3000, 4000
    box = framing.crop_box((w, h), (1000, 600))
    win = W(w, h)
    assert box == (0, round(win[1]), 3000, round(win[1] + win[3]))


@pytest.mark.parametrize("size", [(3000, 4000), (4000, 3000), (1080, 2400), (8000, 2000), (640, 480), (4032, 3024)])
def test_default_1000x600_copy_is_exactly_the_window(size):
    w, h = size
    assert imaging.parse_target(imaging.DEFAULT_TARGET) == (1000, 600)
    win = W(w, h)
    box = framing.crop_box(size, (1000, 600))
    assert box == (round(win[0]), round(win[1]), round(win[0] + win[2]), round(win[1] + win[3]))
    band = framing.visible_band(box)                 # CSS on 800x480 shows the whole copy
    assert band == pytest.approx(box, abs=0.5)


@pytest.mark.parametrize("size", [(4000, 3000), (3000, 4000), (1080, 2312), (8000, 1000)])
def test_noisy_1000x600_under_300kb(tmp_path, size):
    src, dst = tmp_path / "s.jpg", tmp_path / "d.jpg"
    Image.frombytes("RGB", size, os.urandom(size[0] * size[1] * 3)).save(src, quality=95)
    w, h, n = imaging.make_display_copy(str(src), str(dst), (1000, 600))
    assert (w, h) == (1000, 600) and n <= imaging.MAX_BYTES
