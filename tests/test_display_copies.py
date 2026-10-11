"""MF-15: display copies cover the target, stay < 300 KB, strip GPS, never touch originals."""
import io
import os

import pytest
from PIL import Image

from memoryframe import __main__ as cli
from memoryframe import db as mfdb
from memoryframe import imaging, library


def jpeg(w, h, noise=False, exif=None):
    if noise:
        im = Image.frombytes("RGB", (w, h), os.urandom(w * h * 3))
    else:
        im = Image.new("RGB", (w, h))
        for x in range(0, w, 50):
            im.paste((x % 256, (x * 3) % 256, 120), (x, 0, x + 25, h))
    b = io.BytesIO()
    im.save(b, "JPEG", quality=95, **({"exif": exif} if exif else {}))
    return b.getvalue()


def gps_exif():
    ex = Image.Exif()
    ex[0x0112] = 6   # orientation: rotate 90
    ex[0x8825] = {1: "S", 2: (27.0, 28.0, 0.0), 3: "E", 4: (153.0, 2.0, 0.0)}
    return ex.tobytes()


def stored(app, name):
    conn = mfdb.connect(app.config["DB_PATH"])
    try:
        return conn.execute("SELECT stored_name FROM media WHERE original_name = ?", (name,)).fetchone()[0]
    finally:
        conn.close()


@pytest.mark.parametrize("size,target,expected", [
    ((4000, 3000), (800, 600), (800, 600)),
    ((3000, 4000), (800, 600), (800, 1067)),     # portrait: width covers 800
    ((4000, 1000), (800, 600), (2400, 600)),     # panorama: height covers 600
    ((640, 480), (800, 600), (640, 480)),        # never upscaled
    ((4000, 3000), (1000, 750), (1000, 750)),
])
def test_cover_size(size, target, expected):
    assert imaging.cover_size(size, target) == expected


def test_parse_target():
    assert imaging.parse_target("1000x750") == (1000, 750)
    with pytest.raises(ValueError):
        imaging.parse_target("big")


def test_upload_gets_cover_copy_under_300kb_and_original_untouched(mf_app, lib, client):
    data = jpeg(4000, 3000, noise=True)
    lib.add("big.jpg", data)
    name = stored(mf_app, "big.jpg")
    r = client.get(f"/uploads/{name}")
    assert r.status_code == 200
    im = Image.open(io.BytesIO(r.data))
    assert im.format == "JPEG" and im.width >= 800 and im.height >= 600
    assert len(r.data) <= imaging.MAX_BYTES
    orig = os.path.join(mf_app.config["DATA_DIR"], "originals", name)
    assert open(orig, "rb").read() == data
    assert os.path.exists(os.path.join(mf_app.config["DATA_DIR"], "display", imaging.display_name(name)))


def test_exif_orientation_applied_and_gps_stripped(mf_app, lib, client):
    lib.add("gps.jpg", jpeg(1600, 1200, exif=gps_exif()))
    r = client.get(f"/uploads/{stored(mf_app, 'gps.jpg')}")
    im = Image.open(io.BytesIO(r.data))
    assert im.size == (1000, 750)                     # portrait after rotation, cover-cropped
    # source stripes run vertically; after the 90-degree EXIF rotation they must run horizontally
    rgb = im.convert("RGB")
    row = {rgb.getpixel((x, 300))[0] // 16 for x in range(50, 750, 50)}
    col = {rgb.getpixel((400, y))[0] // 16 for y in range(20, 580, 20)}
    assert len(row) <= 2 < len(col)
    ex = im.getexif()
    assert 0x8825 not in ex and not ex.get_ifd(0x8825)


def test_add_file_creates_copy_when_target_given(mf_app, tmp_path):
    src = tmp_path / "x.jpg"
    src.write_bytes(jpeg(2000, 1500))
    conn = mfdb.connect(mf_app.config["DB_PATH"])
    try:
        name = library.add_file(conn, mf_app.config["DATA_DIR"], str(src), "x.jpg", "upload",
                                display_target=(800, 600))
    finally:
        conn.close()
    with Image.open(os.path.join(mf_app.config["DATA_DIR"], "display", imaging.display_name(name))) as im:
        assert im.size == (800, 600)


def test_gif_and_broken_images_fall_back_to_original(mf_app, lib, client):
    b = io.BytesIO()
    Image.new("P", (10, 10)).save(b, "GIF")
    lib.add("a.gif", b.getvalue())
    lib.add("broken.jpg", b"not an image")
    r = client.get(f"/uploads/{stored(mf_app, 'a.gif')}")
    assert r.status_code == 200 and r.data == b.getvalue()
    r = client.get(f"/uploads/{stored(mf_app, 'broken.jpg')}")
    assert r.status_code == 200 and r.data == b"not an image"


def test_png_with_alpha_becomes_jpeg(mf_app, lib, client):
    b = io.BytesIO()
    Image.new("RGBA", (1200, 900), (255, 0, 0, 128)).save(b, "PNG")
    lib.add("a.png", b.getvalue())
    r = client.get(f"/uploads/{stored(mf_app, 'a.png')}")
    assert Image.open(io.BytesIO(r.data)).format == "JPEG"


def test_heic_accepted_and_converted(mf_app, lib, client):
    pillow_heif = pytest.importorskip("pillow_heif")
    b = io.BytesIO()
    pillow_heif.from_pillow(Image.new("RGB", (1600, 1200), (0, 128, 0))).save(b, quality=80)
    lib.add("phone.heic", b.getvalue())
    r = client.get(f"/uploads/{stored(mf_app, 'phone.heic')}")
    im = Image.open(io.BytesIO(r.data))
    assert im.format == "JPEG" and im.size == (1000, 750)


def test_backfill_cli(mf_app, lib, monkeypatch, capsys):
    lib.add("one.jpg", jpeg(1600, 1200))
    lib.add("two.jpg", jpeg(1200, 1600))
    display = os.path.join(mf_app.config["DATA_DIR"], "display")
    assert os.listdir(display) == []        # library helper adds without copies
    monkeypatch.setenv("MEMORYFRAME_DISPLAY_SIZE", "1000x750")
    monkeypatch.setenv("MEMORYFRAME_DATA_DIR", mf_app.config["DATA_DIR"])
    assert cli.main(["display-copies"]) == 0
    assert "2 made, 0 failed" in capsys.readouterr().out
    assert len(os.listdir(display)) == 2
    assert cli.main(["display-copies"]) == 0
    assert "0 made" in capsys.readouterr().out
    assert cli.main(["display-copies", "--force"]) == 0
    assert "2 made" in capsys.readouterr().out


def test_noise_image_still_under_limit(tmp_path):
    src = tmp_path / "n.png"
    Image.frombytes("RGB", (1000, 750), os.urandom(1000 * 750 * 3)).save(src)
    w, h, size = imaging.make_display_copy(str(src), str(tmp_path / "n.jpg"), (1000, 750), max_bytes=150_000)
    assert size <= 150_000


def test_backfill_counts_failures(mf_app, lib):
    lib.add("bad.jpg", b"garbage")
    conn = mfdb.connect(mf_app.config["DB_PATH"])
    try:
        assert imaging.backfill(conn, mf_app.config["DATA_DIR"], (800, 600)) == (0, 1)
    finally:
        conn.close()



# ---------- portrait copies are capped to what cover-crop shows (1000x750) ----------

@pytest.mark.parametrize("w,h", [(3000, 4000), (1080, 2312), (1000, 5000), (600, 3000), (4000, 3000), (8000, 1000)])
def test_display_copy_never_exceeds_target(tmp_path, w, h):
    src, dst = tmp_path / "s.jpg", tmp_path / "d.jpg"
    src.write_bytes(jpeg(w, h))
    cw, ch, n = imaging.make_display_copy(str(src), str(dst), (1000, 750))
    im = Image.open(dst)
    assert (cw, ch) == im.size and n == os.path.getsize(dst) <= imaging.MAX_BYTES
    assert cw <= 1000 and ch <= 750, im.size
    if w >= 1000 and h >= 750:
        assert im.size == (1000, 750)                       # still fills the screen
    assert (w, h) != (3000, 4000) or im.size == (1000, 750)  # was 1000x1333
    assert (w, h) != (1080, 2312) or im.size == (1000, 750)  # was 1000x2141


def test_noisy_very_tall_portrait_under_300kb(tmp_path):
    src, dst = tmp_path / "s.jpg", tmp_path / "d.jpg"
    src.write_bytes(jpeg(1000, 5000, noise=True))
    w, h, n = imaging.make_display_copy(str(src), str(dst), (1000, 750))
    assert n <= imaging.MAX_BYTES and w <= 1000 and h <= 750


def test_default_display_size_is_1000x750(mf_app, monkeypatch):
    from memoryframe import storage
    monkeypatch.delenv("MEMORYFRAME_DISPLAY_SIZE", raising=False)
    assert storage.default_config()["DISPLAY_SIZE"] == "1000x750"
    assert imaging.DEFAULT_TARGET == "1000x750"
    assert mf_app.config["DISPLAY_TARGET"] == (1000, 750)


@pytest.mark.parametrize("w,h", [(4000, 3000), (3000, 4000), (1080, 2312)])
def test_noisy_photos_under_300kb_at_1000x750(tmp_path, w, h):
    src, dst = tmp_path / "s.jpg", tmp_path / "d.jpg"
    src.write_bytes(jpeg(w, h, noise=True))
    cw, ch, n = imaging.make_display_copy(str(src), str(dst), (1000, 750))
    assert n <= imaging.MAX_BYTES <= 300 * 1024 and cw <= 1000 and ch <= 750



@pytest.mark.parametrize("value,expected", [("1000x750", (1000, 750)), ("320x320", (320, 320)),
                                            ("4000x4000", (4000, 4000)), (" 800X600 ", (800, 600))])
def test_parse_target_bounds_ok(value, expected):
    assert imaging.parse_target(value) == expected


@pytest.mark.parametrize("value", ["10x10", "319x750", "1000x319", "4001x750", "99999x99999", "0x0", "abc", ""])
def test_parse_target_out_of_range_falls_back(value, caplog):
    with pytest.raises(ValueError):
        imaging.parse_target(value)
    assert imaging.target_or_default(value) == (1000, 750)
    assert "using 1000x750" in caplog.text


def test_app_falls_back_on_bad_display_size(tmp_path, monkeypatch, caplog):
    from memoryframe import create_app
    monkeypatch.setenv("MEMORYFRAME_DISPLAY_SIZE", "99999x99999")
    monkeypatch.setenv("MEMORYFRAME_DATA_DIR", str(tmp_path / "data"))
    app = create_app({"TESTING": True})
    assert app.config["DISPLAY_TARGET"] == (1000, 750) and "99999x99999" in caplog.text


def test_force_regeneration_leaves_originals_unchanged(mf_app, lib, monkeypatch):
    import hashlib
    lib.add("a.jpg", jpeg(3000, 4000))
    lib.add("b.jpg", jpeg(1600, 1200, exif=gps_exif()))
    odir = os.path.join(mf_app.config["DATA_DIR"], "originals")
    before = {f: hashlib.sha256(open(os.path.join(odir, f), "rb").read()).hexdigest() for f in os.listdir(odir)}
    monkeypatch.setenv("MEMORYFRAME_DATA_DIR", mf_app.config["DATA_DIR"])
    assert cli.main(["display-copies", "--force"]) in (0, None)
    after = {f: hashlib.sha256(open(os.path.join(odir, f), "rb").read()).hexdigest() for f in os.listdir(odir)}
    assert before == after and len(after) == 2
