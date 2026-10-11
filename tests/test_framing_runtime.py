"""#36 review: the runtime path (make_display_copy) - target aspect, exact centre crop with no
faces, and a hard face-detection time budget."""
import time

import pytest
from PIL import Image

from memoryframe import faces, framing, imaging


def _gradient(w, h):
    """No faces; a 2-D colour ramp so the crop position is visible in the output pixels."""
    im = Image.new("RGB", (w, h))
    px = im.load()
    for y in range(0, h):
        for x in range(0, w):
            px[x, y] = (x * 255 // max(1, w - 1), y * 255 // max(1, h - 1), 128)
    return im


def _centred(w, h, tw, th):
    a = tw / th
    cw = min(w, h * a)
    ch = cw / a
    left, top = int(round((w - cw) / 2)), int(round((h - ch) / 2))
    return (left, top, left + min(w - left, int(round(cw))), top + min(h - top, int(round(ch))))


@pytest.fixture
def spy(monkeypatch):
    seen = {}
    real_crop, real_detect = framing.crop_box, faces.detect

    def crop(size, target=None, fs=None):
        seen["faces"] = fs
        seen["box"] = real_crop(size, target, fs)
        return seen["box"]

    def detect(im):
        seen["detected"] = real_detect(im)
        return seen["detected"]
    monkeypatch.setattr(framing, "crop_box", crop)
    monkeypatch.setattr(faces, "detect", detect)
    return seen


@pytest.mark.parametrize("target", [(1000, 600), (1000, 750)])
@pytest.mark.parametrize("size", [(600, 800), (800, 600), (540, 1200), (1600, 400), (900, 900)],
                         ids=["portrait", "landscape", "tall", "panorama", "square"])
def test_no_face_runtime_is_exact_centre_crop(tmp_path, spy, size, target):
    w, h = size
    src, dst = tmp_path / "s.png", tmp_path / "d.jpg"
    _gradient(w, h).save(src)
    imaging.make_display_copy(str(src), str(dst), target)
    assert spy["detected"] == []                       # really no faces on this input
    assert spy["box"] == _centred(w, h, *target), (spy["box"], _centred(w, h, *target))
    out = Image.open(dst)
    assert abs(out.width / out.height - target[0] / target[1]) < 0.01
    # centre pixel of the copy == centre pixel of the original (no offset)
    r, g, _ = out.convert("RGB").getpixel((out.width // 2, out.height // 2))
    assert abs(r - 128) <= 8 and abs(g - 128) <= 8, (r, g)


@pytest.mark.parametrize("target", [(1000, 600), (1000, 750)])
def test_output_not_distorted(tmp_path, target):
    """A circle must stay a circle: crop in the target aspect, then a uniform scale."""
    from PIL import ImageDraw
    im = Image.new("RGB", (3000, 4000), "white")
    ImageDraw.Draw(im).ellipse((1100, 1600, 1900, 2400), fill="black")          # centred, d=800
    src, dst = tmp_path / "s.png", tmp_path / "d.jpg"
    im.save(src)
    imaging.make_display_copy(str(src), str(dst), target)
    out = Image.open(dst).convert("L")
    assert out.size == target
    bbox = Image.eval(out, lambda v: 255 if v < 128 else 0).getbbox()
    bw, bh = bbox[2] - bbox[0], bbox[3] - bbox[1]
    assert abs(bw - bh) <= 2, (bw, bh)                 # not stretched
    assert abs(bw - 800 * target[0] / 3000) <= 3


@pytest.mark.parametrize("target", [(1000, 600), (1000, 750)])
def test_crop_box_uses_target_aspect(target):
    for size in [(3000, 4000), (4000, 3000), (8000, 2000), (1080, 2400)]:
        l, t, r, b = framing.crop_box(size, target)
        assert abs((r - l) / (b - t) - target[0] / target[1]) < 0.01


def test_slow_detector_falls_back_within_budget(tmp_path, monkeypatch, spy):
    def slow(arr):
        time.sleep(4)
        return None
    monkeypatch.setattr(faces, "_run_detector", slow)
    src, dst = tmp_path / "s.png", tmp_path / "d.jpg"
    _gradient(600, 800).save(src)
    t0 = time.monotonic()
    imaging.make_display_copy(str(src), str(dst), (1000, 600))
    took = time.monotonic() - t0
    assert took < faces.BUDGET_S + 1.0, took
    assert spy["detected"] == [] and spy["box"] == _centred(600, 800, 1000, 600)


def test_busy_detector_does_not_queue_beyond_budget(monkeypatch):
    """A second copy while a timed-out detection still runs: also bounded by the budget."""
    monkeypatch.setattr(faces, "_run_detector", lambda arr: time.sleep(3))
    im = _gradient(64, 64)
    for _ in range(2):
        t0 = time.monotonic()
        assert faces.detect(im) == []
        assert time.monotonic() - t0 < faces.BUDGET_S + 0.3
    time.sleep(3.2)                                    # let the stray worker finish


def test_detector_error_falls_back(monkeypatch):
    def boom(arr):
        raise RuntimeError("dnn exploded")
    monkeypatch.setattr(faces, "_run_detector", boom)
    assert faces.detect(_gradient(64, 64)) == []
