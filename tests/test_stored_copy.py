"""#82: uploads are stored as ONE shrunk, upright, metadata-free JPEG; no full-size original is kept."""
import hashlib
import io
import os

import pytest
from PIL import Image

from memoryframe import imaging, library, upload
from tests.test_upload import PHONE, jpeg, media_rows, post


def files_in(app, sub):
    d = os.path.join(app.config["DATA_DIR"], sub)
    return sorted(os.listdir(d)) if os.path.isdir(d) else []


def stored(app, row):
    return Image.open(os.path.join(app.config["DATA_DIR"], "originals", row["stored_name"]))


def marked(w, h, orientation=None, fmt="JPEG", **kw):
    """w x h photo with a red block in the top-left corner (to see how it was rotated)."""
    im = Image.new("RGB", (w, h), (20, 40, 200))
    im.paste((255, 0, 0), (0, 0, w // 4, h // 4))
    b = io.BytesIO()
    if orientation:
        exif = Image.Exif()
        exif[0x0112] = orientation
        kw["exif"] = exif
    im.save(b, fmt, **kw)
    return b.getvalue()


def is_red(px):
    return px[0] > 200 and px[1] < 60 and px[2] < 60


def test_upload_keeps_one_small_upright_jpeg_and_no_original(mf_app, paired):
    data = marked(4000, 3000, orientation=6, quality=95)             # shown rotated: 3000 wide x 4000 tall
    r = post(paired, data, "IMG_1.jpg")
    assert r.status_code == 201, r.get_json()
    (row,) = media_rows(mf_app)
    assert row["stored_name"].endswith(".jpg") and row["original_name"] == "IMG_1.jpg"
    assert files_in(mf_app, "originals") == [row["stored_name"]]       # exactly one file...
    path = os.path.join(mf_app.config["DATA_DIR"], "originals", row["stored_name"])
    assert open(path, "rb").read() != data and os.path.getsize(path) < len(data)      # ...and it is not the upload
    assert row["size_bytes"] == os.path.getsize(path)
    assert row["sha256"] == hashlib.sha256(data).hexdigest()           # duplicate check = hash as uploaded
    for sub in ("originals", "display", "thumbs", "tmp"):               # nothing anywhere holds the upload
        for f in files_in(mf_app, sub):
            fp = os.path.join(mf_app.config["DATA_DIR"], sub, f)
            if os.path.isfile(fp):
                assert open(fp, "rb").read() != data
    with stored(mf_app, row) as im:
        assert im.format == "JPEG" and im.size == (1500, 2000)         # covers 1500x900, never cropped
        assert len(im.getexif()) == 0 and "icc_profile" not in im.info and "exif" not in im.info
        assert is_red(im.getpixel((im.width - 10, 10))) and not is_red(im.getpixel((10, 10)))    # upright


@pytest.mark.parametrize("orientation,red_at", [(1, "tl"), (3, "br"), (6, "tr"), (8, "bl")])
def test_exif_rotation_is_applied_to_the_stored_copy(mf_app, paired, orientation, red_at):
    assert post(paired, marked(1600, 1200, orientation, quality=95)).status_code == 201
    (row,) = media_rows(mf_app)
    with stored(mf_app, row) as im:
        w, h = im.size
        spot = {"tl": (8, 8), "tr": (w - 8, 8), "bl": (8, h - 8), "br": (w - 8, h - 8)}[red_at]
        assert is_red(im.getpixel(spot)), (im.size, red_at)
        assert (w > h) == (orientation in (1, 3))


def test_heic_is_stored_as_upright_jpeg(mf_app, paired):
    # pillow-heif applies the container's own rotation (irot) and resets the EXIF tag, so exif_transpose
    # cannot rotate a second time. (A real iPhone HEIC with irot is on the on-device checklist: pillow-heif
    # cannot write one here.)
    pytest.importorskip("pillow_heif").register_heif_opener()
    data = marked(320, 160, fmt="HEIF", quality=95)
    r = post(paired, data, "p.heic")
    assert r.status_code == 201, r.get_json()
    (row,) = media_rows(mf_app)
    assert row["stored_name"].endswith(".jpg") and row["original_name"] == "p.jpg"
    with stored(mf_app, row) as im:
        assert im.size == (320, 160) and is_red(im.getpixel((8, 8)))


def test_stored_copy_is_not_cropped_and_display_copy_crops_at_display_time(mf_app, paired):
    assert post(paired, jpeg(6000, 2000)).status_code == 201           # 3:1 panorama
    (row,) = media_rows(mf_app)
    with stored(mf_app, row) as im:
        assert im.size == (2700, 900)                                   # same 3:1, only scaled
    with Image.open(os.path.join(mf_app.config["DATA_DIR"], "display", imaging.display_name(row["stored_name"]))) as d:
        assert d.size == (1000, 600)                                    # the 5:3 crop is made from it


def test_small_photos_are_not_upscaled(mf_app, paired):
    assert post(paired, jpeg(800, 600)).status_code == 201
    with stored(mf_app, media_rows(mf_app)[0]) as im:
        assert im.size == (800, 600)


def test_png_with_transparency_is_stored_as_flat_jpeg(mf_app, paired):
    b = io.BytesIO()
    Image.new("RGBA", (400, 300), (255, 255, 255, 0)).save(b, "PNG")
    assert post(paired, b.getvalue(), "x.png").status_code == 201
    (row,) = media_rows(mf_app)
    assert row["stored_name"].endswith(".jpg") and row["original_name"] == "x.jpg"
    with stored(mf_app, row) as im:
        assert im.format == "JPEG" and im.getpixel((5, 5))[0] < 10     # transparent -> black, like the frame


def test_gif_is_kept_as_sent(mf_app, paired):
    b = io.BytesIO()
    Image.new("P", (40, 30), 1).save(b, "GIF")
    assert post(paired, b.getvalue(), "a.gif").status_code == 201
    (row,) = media_rows(mf_app)
    assert row["stored_name"].endswith(".gif")
    assert open(os.path.join(mf_app.config["DATA_DIR"], "originals", row["stored_name"]), "rb").read() == b.getvalue()


def test_same_upload_twice_is_a_duplicate(mf_app, paired):
    data = jpeg(3000, 2000, (5, 6, 7))
    assert post(paired, data).status_code == 201
    r = post(paired, data)
    assert r.status_code == 200 and r.get_json()["status"] == "duplicate"
    assert len(media_rows(mf_app)) == 1 and len(files_in(mf_app, "originals")) == 1


def test_truncated_jpeg_is_415_and_leaves_nothing(mf_app, paired):
    data = marked(1600, 1200, quality=95)
    r = post(paired, data[: len(data) // 2])
    assert r.status_code == 415 and r.get_json()["error"] == upload.NOT_A_PHOTO
    assert media_rows(mf_app) == [] and files_in(mf_app, "originals") == []
    assert [f for f in files_in(mf_app, "tmp") if f != "trash"] == []


def test_big_jpeg_is_decoded_at_reduced_size(mf_app, paired, monkeypatch):
    """A 30 MP JPEG is decoded at 1/4 scale (1600x1200), never at 6400x4800 (~92 MB)."""
    seen = []
    orig = imaging._resizable
    monkeypatch.setattr(imaging, "_resizable", lambda im: seen.append(im.size) or orig(im))   # size after draft
    b = io.BytesIO()
    Image.new("RGB", (6400, 4800), (30, 90, 160)).save(b, "JPEG", quality=70)
    assert post(paired, b.getvalue()).status_code == 201
    assert seen and seen[0] == (1600, 1200)
    with stored(mf_app, media_rows(mf_app)[0]) as im:
        assert im.size == (1500, 1125)


def test_only_one_photo_is_decoded_at_a_time(tmp_path, monkeypatch):
    import threading
    import time
    inside, worst = [0], [0]
    orig = imaging._resizable

    def slow(im, *a, **k):
        inside[0] += 1
        worst[0] = max(worst[0], inside[0])
        time.sleep(0.05)
        out = orig(im)
        inside[0] -= 1
        return out

    monkeypatch.setattr(imaging, "_resizable", slow)
    src = tmp_path / "a.jpg"
    src.write_bytes(jpeg(900, 600))
    ts = [threading.Thread(target=imaging.make_stored_copy, args=(str(src), str(tmp_path / f"o{i}.jpg"), (1000, 600)))
          for i in range(4)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert worst[0] == 1


def test_over_the_cap_error_quotes_the_number(mf_app, paired):
    mf_app.config["MAX_IMAGE_PIXELS"] = 100_000_000
    from tests.test_150mp import with_header_size
    r = post(paired, with_header_size(jpeg(64, 48), 12000, 9000))       # 108 MP > a 100 MP cap
    assert r.status_code == 415 and "over 100 megapixels" in r.get_json()["error"]


def test_png_has_the_lower_non_jpeg_cap(tmp_path):
    p = tmp_path / "a.png"
    Image.new("RGB", (100, 100)).save(p)
    assert upload.sniff_image(str(p), 150_000_000, 10_000) == ".png"
    with pytest.raises(upload.UploadError) as e:
        upload.sniff_image(str(p), 150_000_000, 9_999)
    assert e.value.status == 415 and "PNG" in e.value.message and "JPEG" in e.value.message and "0 megapixels" in e.value.message


def test_storage_estimate_uses_small_photos(mf_app):
    assert mf_app.config["AVG_PHOTO_BYTES"] == 1024 * 1024


def test_add_file_without_store_target_still_copies_byte_identical(mf_app, tmp_path):
    from memoryframe import db as mfdb
    src = tmp_path / "old.jpg"
    src.write_bytes(jpeg(2000, 1500))
    conn = mfdb.connect(mf_app.config["DB_PATH"])
    try:
        name = library.add_file(conn, mf_app.config["DATA_DIR"], str(src), "old.jpg", "legacy")
    finally:
        conn.close()
    assert open(os.path.join(mf_app.config["DATA_DIR"], "originals", name), "rb").read() == src.read_bytes()


def test_upload_page_tells_the_browser_the_stored_size(paired):
    html = paired.get("/admin/upload", environ_base=PHONE).get_data(as_text=True)
    assert 'data-store-w="1500"' in html and 'data-store-h="900"' in html
    assert "full-size original isn" in html
