"""Display copies (MF-15).

For each image in originals/ a JPEG display copy is written to display/<stem>.jpg:
  - EXIF orientation applied, then all metadata (EXIF/GPS/XMP) dropped;
  - scaled so it still COVERS the target (both sides >= target, aspect kept,
    no crop) - the slideshow uses CSS object-fit: cover, so a "fit inside" copy
    would be upscaled and soft. Images already smaller are never upscaled;
  - under MAX_BYTES (JPEG quality steps down; as a last resort the image shrinks).
Originals are only ever read. Animated GIFs keep being served as originals.
HEIC/HEIF (Android/iPhone) is read through pillow-heif when it is installed.
"""
import logging
import re
import os
import threading
import time

log = logging.getLogger("memoryframe.imaging")

DEFAULT_TARGET = "1000x600"    # user decision 2026-10-11: exactly 5:3 = the visible window
MAX_BYTES = 300 * 1024
QUALITIES = (85, 80, 75, 70, 65, 60, 55, 50)

try:
    from PIL import Image, ImageOps
except ImportError:            # pragma: no cover - Pillow is in requirements.txt
    Image = None

try:
    import pillow_heif
    pillow_heif.register_heif_opener()
    HEIF = True
except ImportError:            # pragma: no cover
    HEIF = False

_lock = threading.Lock()

# #82: allow photos up to the 150 MP upload cap without Pillow's DecompressionBombWarning (default
# warns above 89 MP, errors above 2x). The real guard is the header check in upload.sniff_image.
if Image is not None and Image.MAX_IMAGE_PIXELS is not None and Image.MAX_IMAGE_PIXELS < 150_000_000:
    Image.MAX_IMAGE_PIXELS = 150_000_000

DRAFT_FACTOR = 3        # JPEG draft keeps the image at least this many times the target size


def draft_jpeg(im, want):
    """#82: ask Pillow's JPEG decoder for a 1/2, 1/4 or 1/8 scale decode (DCT scaling), so a 108-150 MP
    JPEG is never expanded to full size in RAM (~3 bytes/pixel). Must be called before the image is
    loaded. The result is never smaller than `want` (width, height) in either side; photos that are
    not much bigger than `want` are unchanged. Non-JPEG files and other modes are left alone. CMYK/YCCK
    (print-workflow JPEGs) are scaled too: Pillow's draft() supports them."""
    if getattr(im, "format", None) not in ("JPEG", "MPO") or im.mode not in ("RGB", "L", "CMYK"):
        return im
    try:
        orient = im.getexif().get(0x0112, 1)
    except Exception:  # noqa: BLE001
        orient = 1
    if orient in (5, 6, 7, 8):          # stored sideways: the displayed width is the stored height
        want = (want[1], want[0])
    try:
        im.draft(im.mode, want)
    except Exception:  # noqa: BLE001 - never worse than a full decode
        pass
    return im


MIN_SIDE, MAX_SIDE = 320, 4000


def parse_target(value):
    """'WxH' -> (w, h); each side must be 320-4000. Raises ValueError otherwise."""
    try:
        w, h = (int(x) for x in str(value).strip().lower().split("x"))
        if MIN_SIDE <= w <= MAX_SIDE and MIN_SIDE <= h <= MAX_SIDE:
            return w, h
    except ValueError:
        pass
    raise ValueError(f"display size must look like 1000x600 (each side {MIN_SIDE}-{MAX_SIDE}), got {value!r}")


def target_or_default(value):
    """parse_target, but an invalid/out-of-range value falls back to the default and is logged."""
    try:
        return parse_target(value)
    except ValueError as e:
        log.warning("%s; using %s", e, DEFAULT_TARGET)
        return parse_target(DEFAULT_TARGET)


def display_name(stored_name):
    return os.path.splitext(stored_name)[0] + ".jpg"


def needs_copy(stored_name):
    """GIFs stay originals (animation); everything else displayable gets a copy."""
    return os.path.splitext(stored_name)[1].lower() != ".gif"


def cover_size(size, target):
    w, h = size
    tw, th = target
    scale = max(tw / w, th / h)
    if scale >= 1:
        return w, h
    return max(tw, round(w * scale)), max(th, round(h * scale))


def make_display_copy(src, dst, target, max_bytes=MAX_BYTES):
    """Write the display copy atomically. Returns (width, height, bytes)."""
    if Image is None:
        raise RuntimeError("Pillow is not installed")
    with Image.open(src) as im:
        draft_jpeg(im, (DRAFT_FACTOR * target[0], DRAFT_FACTOR * target[1]))
        im = ImageOps.exif_transpose(im)
        if im.mode not in ("RGB", "L"):
            bg = Image.new("RGB", im.size, (0, 0, 0))
            rgba = im.convert("RGBA")
            bg.paste(rgba, mask=rgba.getchannel("A"))
            im = bg
        from . import faces, framing
        found = faces.detect(im)                                   # MF-26: [] -> no-face rules
        if im.width / im.height < BLURFILL_BELOW:
            im = blurfill(im, target, found)                       # #86: narrow photo, B2 look
        else:
            # Crop first, in original pixels, where the framing rules say (MF-26 / Designer):
            # the visible 5:3 window, expanded to the target aspect. Then scale down only.
            box = framing.crop_box(im.size, target, found)
            if box != (0, 0) + im.size:
                im = im.crop(box)
            if im.width > target[0] or im.height > target[1]:
                im = im.resize(target, Image.LANCZOS)          # box has the target aspect: no distortion
        tmp = dst + ".tmp"
        while True:
            for q in QUALITIES:
                im.save(tmp, "JPEG", quality=q, optimize=True, progressive=True,   # no exif= -> stripped
                        comment=copy_marker(target))
                if os.path.getsize(tmp) <= max_bytes:
                    os.replace(tmp, dst)
                    return im.size + (os.path.getsize(dst),)
            # Pathological noise: shrink 10 % and retry (may drop below cover; still bounded).
            im = im.resize((max(1, int(im.width * 0.9)), max(1, int(im.height * 0.9))), Image.LANCZOS)


# ---------------- #86: narrow photos (portrait, square, 5:4) get blurred sides ("B2") ----------------
# Wren's spec (design/visual-direction 410e908, frame/15-PORTRAIT-BLUR.md section 3), tuned for 1000x600:
# the whole display copy is baked into ONE JPEG, so the slideshow and the kiosk page do no extra work.
BLURFILL_BELOW = 1.30   # width/height of the upright photo: below -> blur-fill, at or above -> cover (as before)
MAX_SHARP_W = 0.80      # sharp photo at most 80 % of the width, so each side bar is >= 10 %
TRIM = 1.15             # B2: sharp photo up to 15 % taller than the screen (6.5 % off top and bottom)
BG_SCALE = 4            # blur at 1/4 size, then upscale: ~2x cheaper than blurring at full size
BG_BLUR = 40            # radius at 1000x600 (scaled with the width for other sizes)
BG_DIM = 0.35           # fraction of black laid over the blurred sides
SHADOW_BLUR, SHADOW_ALPHA = 12, 0.35   # soft edge under the sharp photo (no border)


def _trim_top(im_size, k, ch, faces):
    """Rows (in the k-times-screen-height photo) cut off the top when trimming. Centred, or moved so
    the padded face boxes stay inside the screen. Returns None if they cannot (never cut a head)."""
    from . import framing
    fh = round(ch * k)
    extra = fh - ch
    if extra <= 0:
        return 0
    kept = framing.filter_faces(faces, *im_size)
    if not kept:
        return extra // 2
    pads = [framing.padded(f) for f in kept]
    y0 = max(0.0, min(p[1] for p in pads)) / im_size[1] * fh      # padded face top / bottom, in fh rows
    y1 = min(float(im_size[1]), max(p[3] for p in pads)) / im_size[1] * fh
    lo, hi = max(0.0, y1 - ch), min(float(extra), y0)
    return round(min(max(extra / 2, lo), hi)) if lo <= hi else None


def blurfill(im, target, faces=()):
    """Display copy for a narrow photo: sharp photo (15 % trim) centred over a strong blurred, dimmed
    enlargement of the same photo. `im` is the upright RGB/L stored copy. Memory: the sharp photo and
    the 1/4-size background are resized straight from `im` (box=), so no full-size intermediate exists."""
    from PIL import ImageDraw, ImageFilter
    from . import framing
    cw, ch = target
    s = cw / 1000                                                   # radii are defined at 1000 px wide
    a = im.width / im.height
    # background: the same window as the cover path would show, blurred at 1/4 size, upscaled
    bw, bh = cw // BG_SCALE, ch // BG_SCALE
    small = im.resize((bw, bh), Image.LANCZOS, box=framing.crop_box(im.size, target, faces), reducing_gap=2.0)
    small = small.filter(ImageFilter.GaussianBlur(BG_BLUR * s / BG_SCALE))
    bg = small.resize(target, Image.BILINEAR).convert("RGB")
    bg = Image.blend(bg, Image.new("RGB", target, (0, 0, 0)), BG_DIM)
    # sharp photo: 15 % taller than the screen, but never wider than 80 % of it
    k = max(1.0, min(TRIM, MAX_SHARP_W * cw / (a * ch)))
    top = _trim_top(im.size, k, ch, faces)
    if top is None:
        k, top = 1.0, 0                                             # a face would be cut: keep the whole photo
    fh = round(ch * k)
    fw, vis = max(1, round(fh * a)), min(fh, ch)                    # visible rows (a hair-thin photo: 1 px wide, no crash)
    rows = im.height / fh                                           # photo rows per sharp row
    fg = im.resize((fw, vis), Image.LANCZOS, box=(0, top * rows, im.width, (top + vis) * rows), reducing_gap=3.0)
    x0, y0 = (cw - fw) // 2, (ch - vis) // 2
    # soft edge: a 35 % black rectangle, blurred, under the photo (only the area it can reach is blurred)
    r = round(SHADOW_BLUR * s)
    pad = 3 * r
    region = (max(0, x0 - pad), max(0, y0 - pad), min(cw, x0 + fw + pad), min(ch, y0 + vis + pad))
    m = Image.new("L", (region[2] - region[0], region[3] - region[1]), 0)
    ImageDraw.Draw(m).rectangle((x0 - region[0], y0 - region[1], x0 - region[0] + fw - 1, y0 - region[1] + vis - 1),
                                fill=int(255 * SHADOW_ALPHA))
    bg.paste((0, 0, 0), region[:2], m.filter(ImageFilter.GaussianBlur(r)))
    bg.paste(fg, (x0, y0))
    return bg


COPY_VERSION = "mf86a"  # (#86: narrow photos blur-filled) bump when crop/size rules change: existing copies are then remade
_current = {}           # path -> (mtime, target) known to be current (avoids re-reading headers)


def copy_marker(target):
    """Written as the JPEG comment of every copy: rules version + target size."""
    return f"memoryframe-display {COPY_VERSION} {target[0]}x{target[1]}".encode()


def copy_is_current(path, target):
    """True if `path` was made for this target by the current rules. Copies from older
    versions (no marker: uncropped, 800x600, 1000x750) or for another size are stale."""
    try:
        mtime = os.path.getmtime(path)
        if _current.get(path) == (mtime, tuple(target)):
            return True
        with Image.open(path) as im:
            ok = im.info.get("comment") == copy_marker(target)
        if ok:                                           # truncated write: no JPEG EOI marker
            with open(path, "rb") as f:
                f.seek(-2, os.SEEK_END)
                ok = f.read(2) == b"\xff\xd9"
    except Exception:  # noqa: BLE001
        return False
    if ok:
        _current[path] = (mtime, tuple(target))
    return ok


def _ensure_copy(data_dir, stored_name, target, force=False):
    """(path to show, error). The copy is (re)made under the lock if it is missing, stale or `force`d.
    `error` is the exception of a failed remake, else None. A failed remake never touches the old copy:
    make_display_copy writes a temp file and replaces atomically, and the temp file is removed here."""
    original = os.path.join(data_dir, "originals", stored_name)
    if not needs_copy(stored_name):
        return original, None
    dst = os.path.join(data_dir, "display", display_name(stored_name))
    if not force and os.path.exists(dst) and copy_is_current(dst, target):
        return dst, None
    with _lock:
        if force or not (os.path.exists(dst) and copy_is_current(dst, target)):
            try:
                make_display_copy(original, dst, target)
            except Exception as e:  # noqa: BLE001 - never break the slideshow
                log.warning("display copy failed for %s: %s", stored_name, e)
                try:
                    os.remove(dst + ".tmp")
                except OSError:
                    pass
                return (dst if os.path.exists(dst) else original), e     # old copy beats nothing
    return dst, None


def ensure_display_copy(data_dir, stored_name, target):
    """Path of the file the display should show (copy if possible, else the original).
    A missing or stale copy (other size / older rules) is (re)made here, lazily on serve."""
    return _ensure_copy(data_dir, stored_name, target)[0]


def transient_failure(e):
    """True if a failed remake says nothing about the photo, so it is worth retrying: out of memory, a timeout,
    or a real system error (disk full, I/O error). Pillow reports a corrupt or truncated image as an OSError
    WITHOUT an errno; a system error always has one. Anything else is the photo's fault (permanent)."""
    return isinstance(e, (MemoryError, TimeoutError)) or (isinstance(e, OSError) and e.errno is not None)


BACKFILL_PAUSE_S = 0.2  # #86: rest between photos for the start-up THREAD (the kiosk browser keeps its CPU); the CLI passes no pause

FAILURES_FILE = ".copy-failures.json"


def _load_failures(data_dir):
    import json
    try:
        with open(os.path.join(data_dir, "display", FAILURES_FILE)) as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_failures(data_dir, failures):
    import json
    path = os.path.join(data_dir, "display", FAILURES_FILE)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path + ".tmp", "w") as f:
            json.dump(failures, f)
        os.replace(path + ".tmp", path)
    except OSError as e:
        log.warning("could not record display copy failures: %s", e)


def _failure_key(original, target):
    """Changes when the original appears/changes or the rules/size change -> retried then."""
    try:
        st = os.stat(original)
        orig = f"{st.st_size}:{int(st.st_mtime)}"
    except OSError:
        orig = "missing"
    return f"{orig}|{copy_marker(target).decode()}"


def backfill(conn, data_dir, target, force=False, pause=0):
    """Create missing or stale display copies for every image. Returns (made, failed).

    A copy only counts as made if a current copy exists afterwards. A failure caused by the photo (missing or
    unreadable original) is counted as failed and recorded in display/.copy-failures.json,
    so later runs (every start-up) skip it until the original, the rules or the size change,
    or --force is used. A transient failure (see transient_failure: out of memory, disk full, timeout) is counted
    as failed but NOT recorded, so the next run (next start-up) retries it. `force` remakes every copy to a temp
    file and replaces the old one atomically, so a failed remake keeps the copy that was there. `pause` seconds of rest after each copy made (the start-up thread passes one so the
    kiosk keeps its CPU; the CLI, which install.sh runs with the service stopped, passes none)."""
    made = failed = 0
    failures = {} if force else _load_failures(data_dir)
    before = dict(failures)
    rows = conn.execute("SELECT stored_name FROM media WHERE kind = 'image' ORDER BY id").fetchall()
    for (name,) in rows:
        if not needs_copy(name):
            continue
        dst = os.path.join(data_dir, "display", display_name(name))
        original = os.path.join(data_dir, "originals", name)
        if not force and os.path.exists(dst) and copy_is_current(dst, target):
            failures.pop(name, None)
            continue
        key = _failure_key(original, target)
        if failures.get(name) == key:
            failed += 1                                  # known failure: skipped, not retried
            continue
        err = None
        if not os.path.exists(original):
            log.warning("display copy skipped for %s: original is missing", name)
            ok = False
        else:
            _, err = _ensure_copy(data_dir, name, target, force)
            ok = err is None and os.path.exists(dst) and copy_is_current(dst, target)
            if pause:        # outside the lock (taken per photo in _ensure_copy), so requests are never held up by it
                time.sleep(pause)                            # background thread only (install/CLI: pause=0)
        if ok:
            made += 1
            failures.pop(name, None)
        else:
            failed += 1
            if err is not None and transient_failure(err):
                failures.pop(name, None)                     # not the photo's fault: retried next run
            else:
                failures[name] = key
    if failures != before:
        _save_failures(data_dir, failures)
    return made, failed


# ---------------- #82: the one stored copy (no full-size original is kept) ----------------

STORE_FACTOR = 1.5      # stored copy covers 1.5x the display target (1500x900 for 1000x600): sharp, never cropped
STORE_QUALITY = 88
STORE_MAX_SIDE = 4096   # extreme panoramas: never store a side longer than this
_store_lock = threading.Lock()   # one big decode at a time: waitress runs 4 threads on a small-RAM Pi


class ImageUnreadable(Exception):
    """The file passed the header checks but cannot be decoded (corrupt or truncated)."""


def stored_size(target):
    """Size the stored copy must still cover, from the display target: (1500, 900) for 1000x600."""
    return round(target[0] * STORE_FACTOR), round(target[1] * STORE_FACTOR)


_T = Image.Transpose if Image is not None else None
_TRANSPOSE = {2: _T.FLIP_LEFT_RIGHT, 3: _T.ROTATE_180, 4: _T.FLIP_TOP_BOTTOM, 5: _T.TRANSPOSE,
              6: _T.ROTATE_270, 7: _T.TRANSVERSE, 8: _T.ROTATE_90} if _T is not None else {}


def _orientation(im):
    """EXIF orientation 1-8 (1 = upright). Read before pixels are loaded; never raises."""
    try:
        o = int(im.getexif().get(0x0112, 1))
    except Exception:  # noqa: BLE001
        return 1
    return o if o in _TRANSPOSE else 1


def _upright(im, orient):
    """Rotate/flip a (small) image for the EXIF orientation. Done AFTER shrinking: ImageOps.exif_transpose
    would copy the full-size image first (#82 memory)."""
    return im.transpose(_TRANSPOSE[orient]) if orient in _TRANSPOSE else im


def _resizable(im):
    """Mode that resizes properly and cheaply: RGB, L, RGBA, LA and CMYK stay as they are (CMYK is
    shrunk first and converted after); palette images (resize would use NEAREST) and the rest are
    converted. Alpha is flattened later, on the small image (_flatten)."""
    if im.mode in ("RGB", "L", "RGBA", "LA", "CMYK"):
        return im
    if im.mode in ("P", "PA") or "transparency" in im.info:
        return im.convert("RGBA")
    return im.convert("RGB")


def _flatten(im):
    """Small image -> RGB/L without alpha (transparent areas black) and without CMYK."""
    if im.mode in ("RGBA", "LA"):
        rgba = im.convert("RGBA")
        bg = Image.new("RGB", im.size, (0, 0, 0))
        bg.paste(rgba, mask=rgba.getchannel("A"))
        return bg
    if im.mode in ("RGB", "L"):
        return im
    return im.convert("RGB")


def _to_srgb(im, profile):
    """Wide-gamut photos (Display P3 phones) would look dull once the profile is dropped."""
    try:
        import io
        from PIL import ImageCms
        src = ImageCms.ImageCmsProfile(io.BytesIO(profile))
        dst = ImageCms.createProfile("sRGB")
        return ImageCms.profileToProfile(im if im.mode == "CMYK" else im.convert("RGB"), src, dst,
                                         outputMode="RGB")
    except Exception:  # noqa: BLE001 - keep the pixels as they are
        return im


def fsync_path(path):
    """Flush a finished file to disk (the stored copy is the only copy: #82)."""
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def fsync_dir(path):
    """Make a rename/create in this directory durable. Best effort: some filesystems refuse it."""
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def make_stored_copy(src, dst, target):
    """Write the ONLY copy of an uploaded photo (#82): decoded at reduced size where the format allows
    (JPEG draft), scaled down so it still COVERS stored_size(target) (aspect kept, NOT cropped: the
    display copy crops at display time), EXIF orientation applied (upright; on the small image), no
    metadata (no EXIF/GPS/XMP/IPTC/ICC/comment), sRGB, JPEG, fsynced. Never upscales.
    Returns (width, height). Raises ImageUnreadable if the file cannot be decoded."""
    want = stored_size(target)
    with _store_lock:
        try:
            with Image.open(src) as im:
                orient = _orientation(im)
                draft_jpeg(im, want)
                profile = im.info.get("icc_profile")
                im = _resizable(im)
                sideways = orient in (5, 6, 7, 8)
                w, h = cover_size((im.height, im.width) if sideways else im.size, want)
                if max(w, h) > STORE_MAX_SIDE:
                    k = STORE_MAX_SIDE / max(w, h)
                    w, h = max(1, round(w * k)), max(1, round(h * k))
                if sideways:
                    w, h = h, w                       # size in the stored (unrotated) orientation
                if (w, h) != im.size:
                    im = im.resize((w, h), Image.LANCZOS, reducing_gap=3.0)
                cmyk = im.mode == "CMYK"
                if cmyk and profile:
                    im = _to_srgb(im, profile)          # CMYK profile -> sRGB straight from the CMYK data
                im = _flatten(im)
                if profile and not cmyk and im.mode == "RGB":
                    im = _to_srgb(im, profile)
                im = _upright(im, orient)
                im.load()                               # (a small, unchanged photo is still the open file)
        except (Image.DecompressionBombError, MemoryError) as e:
            raise ImageUnreadable(str(e) or type(e).__name__) from e
        except (OSError, ValueError, SyntaxError, EOFError, Image.UnidentifiedImageError) as e:
            raise ImageUnreadable(str(e)) from e
        tmp = dst + ".tmp"
        try:
            im.info = {}                               # nothing (e.g. a COM comment) rides along
            with open(tmp, "wb") as f:
                im.save(f, "JPEG", quality=STORE_QUALITY, optimize=True, comment=b"")
                f.flush()
                os.fsync(f.fileno())
            with Image.open(tmp) as check:
                check.verify()                         # a bad write must not become the only copy
            os.replace(tmp, dst)       # (the caller moves it into originals/ and fsyncs that directory)
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)
        return im.size


# ---------------- MF-16: thumbnails and removal ----------------

THUMB_SIZE = 240


def thumb_path(data_dir, stored_name):
    return os.path.join(data_dir, "thumbs", os.path.splitext(stored_name)[0] + ".jpg")


def make_thumb(src, dst, size=THUMB_SIZE):
    """Square 240 px JPEG (cover + centre crop), orientation applied, no metadata (no comment either)."""
    with Image.open(src) as im:
        orient = _orientation(im)
        draft_jpeg(im, (2 * size, 2 * size))
        im = _resizable(im)
        im = ImageOps.fit(im, (size, size), Image.LANCZOS)     # square, centred: commutes with the rotation
        im = _upright(_flatten(im), orient)
        im.info = {}
        tmp = dst + ".tmp"
        im.save(tmp, "JPEG", quality=75, optimize=True, comment=b"")
        os.replace(tmp, dst)


def ensure_thumb(data_dir, stored_name):
    """Path of the thumbnail (made on demand), or None if the image cannot be decoded."""
    dst = thumb_path(data_dir, stored_name)
    if os.path.exists(dst):
        return dst
    src = os.path.join(data_dir, "originals", stored_name)
    with _lock:
        if not os.path.exists(dst):
            try:
                make_thumb(src, dst)
            except Exception as e:  # noqa: BLE001
                log.warning("thumbnail failed for %s: %s", stored_name, e)
                return None
    return dst


def over_pixel_cap(path, max_pixels, max_nonjpeg_pixels=None):
    """True if the image header says width*height > max_pixels (formats other than JPEG:
    max_nonjpeg_pixels if given and lower; GIF included). No pixel data is decoded.
    Unreadable files return False; callers validate content separately."""
    from PIL import Image
    try:
        with Image.open(path) as im:
            w, h = im.size
            if max_nonjpeg_pixels and im.format not in ("JPEG", "MPO"):
                max_pixels = min(max_pixels, max_nonjpeg_pixels)
    except Image.DecompressionBombError:
        return True
    except Exception:  # noqa: BLE001
        return False
    return w * h > max_pixels


def remove_all(data_dir, stored_name):
    """Delete the original and every derivative. Returns the paths that were removed."""
    removed = []
    for p in (os.path.join(data_dir, "originals", stored_name),
              os.path.join(data_dir, "display", display_name(stored_name)),
              thumb_path(data_dir, stored_name)):
        try:
            os.remove(p)
            removed.append(p)
        except FileNotFoundError:
            pass
    return removed


def _derived_paths(data_dir, stored_name):
    return [os.path.join(data_dir, "originals", stored_name),
            os.path.join(data_dir, "display", display_name(stored_name)),
            thumb_path(data_dir, stored_name)]


TRASH_SUBDIRS = ("originals", "display", "thumbs")     # index = the 0-/1-/2- prefix


class UnsafeName(ValueError):
    """A stored name that is not a bare file name: deleting it could reach outside the photo dirs."""


def check_stored_name(data_dir, stored_name):
    """#80 guard for every delete: stored names are server-made "<uuid hex>.<ext>" file names, never paths.
    Refuse anything else (separators, "..", hidden names, NUL) before a single file is moved."""
    name = stored_name if isinstance(stored_name, str) else ""
    if (not name or name.startswith(".") or os.path.basename(name) != name
            or any(c in name for c in ("/", "\\", "\0"))):
        raise UnsafeName(repr(stored_name))
    root = os.path.abspath(data_dir)
    for p in _derived_paths(data_dir, name):          # belt and braces: every target sits under data_dir
        if os.path.commonpath([root, os.path.abspath(p)]) != root:
            raise UnsafeName(repr(stored_name))


def stage_delete(data_dir, stored_name, media_id):
    """Move the original and derivatives into tmp/trash as "<kind>-<media id>-<name>".
    Returns [(staged, original_path)]. Raises UnsafeName (nothing moved) for a name that isn't a bare file name."""
    check_stored_name(data_dir, stored_name)
    trash = os.path.join(data_dir, "tmp", "trash")
    os.makedirs(trash, exist_ok=True)
    staged = []
    try:
        for i, p in enumerate(_derived_paths(data_dir, stored_name)):
            if os.path.exists(p):
                dst = os.path.join(trash, f"{i}-{int(media_id)}-{os.path.basename(p)}")
                os.replace(p, dst)
                staged.append((dst, p))
    except OSError:
        unstage_delete(staged)
        raise
    return staged


def unstage_delete(staged):
    for dst, original in staged:
        try:
            os.replace(dst, original)
        except OSError as e:
            log.error("could not restore %s from %s: %s", original, dst, e)


def purge_staged(staged):
    n = 0
    for dst, _ in staged:
        try:
            os.remove(dst)
            n += 1
        except OSError as e:
            log.warning("left %s in the trash (swept at next start): %s", dst, e)
    return n


def _restore_legacy(conn, data_dir, path, kind, name):
    """Pre-id trash names ("<kind>-<uuid>.<ext>"): restore if exactly one row matches the
    stored name, else keep and log. Never deletes. Returns 1 if restored."""
    stem = os.path.splitext(name)[0]
    if kind == 0:
        rows = conn.execute("SELECT stored_name FROM media WHERE stored_name = ?", (name,)).fetchall()
    else:
        rows = conn.execute("SELECT stored_name FROM media WHERE stored_name LIKE ?",
                            (stem + ".%",)).fetchall() if name.endswith(".jpg") else []
    dest = os.path.join(data_dir, TRASH_SUBDIRS[kind], name)
    if len(rows) != 1 or os.path.exists(dest):
        log.warning("trash: keeping legacy %s (matching rows: %d, target exists: %s)",
                    os.path.basename(path), len(rows), os.path.exists(dest))
        return 0
    os.replace(path, dest)
    log.warning("trash: restored legacy %s", dest)
    return 1


def _sweep_trash(conn, data_dir):
    """Recover from a crash in the middle of a delete.

    Trash files are named "<0|1|2>-<media id>-<file>". If that media row still exists the
    delete never committed: move the file back. Only when the row is gone is the file
    deleted. Anything unexpected is kept and logged - never deleted.
    """
    import re
    trash = os.path.join(data_dir, "tmp", "trash")
    restored = removed = kept = 0
    if not os.path.isdir(trash):
        return restored, removed, kept
    for f in sorted(os.listdir(trash)):
        path = os.path.join(trash, f)
        m = re.fullmatch(r"([012])-(\d{1,19})-([0-9A-Za-z._-]+)", f)
        legacy = None if m else re.fullmatch(r"([012])-([0-9a-f]{32}\.[0-9a-z]{1,5})", f)
        if legacy and os.path.isfile(path):
            restored_one = _restore_legacy(conn, data_dir, path, int(legacy.group(1)), legacy.group(2))
            restored += restored_one
            kept += 1 - restored_one
            continue
        if not m or not os.path.isfile(path):
            log.warning("trash: keeping unrecognised %s", f)
            kept += 1
            continue
        kind, media_id, name = int(m.group(1)), int(m.group(2)), m.group(3)
        if media_id > 2**63 - 1:
            log.warning("trash: keeping %s (id out of range)", f)
            kept += 1
            continue
        row = conn.execute("SELECT stored_name FROM media WHERE id = ?", (media_id,)).fetchone()
        if row is None:
            try:
                os.remove(path)
                removed += 1
            except OSError as e:
                log.warning("trash: could not remove %s: %s", f, e)
            continue
        stored = row[0]
        expected = stored if kind == 0 else os.path.splitext(stored)[0] + ".jpg"
        dest = os.path.join(data_dir, TRASH_SUBDIRS[kind], name)
        if name != expected or os.path.exists(dest):
            log.warning("trash: keeping %s (row %d is %s; target exists: %s)",
                        f, media_id, stored, os.path.exists(dest))
            kept += 1
            continue
        os.replace(path, dest)
        log.warning("trash: restored %s after an interrupted delete", dest)
        restored += 1
    return restored, removed, kept


_TMP_LEFTOVER = re.compile(r"^(upload-[0-9a-f]{32}|[0-9a-f]{32}\.[a-z0-9]+(\.tmp)?|.*\.tmp)$")


def _sweep_tmp(data_dir):
    """Start-up: remove upload/save leftovers (up to 50 MB each) from tmp/ after a crash. Nothing is in
    flight at start-up. tmp/trash (a directory, handled by _sweep_trash) is never touched."""
    d = os.path.join(data_dir, "tmp")
    n = 0
    for f in os.listdir(d) if os.path.isdir(d) else []:
        p = os.path.join(d, f)
        if _TMP_LEFTOVER.match(f) and os.path.isfile(p):
            try:
                os.remove(p)
                n += 1
            except OSError:
                pass
    return n


def sweep(conn, data_dir):
    """Start-up cleanup: recover tmp/trash (see _sweep_trash), then remove display copies and
    thumbnails whose photo is no longer in the database. Originals are never deleted here."""
    known = {os.path.splitext(r[0])[0] for r in conn.execute("SELECT stored_name FROM media")}
    restored, swept, kept = _sweep_trash(conn, data_dir)
    swept += _sweep_tmp(data_dir)
    for sub in ("display", "thumbs"):
        d = os.path.join(data_dir, sub)
        for f in os.listdir(d) if os.path.isdir(d) else []:
            if f.endswith(".jpg") and os.path.splitext(f)[0] not in known:
                try:
                    os.remove(os.path.join(d, f))
                    swept += 1
                except OSError:
                    pass
    odir = os.path.join(data_dir, "originals")
    orphans = [f for f in os.listdir(odir) if os.path.splitext(f)[0] not in known] if os.path.isdir(odir) else []
    if orphans:
        log.warning("%d original(s) not in the database were kept: %s", len(orphans), orphans[:5])
    return swept
