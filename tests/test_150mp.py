"""#82: 150 MP upload cap, reduced-size JPEG decoding, lower HEIC cap. Light on RAM: big sizes are
faked by patching the JPEG header (no pixel data), real decodes use a ~30 MP solid-colour JPEG."""
import io
import struct

import pytest
from PIL import Image

from memoryframe import faces, imaging, storage, upload
from tests.test_upload import jpeg, media_rows, post

HEIC = pytest.importorskip("pillow_heif")


def with_header_size(data, w, h):
    """The same JPEG bytes with the SOFn width/height rewritten (header only: pixels are not real)."""
    b = bytearray(data)
    i = 2
    while i < len(b):
        marker = b[i + 1]
        n = struct.unpack(">H", b[i + 2:i + 4])[0]
        if marker in (0xC0, 0xC1, 0xC2):
            b[i + 5:i + 9] = struct.pack(">HH", h, w)
            return bytes(b)
        i += 2 + n
    raise AssertionError("no SOF marker")


def write(tmp_path, name, data):
    p = tmp_path / name
    p.write_bytes(data)
    return str(p)


def test_defaults_are_150mp_jpeg_and_50mp_heic(monkeypatch):
    monkeypatch.delenv("MEMORYFRAME_MAX_MEGAPIXELS", raising=False)
    monkeypatch.delenv("MEMORYFRAME_MAX_NONJPEG_MEGAPIXELS", raising=False)
    cfg = storage.default_config()
    assert upload.MAX_PIXELS == cfg["MAX_IMAGE_PIXELS"] == 150_000_000
    assert upload.MAX_NONJPEG_PIXELS == cfg["MAX_NONJPEG_PIXELS"] == 50_000_000


def test_env_overrides_both_caps(monkeypatch):
    monkeypatch.setenv("MEMORYFRAME_MAX_MEGAPIXELS", "100")
    monkeypatch.setenv("MEMORYFRAME_MAX_NONJPEG_MEGAPIXELS", "30")
    cfg = storage.default_config()
    assert cfg["MAX_IMAGE_PIXELS"] == 100_000_000 and cfg["MAX_NONJPEG_PIXELS"] == 30_000_000


@pytest.mark.parametrize("raw", ["abc", "0", "-3"])
def test_bad_heic_env_falls_back(monkeypatch, raw):
    monkeypatch.setenv("MEMORYFRAME_MAX_NONJPEG_MEGAPIXELS", raw)
    assert storage.default_config()["MAX_NONJPEG_PIXELS"] == 50_000_000


@pytest.mark.parametrize("w,h", [(12000, 9000), (15000, 10000)])        # 108 MP (phone) and exactly 150 MP
def test_108_and_150mp_headers_are_accepted(tmp_path, w, h):
    p = write(tmp_path, "big.jpg", with_header_size(jpeg(64, 48), w, h))
    assert upload.sniff_image(p) == ".jpg"


def test_just_over_150mp_is_415_with_150_in_message(tmp_path):
    p = write(tmp_path, "big.jpg", with_header_size(jpeg(64, 48), 15001, 10000))
    with pytest.raises(upload.UploadError) as e:
        upload.sniff_image(p)
    assert e.value.status == 415 and "over 150 megapixels" in e.value.message


def test_api_rejects_over_150mp_without_decoding(mf_app, paired, monkeypatch):
    monkeypatch.setattr(Image.Image, "verify", lambda self: pytest.fail("decoded past the cap"))
    r = post(paired, with_header_size(jpeg(64, 48), 16000, 10000))
    assert r.status_code == 415 and "150 megapixels" in r.get_json()["error"]
    assert media_rows(mf_app) == []


def _heic(tmp_path, w=64, h=48):
    p = tmp_path / "p.heic"
    HEIC.register_heif_opener()
    Image.new("RGB", (w, h), (200, 30, 30)).save(p, "HEIF")
    return str(p)


def test_heic_has_its_own_lower_cap(tmp_path):
    p = _heic(tmp_path)                                   # 3072 px
    assert upload.sniff_image(p, 150_000_000, 3072) == ".heic"
    with pytest.raises(upload.UploadError) as e:
        upload.sniff_image(p, 150_000_000, 3071)
    assert e.value.status == 415 and "HEIC" in e.value.message and "JPEG" in e.value.message
    assert upload.sniff_image(p) == ".heic"               # default 50 MP cap: a small HEIC is fine


def test_nonjpeg_cap_never_exceeds_the_jpeg_cap(tmp_path):
    p = _heic(tmp_path)
    with pytest.raises(upload.UploadError):
        upload.sniff_image(p, 3071, 50_000_000)


def test_jpeg_is_not_subject_to_the_nonjpeg_cap(tmp_path):
    p = write(tmp_path, "a.jpg", jpeg(200, 100))
    assert upload.sniff_image(p, 150_000_000, 1000) == ".jpg"


def test_over_pixel_cap_heic_aware(tmp_path):
    h, j = _heic(tmp_path), write(tmp_path, "a.jpg", jpeg(64, 48))
    assert imaging.over_pixel_cap(h, 150_000_000, 3071) and not imaging.over_pixel_cap(h, 150_000_000)
    assert not imaging.over_pixel_cap(j, 150_000_000, 10)


def test_pillow_bomb_limit_is_raised_to_the_cap():
    assert Image.MAX_IMAGE_PIXELS >= 150_000_000


# ---- reduced-size JPEG decoding ----

@pytest.fixture(scope="module")
def big_jpeg(tmp_path_factory):
    p = tmp_path_factory.mktemp("big") / "big.jpg"
    Image.new("RGB", (6400, 4800), (30, 90, 160)).save(p, "JPEG", quality=70)       # 30.7 MP, solid colour
    return str(p)


def test_display_copy_of_a_big_jpeg_decodes_at_reduced_size(big_jpeg, tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr(faces, "detect", lambda im: seen.append(im.size) or [])
    dst = str(tmp_path / "d.jpg")
    w, h, _ = imaging.make_display_copy(big_jpeg, dst, (1000, 600))
    assert (w, h) == (1000, 600)                         # still exactly the target
    assert seen == [(3200, 2400)]                        # 1/2 scale draft, >= 3x target; never the full 6400x4800


def test_thumb_of_a_big_jpeg_decodes_at_reduced_size(big_jpeg, tmp_path, monkeypatch):
    sizes = []
    orig = imaging.ImageOps.fit
    monkeypatch.setattr(imaging.ImageOps, "fit", lambda im, *a, **k: sizes.append(im.size) or orig(im, *a, **k))
    dst = str(tmp_path / "t.jpg")
    imaging.make_thumb(big_jpeg, dst)
    assert sizes == [(800, 600)]                         # 1/8 scale; the full image is never loaded
    with Image.open(dst) as t:
        assert t.size == (240, 240)


def test_draft_respects_exif_orientation(tmp_path):
    # stored 6400x3200, rotated 90 degrees when shown (3200 wide x 6400 tall): the draft must still
    # leave the DISPLAYED width >= 3x target width (3000), so it cannot go below 1/1 here.
    exif = Image.Exif()
    exif[0x0112] = 6
    p = str(tmp_path / "rot.jpg")
    Image.new("RGB", (6400, 3200), (1, 2, 3)).save(p, "JPEG", exif=exif)
    w, h, _ = imaging.make_display_copy(p, str(tmp_path / "o.jpg"), (1000, 600))
    assert (w, h) == (1000, 600)
    with Image.open(p) as im:
        imaging.draft_jpeg(im, (3000, 1800))
        assert im.size == (6400, 3200)                   # swapped want (1800, 3000): scale 1


def test_small_jpeg_and_non_jpeg_are_not_reduced(tmp_path):
    p = write(tmp_path, "s.jpg", jpeg(4000, 3000))       # 12 MP: below 2x the 3000x1800 request
    with Image.open(p) as im:
        imaging.draft_jpeg(im, (3000, 1800))
        assert im.size == (4000, 3000)
    png = str(tmp_path / "x.png")
    Image.new("RGB", (6400, 480)).save(png)
    with Image.open(png) as im:
        assert imaging.draft_jpeg(im, (100, 100)).size == (6400, 480)


def test_draft_failure_falls_back_to_full_decode(tmp_path, monkeypatch):
    p = write(tmp_path, "s.jpg", jpeg(800, 600))
    monkeypatch.setattr(Image.Image, "draft", lambda *a, **k: (_ for _ in ()).throw(ValueError("x")), raising=False)
    imaging.make_display_copy(p, str(tmp_path / "o.jpg"), (1000, 600))
    assert (tmp_path / "o.jpg").exists()
