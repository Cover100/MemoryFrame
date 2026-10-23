"""#86: narrow photos (width/height < 1.30) get the B2 blurred-sides display copy; wider ones are
pixel-identical to the old full-bleed cover copy. The stored (uncropped) copy is never touched."""
import hashlib
import io
import os

import pytest
from PIL import Image, ImageChops, ImageOps, ImageStat

from memoryframe import db as mfdb
from memoryframe import faces, framing, imaging

T = (1000, 600)
YELLOW = (255, 255, 0)


@pytest.fixture(autouse=True)
def no_pause(monkeypatch):
    monkeypatch.setattr(imaging, "BACKFILL_PAUSE_S", 0)


def photo(w, h, top_band=0.0):
    """Grey 24 px stripes (blurred away in the bars, sharp in the centre), a 24 px yellow strip on the
    far left and right edge (marks the sharp photo's edges), optionally a red band across the top."""
    im = Image.new("RGB", (w, h), (200, 200, 200))
    for x in range(0, w, 48):
        im.paste((40, 40, 40), (x, 0, x + 24, h))
    im.paste(YELLOW, (0, 0, 24, h))
    im.paste(YELLOW, (w - 24, 0, w, h))
    if top_band:
        im.paste((255, 0, 0), (12, 0, w - 12, int(h * top_band)))
    return im


def save(tmp_path, im, name="src.jpg", exif=None):
    p = tmp_path / name
    im.save(p, "JPEG", quality=95, **({"exif": exif} if exif else {}))
    return str(p)


def make(tmp_path, im, name="src.jpg", exif=None):
    dst = str(tmp_path / ("out-" + name))
    imaging.make_display_copy(save(tmp_path, im, name, exif), dst, T)
    return Image.open(dst).convert("RGB"), dst


def sharp_span(out, y=300):
    """(first, last) x of the sharp photo on row y: the yellow edge strips (background is dimmed < 200)."""
    xs = [x for x in range(out.width) if (lambda p: p[0] > 200 and p[1] > 200 and p[2] < 90)(out.getpixel((x, y)))]
    return xs[0], xs[-1]


def old_cover_copy(src, dst):
    """Frozen copy of the pre-#86 cover pipeline (no faces): what a 1.30-or-wider photo must still get."""
    with Image.open(src) as im:
        im = ImageOps.exif_transpose(im)
        im = im.crop(framing.crop_box(im.size, T, []))
        if im.width > T[0] or im.height > T[1]:
            im = im.resize(T, Image.LANCZOS)
        im.save(dst, "JPEG", quality=85, optimize=True, progressive=True, comment=imaging.copy_marker(T))


@pytest.mark.parametrize("size", [(1200, 1600), (900, 1600), (1500, 1500), (1500, 1200), (1200, 1800)])
def test_narrow_photo_gets_b2(tmp_path, size):
    out, _ = make(tmp_path, photo(*size))
    assert out.size == T
    a = size[0] / size[1]
    left, right = sharp_span(out)
    k = max(1.0, min(1.15, 0.8 * 1000 / (a * 600)))
    assert abs((right - left + 1) - round(round(600 * k) * a)) <= 2          # sharp width
    assert abs(left - (999 - right)) <= 1                                       # centred within 1 px
    assert left >= 99 and 999 - right >= 99                                    # bars never under 10 %
    for y in (0, 599):                                                          # sharp photo fills the full height
        assert sharp_span(out, y) == (left, right)
    bar = out.crop((0, 100, left - 15, 500))                                    # stay clear of the shadow
    px = bar.convert("L").load()
    grad = sum(abs(px[x + 1, y] - px[x, y]) for x in range(bar.width - 1) for y in range(bar.height)) / (
        (bar.width - 1) * bar.height)
    assert grad < 1.5                                                           # blurred: no sharp detail


def test_sides_are_dimmed_by_35_percent():
    im = imaging.blurfill(Image.new("RGB", (1200, 1600), (200, 100, 40)), T)
    assert im.size == T
    assert all(abs(c - round(v * 0.65)) <= 2 for c, v in zip(im.getpixel((20, 300)), (200, 100, 40)))


def test_trim_is_15_percent_centred(tmp_path):
    """3:4: the photo is shown 1.15x the screen height, so 6.5 % of its rows are cut top and bottom."""
    out, _ = make(tmp_path, photo(1200, 1600, top_band=0.10))
    fh = round(600 * 1.15)                                       # 690 rows for the whole photo
    cut = (fh - 600) // 2                                        # 45 rows off the top
    red = sum(1 for y in range(600) if out.getpixel((500, y))[0] > 200 and out.getpixel((500, y))[1] < 80)
    assert abs(red - (0.10 * fh - cut)) <= 3


def test_face_stays_inside_the_trim(tmp_path, monkeypatch):
    """A face near the top: the trim moves down so the padded face box is kept, never cut."""
    f = faces.Face(x=450, y=20, w=200, h=200, score=0.99, det_h=100)
    monkeypatch.setattr(faces, "detect", lambda im: [f])
    out, _ = make(tmp_path, photo(1200, 1600, top_band=0.10))
    red = sum(1 for y in range(600) if out.getpixel((500, y))[0] > 200 and out.getpixel((500, y))[1] < 80)
    assert red > 0.10 * 690 - 3                                  # (almost) nothing cut off the top
    # a face that cannot fit in the trimmed window -> whole photo kept (k = 1, no trim)
    g = faces.Face(x=100, y=0, w=1000, h=1600, score=0.99, det_h=300)
    monkeypatch.setattr(faces, "detect", lambda im: [g])
    out, _ = make(tmp_path, photo(1200, 1600))
    left, right = sharp_span(out)
    assert abs((right - left + 1) - 450) <= 2                    # k = 1: 600 rows, 450 wide


@pytest.mark.parametrize("size", [(1500, 1125), (1500, 900), (1500, 750), (2000, 1000)])
def test_wide_photo_is_pixel_identical_to_old_cover_copy(tmp_path, size):
    src = save(tmp_path, photo(*size))
    old = str(tmp_path / "old.jpg")
    old_cover_copy(src, old)
    new = str(tmp_path / "new.jpg")
    imaging.make_display_copy(src, new, T)
    assert open(old, "rb").read() == open(new, "rb").read()      # byte for byte, marker comment included


def test_threshold_at_1_30(tmp_path):
    for size, blurfill in (((1300, 1000), False), ((1301, 1000), False), ((1299, 1000), True),
                           ((1250, 1000), True)):
        src = save(tmp_path, photo(*size))
        old = str(tmp_path / "old.jpg")
        old_cover_copy(src, old)
        out, dst = make(tmp_path, photo(*size))
        same = open(old, "rb").read() == open(dst, "rb").read()
        assert same != blurfill, size
        if blurfill:
            left, right = sharp_span(out)
            assert left >= 99 and 999 - right >= 99, size       # even just under 1.30 the bars are 10 %


def test_exif_rotated_photo(tmp_path):
    """A phone photo stored sideways (orientation 6) is made upright first, then treated by its upright shape."""
    upright = photo(1200, 1600)
    raw = upright.transpose(Image.Transpose.ROTATE_90)           # displayed upright again by orientation 6
    ex = Image.Exif()
    ex[0x0112] = 6
    out, _ = make(tmp_path, raw, "rot.jpg", ex.tobytes())
    ref, _ = make(tmp_path, upright, "up.jpg")
    left, right = sharp_span(out)
    assert (left, right) == pytest.approx(sharp_span(ref), abs=1)
    diff = ImageChops.difference(out, ref).convert("L")
    assert ImageStat.Stat(diff).mean[0] < 3


def test_display_copy_never_touches_the_stored_copy(mf_app, lib):
    buf = io.BytesIO()
    photo(1500, 2000).save(buf, "JPEG")
    lib.add("p.jpg", buf.getvalue())
    conn = mfdb.connect(mf_app.config["DB_PATH"])
    name = conn.execute("SELECT stored_name FROM media").fetchone()[0]
    stored = os.path.join(mf_app.config["DATA_DIR"], "originals", name)
    before = (hashlib.sha256(open(stored, "rb").read()).hexdigest(), os.stat(stored).st_mtime_ns)
    dst = os.path.join(mf_app.config["DATA_DIR"], "display", imaging.display_name(name))
    imaging.backfill(conn, mf_app.config["DATA_DIR"], T, force=True)
    conn.close()
    assert imaging.copy_is_current(dst, T) and Image.open(dst).size == T
    assert (hashlib.sha256(open(stored, "rb").read()).hexdigest(), os.stat(stored).st_mtime_ns) == before


def _add_photos(lib, mf_app, n):
    conn = mfdb.connect(mf_app.config["DB_PATH"])
    for i in range(n):
        buf = io.BytesIO()
        im = photo(1500, 2000)
        im.paste((i * 60, 99, 10), (700, 900, 760, 960))                    # distinct checksums (duplicates are skipped)
        im.save(buf, "JPEG")
        lib.add(f"p{i}.jpg", buf.getvalue())
    names = [r[0] for r in conn.execute("SELECT stored_name FROM media ORDER BY id")]
    conn.close()
    return [os.path.join(mf_app.config["DATA_DIR"], "display", imaging.display_name(n)) for n in names]


def test_old_copies_are_stale_and_backfill_is_idempotent(mf_app, lib):
    dsts = _add_photos(lib, mf_app, 3)
    d = mf_app.config["DATA_DIR"]
    old = imaging.copy_marker(T).replace(imaging.COPY_VERSION.encode(), b"mf26c")   # pre-#86 copies
    for p in dsts:
        Image.new("RGB", T).save(p, "JPEG", comment=old)
        assert not imaging.copy_is_current(p, T)
    conn = mfdb.connect(mf_app.config["DB_PATH"])
    assert imaging.backfill(conn, d, T) == (3, 0)
    bytes1 = [open(p, "rb").read() for p in dsts]
    assert all(imaging.copy_is_current(p, T) for p in dsts)
    assert imaging.backfill(conn, d, T) == (0, 0)                # second run: nothing to do, nothing rewritten
    assert [open(p, "rb").read() for p in dsts] == bytes1
    conn.close()


def test_backfill_resumes_after_an_interruption(mf_app, lib, monkeypatch):
    dsts = _add_photos(lib, mf_app, 3)
    d = mf_app.config["DATA_DIR"]
    for p in dsts:
        if os.path.exists(p):
            os.remove(p)
    conn = mfdb.connect(mf_app.config["DB_PATH"])
    real_replace, calls = os.replace, []

    def dying_replace(a, b):                                     # power cut / kill while the 2nd copy is written
        calls.append(b)
        if len(calls) == 2:
            raise KeyboardInterrupt
        return real_replace(a, b)

    monkeypatch.setattr(imaging.os, "replace", dying_replace)
    with pytest.raises(KeyboardInterrupt):
        imaging.backfill(conn, d, T)
    monkeypatch.undo()
    done = [p for p in dsts if os.path.exists(p)]
    assert len(done) == 1 and all(imaging.copy_is_current(p, T) for p in done)   # no half-written copy is served
    assert imaging.backfill(conn, d, T) == (2, 0)                # resumes: only the missing two are made
    assert imaging.backfill(conn, d, T) == (0, 0)
    assert all(imaging.copy_is_current(p, T) for p in dsts)
    assert not [f for f in os.listdir(os.path.dirname(dsts[0])) if f.endswith(".tmp")]
    conn.close()


def test_backfill_rests_between_photos(mf_app, lib, monkeypatch):
    _add_photos(lib, mf_app, 2)
    for f in os.listdir(os.path.join(mf_app.config["DATA_DIR"], "display")):
        os.remove(os.path.join(mf_app.config["DATA_DIR"], "display", f))
    naps = []
    monkeypatch.setattr(imaging, "BACKFILL_PAUSE_S", 0.2)
    monkeypatch.setattr(imaging.time, "sleep", naps.append)
    conn = mfdb.connect(mf_app.config["DB_PATH"])
    imaging.backfill(conn, mf_app.config["DATA_DIR"], T)
    conn.close()
    assert naps == [0.2, 0.2]
