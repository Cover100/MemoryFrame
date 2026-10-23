"""#82 review fixes: CMYK draft decode, GIF cap, metadata stripping (COM/XMP/IPTC/bad ICC), fsync,
start-up tmp sweep, env cap parsing/clamping, lock released after an exception."""
import errno
import io
import os
import struct

import pytest
from PIL import Image

from memoryframe import db as mfdb
from memoryframe import imaging, storage, upload
from tests.test_150mp import with_header_size, write
from tests.test_stored_copy import files_in, is_red, marked
from tests.test_upload import jpeg, media_rows, post


def segments(data):
    """JPEG marker bytes present before the scan (e.g. {0xFE, 0xE1})."""
    out, i = set(), 2
    while i + 4 <= len(data) and data[i] == 0xFF and data[i + 1] != 0xDA:
        out.add(data[i + 1])
        i += 2 + struct.unpack(">H", data[i + 2:i + 4])[0]
    return out


def inject(data, marker, payload):
    return data[:2] + bytes([0xFF, marker]) + struct.pack(">H", len(payload) + 2) + payload + data[2:]


def dirty_jpeg(w=3000, h=2000):
    d = marked(w, h, quality=90, comment=b"SECRET COMMENT")
    d = inject(d, 0xE1, b"http://ns.adobe.com/xap/1.0/\0<x:xmpmeta>SECRET XMP</x:xmpmeta>")
    d = inject(d, 0xED, b"Photoshop 3.0\x008BIM SECRET IPTC")
    return d


# ---------------- CMYK ----------------

def test_cmyk_jpeg_is_drafted_and_stored_as_rgb(mf_app, paired, monkeypatch):
    seen = []
    orig = imaging._resizable
    monkeypatch.setattr(imaging, "_resizable", lambda im: seen.append((im.mode, im.size)) or orig(im))
    b = io.BytesIO()
    Image.new("CMYK", (6400, 4800), (0, 255, 255, 0)).save(b, "JPEG", quality=70)       # ~red in CMYK
    assert post(paired, b.getvalue()).status_code == 201
    assert seen[0] == ("CMYK", (1600, 1200))                    # 1/4 scale, not 6400x4800 x 4 bytes
    (row,) = media_rows(mf_app)
    im = Image.open(os.path.join(mf_app.config["DATA_DIR"], "originals", row["stored_name"]))
    assert im.mode == "RGB" and im.size == (1500, 1125)


@pytest.mark.parametrize("mode", ["RGB", "L", "CMYK"])
def test_draft_jpeg_scales_rgb_l_and_cmyk(tmp_path, mode):
    p = tmp_path / "a.jpg"
    Image.new(mode, (3200, 2400)).save(p, "JPEG")
    with Image.open(p) as im:
        imaging.draft_jpeg(im, (1000, 600))
        assert im.mode == mode and im.size == (1600, 1200)


# ---------------- GIF cap ----------------

def gif_with_size(w, h):
    b = io.BytesIO()
    Image.new("P", (16, 16)).save(b, "GIF")
    d = bytearray(b.getvalue())
    d[6:10] = struct.pack("<HH", w, h)                     # logical screen size (header only)
    return bytes(d)


def test_gif_uses_the_non_jpeg_cap_and_the_error_quotes_it(tmp_path):
    ok = write(tmp_path, "ok.gif", gif_with_size(7000, 7000))              # 49 MP: within the 50 MP cap
    assert upload.sniff_image(ok) == ".gif"
    big = write(tmp_path, "big.gif", gif_with_size(7100, 7100))            # 50.4 MP
    with pytest.raises(upload.UploadError) as e:
        upload.sniff_image(big)
    assert e.value.status == 415 and "GIF" in e.value.message and "50 megapixels" in e.value.message


def test_gif_cap_follows_a_lower_non_jpeg_cap(tmp_path):
    p = write(tmp_path, "a.gif", gif_with_size(2000, 2000))                 # 4 MP
    with pytest.raises(upload.UploadError) as e:
        upload.sniff_image(p, 150_000_000, 3_000_000)
    assert "3 megapixels" in e.value.message
    assert upload.pixel_cap("GIF", 150_000_000, 50_000_000) == 50_000_000
    assert upload.pixel_cap("JPEG", 150_000_000, 50_000_000) == 150_000_000
    assert imaging.over_pixel_cap(p, 150_000_000, 3_000_000)


def test_gif_over_the_cap_is_415_through_the_api(mf_app, paired):
    r = post(paired, gif_with_size(8000, 7000), "big.gif")
    assert r.status_code == 415 and "GIF" in r.get_json()["error"]
    assert media_rows(mf_app) == [] and files_in(mf_app, "tmp") == []


def test_small_gif_still_kept_as_sent(mf_app, paired):
    b = io.BytesIO()
    Image.new("P", (64, 48)).save(b, "GIF")
    assert post(paired, b.getvalue(), "a.gif").status_code == 201
    assert media_rows(mf_app)[0]["stored_name"].endswith(".gif")


# ---------------- metadata ----------------

@pytest.mark.parametrize("big", [True, False])
def test_comment_xmp_iptc_exif_are_not_in_stored_copy_or_thumbnail(mf_app, paired, big):
    data = dirty_jpeg(*((3000, 2000) if big else (800, 500)))
    assert {0xFE, 0xE1, 0xED} <= segments(data)
    assert post(paired, data).status_code == 201
    (row,) = media_rows(mf_app)
    for sub, name in (("originals", row["stored_name"]), ("thumbs", row["stored_name"]),
                      ("display", row["stored_name"])):
        raw = open(os.path.join(mf_app.config["DATA_DIR"], sub, name), "rb").read()
        assert b"SECRET" not in raw, sub
        if sub != "display":                       # the display copy carries our own rules comment
            assert not ({0xFE, 0xE1, 0xED, 0xE2} & segments(raw)), sub


def test_thumbnail_of_an_old_original_drops_the_comment(tmp_path):
    src = tmp_path / "old.jpg"
    src.write_bytes(dirty_jpeg(900, 600))
    imaging.make_thumb(str(src), str(tmp_path / "t.jpg"))
    raw = (tmp_path / "t.jpg").read_bytes()
    assert b"SECRET" not in raw and not ({0xFE, 0xE1, 0xED} & segments(raw))


@pytest.mark.parametrize("profile", [b"not an icc profile at all", b"\0" * 200, b"acsp" * 50])
def test_bad_icc_profile_does_not_break_the_upload(mf_app, paired, profile):
    d = marked(2400, 1600, quality=90, icc_profile=profile)
    assert post(paired, d).status_code == 201
    (row,) = media_rows(mf_app)
    raw = open(os.path.join(mf_app.config["DATA_DIR"], "originals", row["stored_name"]), "rb").read()
    assert 0xE2 not in segments(raw)
    im = Image.open(os.path.join(mf_app.config["DATA_DIR"], "originals", row["stored_name"]))
    assert is_red(im.convert("RGB").getpixel((8, 8)))


def test_rotation_still_applied_to_alpha_png_and_palette(mf_app, paired):
    """exif_transpose is gone (it copied the full image): rotation is applied to the small image."""
    for fmt, kw in (("PNG", {}),):
        im = Image.new("RGBA", (320, 160), (20, 40, 200, 255))
        im.paste((255, 0, 0, 255), (0, 0, 80, 40))
        exif = Image.Exif()
        exif[0x0112] = 6
        b = io.BytesIO()
        im.save(b, fmt, exif=exif)
        assert post(paired, b.getvalue(), "r.png").status_code == 201
    row = media_rows(mf_app)[-1]
    out = Image.open(os.path.join(mf_app.config["DATA_DIR"], "originals", row["stored_name"]))
    assert out.size == (160, 320) and is_red(out.getpixel((out.width - 8, 8)))


# ---------------- fsync / lock / sweep ----------------

def test_stored_copy_and_directory_are_fsynced_before_the_row_is_committed(mf_app, paired, monkeypatch):
    events = []
    real_fsync = os.fsync
    monkeypatch.setattr(os, "fsync", lambda fd: events.append("fsync") or real_fsync(fd))
    real_connect = mfdb.connect

    class Spy:
        def __init__(self, c):
            self.c = c

        def __getattr__(self, n):
            return getattr(self.c, n)

        def commit(self):
            events.append("commit")
            return self.c.commit()

    from memoryframe import api
    monkeypatch.setattr(api, "get_db", lambda: Spy(real_connect(mf_app.config["DB_PATH"])), raising=False)
    assert post(paired, jpeg()).status_code == 201
    assert events.count("fsync") >= 2                      # the file, and the originals/ directory
    first_commit = events.index("commit")
    assert events[:first_commit].count("fsync") >= 2


def test_fsync_dir_is_best_effort(tmp_path, monkeypatch):
    imaging.fsync_dir(str(tmp_path / "missing"))           # must not raise
    monkeypatch.setattr(os, "fsync", lambda fd: (_ for _ in ()).throw(OSError(errno.EINVAL, "no")))
    imaging.fsync_dir(str(tmp_path))


def test_lock_is_released_after_an_exception(tmp_path, monkeypatch):
    bad = tmp_path / "bad.jpg"
    bad.write_bytes(b"\xff\xd8\xff\xe0" + b"junk" * 10)
    with pytest.raises(imaging.ImageUnreadable):
        imaging.make_stored_copy(str(bad), str(tmp_path / "o.jpg"), (1000, 600))
    assert imaging._store_lock.acquire(blocking=False)
    imaging._store_lock.release()

    good = tmp_path / "good.jpg"
    good.write_bytes(jpeg(2000, 1500))
    monkeypatch.setattr(imaging, "_resizable", lambda im: (_ for _ in ()).throw(MemoryError()))
    with pytest.raises(imaging.ImageUnreadable):
        imaging.make_stored_copy(str(good), str(tmp_path / "o.jpg"), (1000, 600))
    assert imaging._store_lock.acquire(blocking=False)
    imaging._store_lock.release()
    monkeypatch.undo()
    assert imaging.make_stored_copy(str(good), str(tmp_path / "o.jpg"), (1000, 600)) == (1500, 1125)


def test_startup_sweep_removes_tmp_leftovers_but_not_trash(mf_app):
    d = mf_app.config["DATA_DIR"]
    tmp = os.path.join(d, "tmp")
    left = ["upload-" + "a" * 32, "b" * 32 + ".jpg", "c" * 32 + ".jpg.tmp", "d" * 32 + ".gif", "x.tmp"]
    for n in left:
        open(os.path.join(tmp, n), "wb").write(b"x" * 10)
    os.makedirs(os.path.join(tmp, "trash"), exist_ok=True)
    open(os.path.join(tmp, "keep.txt"), "w").write("not ours")
    conn = mfdb.connect(mf_app.config["DB_PATH"])
    try:
        imaging.sweep(conn, d)
    finally:
        conn.close()
    assert sorted(os.listdir(tmp)) == ["keep.txt", "trash"]


# ---------------- env caps ----------------

@pytest.mark.parametrize("raw,want", [("", 50), ("abc", 50), ("-5", 50), ("0", 50), ("50.5", 50), ("12", 12),
                                      ("150", 150), ("151", 150), ("99999999", 150)])
def test_env_cap_parsing_and_clamp(monkeypatch, raw, want):
    monkeypatch.setenv("MEMORYFRAME_MAX_NONJPEG_MEGAPIXELS", raw)
    assert storage.default_config()["MAX_NONJPEG_PIXELS"] == want * 1_000_000


def test_all_caps_are_clamped(monkeypatch):
    for k in ("MEMORYFRAME_MAX_MEGAPIXELS", "MEMORYFRAME_MAX_NONJPEG_MEGAPIXELS"):
        monkeypatch.setenv(k, "99999")
    cfg = storage.default_config()
    assert cfg["MAX_IMAGE_PIXELS"] == cfg["MAX_NONJPEG_PIXELS"] == 150_000_000
    monkeypatch.delenv("MEMORYFRAME_MAX_NONJPEG_MEGAPIXELS")
    assert storage.default_config()["MAX_NONJPEG_PIXELS"] == 50_000_000


def test_over_the_upload_size_limit_says_to_try_a_smaller_photo(mf_app, paired):
    mf_app.config["MAX_CONTENT_LENGTH"] = 20 * 1024
    r = post(paired, os.urandom(60 * 1024), "big.jpg")
    assert r.status_code == 413
    assert "limit is 0 MB" in r.get_json()["error"] or "MB per photo" in r.get_json()["error"]
    assert "smaller photo" in r.get_json()["error"]
