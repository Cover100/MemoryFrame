"""Designer's MF-26 framing rules - no-face part (test matrix rows 4, 5, 12, 13-no-face, 16, 17, 18)."""
import os

import pytest
from PIL import Image

from memoryframe import framing, imaging

T = (1000, 600)


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








def test_17_small_source_no_upscale(tmp_path):
    src, dst = tmp_path / "s.jpg", tmp_path / "d.jpg"
    Image.new("RGB", (640, 480), (9, 9, 9)).save(src)
    w, h, _ = imaging.make_display_copy(str(src), str(dst), T)
    assert (w, h) == (640, 384)                      # 5:3 crop at native resolution


def test_18_deterministic(tmp_path):
    src = tmp_path / "s.jpg"
    Image.effect_noise((1200, 1800), 50).convert("RGB").save(src, quality=90)
    a, b = tmp_path / "a.jpg", tmp_path / "b.jpg"
    imaging.make_display_copy(str(src), str(a), T)
    imaging.make_display_copy(str(src), str(b), T)
    assert open(a, "rb").read() == open(b, "rb").read()
    assert framing.crop_box((1200, 1800), T) == framing.crop_box((1200, 1800), T)





@pytest.mark.parametrize("size", [(3000, 4000), (4000, 3000), (1080, 2400), (8000, 2000), (640, 480), (4032, 3024)])
def test_default_1000x600_copy_is_exactly_the_window(size):
    w, h = size
    assert imaging.parse_target(imaging.DEFAULT_TARGET) == (1000, 600)
    win = W(w, h)
    box = framing.crop_box(size, (1000, 600))
    assert box == pytest.approx((win[0], win[1], win[0] + win[2], win[1] + win[3]), abs=1)
    assert (box[2] - box[0]) / (box[3] - box[1]) == pytest.approx(5 / 3, rel=0.01)


@pytest.mark.parametrize("size", [(4000, 3000), (3000, 4000), (1080, 2312), (8000, 1000)])
def test_noisy_1000x600_under_300kb(tmp_path, size):
    src, dst = tmp_path / "s.jpg", tmp_path / "d.jpg"
    Image.frombytes("RGB", size, os.urandom(size[0] * size[1] * 3)).save(src, quality=95)
    w, h, n = imaging.make_display_copy(str(src), str(dst), (1000, 600))
    assert (w, h) == (1000, 600) and n <= imaging.MAX_BYTES



def test_portrait_copy_shows_the_window(tmp_path):
    """3000x4000, marker band exactly where the window is (rows 300..2100): the copy is that band."""
    im = Image.new("RGB", (3000, 4000), (255, 0, 0))
    im.paste((0, 255, 0), (0, 300, 3000, 2100))
    src, dst = tmp_path / "s.jpg", tmp_path / "d.jpg"
    im.save(src, quality=95)
    imaging.make_display_copy(str(src), str(dst), T)
    out = Image.open(dst).convert("RGB")
    assert out.size == T
    for y in (3, 300, 596):
        assert out.getpixel((500, y))[1] > 200, y


def test_stale_copies_are_regenerated_by_backfill(mf_app, lib):
    """Copies made before MF-26 (no marker: 800x600 / 1000x750 / uncropped) are remade."""
    from memoryframe import db as mfdb
    lib.add("p.jpg", _jpeg(3000, 4000))
    d = mf_app.config["DATA_DIR"]
    conn = mfdb.connect(mf_app.config["DB_PATH"])
    name = conn.execute("SELECT stored_name FROM media").fetchone()[0]
    dst = os.path.join(d, "display", imaging.display_name(name))
    Image.new("RGB", (1000, 1333)).save(dst)                        # old-style copy
    assert not imaging.copy_is_current(dst, T)
    assert imaging.backfill(conn, d, T) == (1, 0)
    assert Image.open(dst).size == T and imaging.copy_is_current(dst, T)
    assert imaging.backfill(conn, d, T) == (0, 0)                    # current: left alone
    conn.close()


def _jpeg(w, h):
    import io
    b = io.BytesIO()
    Image.new("RGB", (w, h), (90, 30, 30)).save(b, "JPEG")
    return b.getvalue()
