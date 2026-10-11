"""MF-26 (#35): Designer's framing test matrix (faces) + detector fallbacks + a real-face check."""
import os
import time

import pytest
from PIL import Image

from memoryframe import faces as mffaces
from memoryframe import framing, imaging
from memoryframe.faces import Face

T = (1000, 600)   # default display copy: exactly the 5:3 window
HERE = os.path.dirname(__file__)


def win(size, fs):
    box, window, kept = framing.choose(size, fs)
    return box, window, kept


def inside(f, l, t, r, b, tol=0.5):
    return f.x >= l - tol and f.y >= t - tol and f.x + f.w <= r + tol and f.y + f.h <= b + tol


def in_window(f, window):
    l, t, ww, wh = window
    return inside(f, l, t, l + ww, t + wh)


def in_band(f, box):
    """At 1000x600 the whole copy is on screen: the box IS the visible area."""
    return inside(f, *box)


def check_visible(box, window, kept):
    for f in kept:
        assert in_window(f, window), (f, window)
        assert in_band(f, box), (f, box)


# 1
def test_1_one_face_centre_right_upper_half():
    f = Face(2600, 700, 400, 500)
    box, window, kept = win((4000, 3000), [f])
    l, t, ww, wh = window
    assert ww == 4000 and kept == [f]
    assert (framing.eye_y(f) - t) / wh == pytest.approx(0.36, abs=0.03)
    check_visible(box, window, kept)


# 2
def test_2_face_touching_top_edge_clamps():
    f = Face(1800, 0, 400, 500)
    box, window, kept = win((4000, 3000), [f])
    assert window[1] == 0 and box == (0, 0, 4000, 2400) and kept == [f]
    check_visible(box, window, kept)                 # at 1000x600 the copy is the window


# 3
def test_3_portrait_face_at_20_percent():
    f = Face(1300, 800, 350, 400)                    # y = 20 % of 4000
    box, window, kept = win((3000, 4000), [f])
    l, t, ww, wh = window
    assert (ww, round(wh)) == (3000, 1800)
    assert (framing.eye_y(f) - t) / wh == pytest.approx(0.36, abs=0.03)
    pb = framing.padded(f)
    assert pb[1] >= t and pb[3] <= t + wh            # head + hair (padding) inside
    check_visible(box, window, kept)


# 6
def test_6_three_faces_in_a_row_fit_and_centred():
    fs = [Face(x, 1000, 300, 380) for x in (1200, 1800, 2400)]
    box, window, kept = win((4000, 3000), fs)
    g = framing._union([framing.padded(f) for f in fs])
    l, t, ww, wh = window
    assert len(kept) == 3 and g[0] >= l and g[2] <= l + ww and g[1] >= t and g[3] <= t + wh
    check_visible(box, window, kept)


# 7
def test_7_six_face_row_in_portrait_keeps_best_and_cuts_no_kept_face():
    fs = [Face(20 + i * 500, 1300, 440 if i in (2, 3) else 400, 480 if i in (2, 3) else 440) for i in range(6)]
    box, window, kept = win((3000, 4000), fs)
    l, t, ww, wh = window
    assert 1 <= len(kept) < 6
    assert {fs[2], fs[3]} <= set(kept)               # largest + most central kept
    check_visible(box, window, kept)
    for f in kept:
        assert not (f.x < l < f.x + f.w or f.x < l + ww < f.x + f.w)


# 8
def test_8_tiny_background_face_ignored():
    big, tiny = Face(1500, 800, 600, 700), Face(3400, 600, 150, 180)
    _, w1, k1 = win((4000, 3000), [big, tiny])
    _, w2, k2 = win((4000, 3000), [big])
    assert k1 == [big] and w1 == w2


# 9
def test_9_face_3_percent_of_short_side_ignored():
    f = Face(2000, 400, 80, 90)                      # 90 / 3000 = 3 %
    box, window, kept = win((4000, 3000), [f])
    assert kept == [] and window == framing.fallback_window(4000, 3000)


# 10
def test_10_face_half_outside_left_discarded():
    f = Face(-300, 900, 600, 700)
    _, window, kept = win((4000, 3000), [f])
    assert kept == []


# 11
def test_11_face_20_percent_outside_left_counts_and_clamps():
    f = Face(-120, 900, 600, 700)
    box, window, kept = win((3000, 4000), [f])
    assert kept == [f] and window[0] == 0 and box[0] == 0


# 12 (no face) is in test_framing.py; with a face, rules apply as normal:
def test_12b_tall_screenshot_with_face():
    f = Face(400, 1400, 200, 240)
    box, window, kept = win((1080, 2400), [f])
    assert kept == [f]
    check_visible(box, window, kept)


# 13
def test_13_panorama_face_at_80_percent_clamped_right():
    f = Face(6400 - 200, 600, 400, 480)              # centre x = 80 % of 8000
    box, window, kept = win((8000, 2000), [f])
    l, t, ww, wh = window
    assert l + ww / 2 == pytest.approx(min(6400, 8000 - ww / 2), abs=1)
    check_visible(box, window, kept)


# 14
@pytest.mark.parametrize("how", ["missing_model", "raises", "no_cv2", "disabled", "too_slow"])
def test_14_detector_missing_or_failing_falls_back(tmp_path, monkeypatch, how):
    import builtins
    if how == "missing_model":
        monkeypatch.setattr(mffaces, "MODEL", str(tmp_path / "nope.onnx"))
    elif how == "raises":
        import cv2
        monkeypatch.setattr(cv2, "FaceDetectorYN", None)
    elif how == "no_cv2":
        real = builtins.__import__
        monkeypatch.setattr(builtins, "__import__",
                            lambda n, *a, **k: (_ for _ in ()).throw(ImportError("no cv2")) if n == "cv2" else real(n, *a, **k))
    elif how == "disabled":
        monkeypatch.setenv("MEMORYFRAME_FACE_DETECT", "0")
    else:
        clock = iter([0.0, 5.0] + [5.0] * 10)
        monkeypatch.setattr(time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(mffaces, "_logged", set())
    src, dst = tmp_path / "s.jpg", tmp_path / "d.jpg"
    Image.open(os.path.join(HERE, "fixture_astronaut.jpg")).resize((1200, 1600)).save(src)
    assert mffaces.detect(Image.open(src)) == []
    w, h, n = imaging.make_display_copy(str(src), str(dst), T)
    assert (w, h) == T and n <= imaging.MAX_BYTES
    assert framing.crop_box((1200, 1600), T, []) == framing.crop_box((1200, 1600), T)


# 15
def test_15_close_up_headroom_5_percent():
    f = Face(900, 700, 1500, 1700)                   # padded height 3570 > 0.85 * 2400
    box, window, kept = win((4000, 3000), [f])
    l, t, ww, wh = window
    assert framing.padded(f)[1] - t == pytest.approx(framing.HEADROOM_MIN * wh, abs=1) or t == 0


# 16
@pytest.mark.parametrize("size,f", [((3000, 4000), Face(1300, 300, 400, 480)),
                                    ((3000, 4000), Face(1300, 3200, 400, 480)),
                                    ((4000, 3000), Face(1800, 900, 400, 480)),
                                    ((4000, 3000), Face(1800, 60, 400, 480)),
                                    ((4000, 3000), Face(1800, 2450, 400, 480)),
                                    ((4000, 2250), Face(3500, 800, 300, 360)),
                                    ((1080, 2400), Face(400, 2000, 200, 240))])
def test_16_kept_faces_inside_rows_75_675(tmp_path, size, f):
    box, window, kept = win(size, [f])
    assert kept
    for k in kept:
        assert in_band(k, box), (k, box)
    # and in copy pixels: at 1000x600 the whole copy (rows 0..600) is on screen
    assert (box[2] - box[0]) / (box[3] - box[1]) == pytest.approx(5 / 3, rel=0.01)
    sy = 600 / (box[3] - box[1])
    for k in kept:
        assert (k.y - box[1]) * sy >= -1 and (k.y + k.h - box[1]) * sy <= 601


# 18
def test_18_deterministic_with_faces():
    fs = [Face(x, 1000, 300, 380) for x in (200, 900, 1600, 2300, 3000, 3600)]
    assert win((4000, 3000), fs) == win((4000, 3000), list(fs))


def test_equal_scores_tie_break_leftmost():
    a, b = Face(1000, 1000, 300, 380), Face(2700, 1000, 300, 380)   # symmetric about centre
    assert framing._order([b, a], 4000, 3000)[0] == a


# ---------- real detector on a real face (NASA public-domain portrait, OpenCV/skimage sample) ----------

def test_yunet_finds_real_face_and_copy_keeps_it_visible(tmp_path):
    pytest.importorskip("cv2")
    im = Image.open(os.path.join(HERE, "fixture_astronaut.jpg"))
    found = mffaces.detect(im)
    assert len(found) == 1 and found[0].score >= 0.8
    # pad the portrait into a tall 3:4 frame with the face low, so naive cropping would cut it
    tall = Image.new("RGB", (1200, 1600), (40, 40, 40))
    tall.paste(im.resize((1200, 1200)), (0, 400))
    src, dst = tmp_path / "s.jpg", tmp_path / "d.jpg"
    tall.save(src, quality=90)
    fs = mffaces.detect(Image.open(src))
    assert fs
    box = framing.crop_box((1200, 1600), T, fs)
    assert all(in_band(f, box) for f in fs)
    t0 = time.monotonic()
    imaging.make_display_copy(str(src), str(dst), T)
    assert Image.open(dst).size == T and time.monotonic() - t0 < 5


def test_detection_speed_12mp():
    pytest.importorskip("cv2")
    im = Image.open(os.path.join(HERE, "fixture_astronaut.jpg")).resize((4000, 3000))
    mffaces.detect(im)                                # warm-up
    t0 = time.monotonic()
    mffaces.detect(im)
    took = time.monotonic() - t0
    print(f"YuNet on 12 MP (640 px detector input): {took * 1000:.0f} ms")
    assert took < 1.5
