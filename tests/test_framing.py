"""Designer's MF-26 framing rules - no-face part (test matrix rows 4, 5, 12, 13-no-face, 16, 17, 18)."""
import os

import pytest
from PIL import Image

from memoryframe import framing, imaging

T = (1000, 600)       # the default target
T43 = (1000, 750)     # GENERIC-SIZE CHECK ONLY: a non-default 4:3 target exercises stage 2


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
def test_generic_size_4x3_window_is_inside_css_visible_band(size):
    """GENERIC-SIZE CHECK ONLY (non-default 4:3 target): the 4:3 copy box shown with object-fit: cover must show the chosen window
    (or, when clamping shifts the box, stay inside the image)."""
    w, h = size
    win = W(w, h)
    box = framing.expand_to(win, w, h, T43[0] / T43[1])
    l, t, r, b = box
    assert 0 <= l < r <= w and 0 <= t < b <= h
    assert (r - l) / (b - t) == pytest.approx(4 / 3, rel=0.01)
    band = framing.visible_band(box)
    assert (band[2] - band[0]) / (band[3] - band[1]) == pytest.approx(5 / 3, rel=0.01)
    if size == (3000, 4000):                         # not clamped: visible band == window
        assert band[1] == pytest.approx(win[1], abs=1) and band[3] == pytest.approx(win[1] + win[3], abs=1)


def test_generic_size_4x3_copy_rows_75_675_are_the_window(tmp_path):
    """GENERIC-SIZE CHECK ONLY (non-default 1000x750 target). 3000x4000 with a marker band exactly where the window is (rows 300..2100):
    in the 1000x750 copy that band must be rows 75..675 (the CSS-visible part)."""
    im = Image.new("RGB", (3000, 4000), (255, 0, 0))
    im.paste((0, 255, 0), (0, 300, 3000, 2100))
    src, dst = tmp_path / "s.jpg", tmp_path / "d.jpg"
    im.save(src, quality=95)
    imaging.make_display_copy(str(src), str(dst), T43)
    out = Image.open(dst).convert("RGB")
    assert out.size == T43
    for y in (80, 375, 670):
        assert out.getpixel((500, y))[1] > 200, y
    for y in (10, 740):
        assert out.getpixel((500, y))[0] > 200, y


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


# ---------- stale copies (other size / older rules) are remade automatically ----------

def _photo(lib, mf_app, name="p.jpg", size=(3000, 4000)):
    import io
    b = io.BytesIO()
    Image.new("RGB", size, (90, 30, 30)).save(b, "JPEG")
    lib.add(name, b.getvalue())
    from memoryframe import db as mfdb
    conn = mfdb.connect(mf_app.config["DB_PATH"])
    stored = conn.execute("SELECT stored_name FROM media WHERE original_name = ?", (name,)).fetchone()[0]
    conn.close()
    return stored, os.path.join(mf_app.config["DATA_DIR"], "display", imaging.display_name(stored))


def test_copies_carry_marker(mf_app, lib):
    _, dst = _photo(lib, mf_app)
    imaging.ensure_display_copy(mf_app.config["DATA_DIR"], os.path.basename(dst), T)
    assert imaging.copy_is_current(dst, T) and not imaging.copy_is_current(dst, (1000, 750))


@pytest.mark.parametrize("old", [(1000, 1333), (1000, 750), (800, 600)])
def test_stale_copy_remade_lazily_on_serve(mf_app, lib, client, old):
    stored, dst = _photo(lib, mf_app)
    Image.new("RGB", old).save(dst)                         # pre-#37 copy, no marker
    r = client.get(f"/uploads/{stored}")
    assert r.status_code == 200
    assert Image.open(dst).size == T and imaging.copy_is_current(dst, T)


def test_copy_for_other_target_is_stale(mf_app, lib):
    stored, dst = _photo(lib, mf_app)
    d = mf_app.config["DATA_DIR"]
    imaging.ensure_display_copy(d, stored, (1000, 750))
    assert Image.open(dst).size == (1000, 750)
    imaging.ensure_display_copy(d, stored, T)               # target changed -> remade
    assert Image.open(dst).size == T


def test_backfill_remakes_only_stale(mf_app, lib):
    from memoryframe import db as mfdb
    _, dst = _photo(lib, mf_app)
    Image.new("RGB", (1000, 750)).save(dst)
    conn = mfdb.connect(mf_app.config["DB_PATH"])
    d = mf_app.config["DATA_DIR"]
    assert imaging.backfill(conn, d, T) == (1, 0)
    assert imaging.backfill(conn, d, T) == (0, 0)           # now current: untouched
    conn.close()


def test_startup_refresh_thread(mf_app, lib):
    import time
    from memoryframe import create_app
    _, dst = _photo(lib, mf_app)
    Image.new("RGB", (1000, 750)).save(dst)
    create_app({"TESTING": True, "REFRESH_COPIES_AT_START": True,
                "DATA_DIR": mf_app.config["DATA_DIR"], "DB_PATH": mf_app.config["DB_PATH"]})
    end = time.monotonic() + 10
    while time.monotonic() < end and not imaging.copy_is_current(dst, T):
        time.sleep(0.1)
    assert Image.open(dst).size == T


def test_failed_remake_keeps_old_copy(mf_app, lib, monkeypatch):
    stored, dst = _photo(lib, mf_app)
    Image.new("RGB", (1000, 750)).save(dst)
    monkeypatch.setattr(imaging, "make_display_copy", lambda *a, **k: 1 / 0)
    assert imaging.ensure_display_copy(mf_app.config["DATA_DIR"], stored, T) == dst
