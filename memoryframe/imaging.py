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
import os
import threading

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
        im = ImageOps.exif_transpose(im)
        if im.mode not in ("RGB", "L"):
            bg = Image.new("RGB", im.size, (0, 0, 0))
            rgba = im.convert("RGBA")
            bg.paste(rgba, mask=rgba.getchannel("A"))
            im = bg
        # Crop first, in original pixels, where the framing rules say (MF-26 / Designer):
        # the visible 5:3 window, expanded to the target aspect. Then scale down only.
        from . import faces, framing
        box = framing.crop_box(im.size, target, faces.detect(im))   # MF-26: [] -> no-face rules
        if box != (0, 0) + im.size:
            im = im.crop(box)
        if im.width > target[0] or im.height > target[1]:
            im = im.resize(target, Image.LANCZOS)          # exactly 1000x600; never upscaled
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


COPY_VERSION = "mf26c"  # bump when crop/size rules change: existing copies are then remade
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
    except Exception:  # noqa: BLE001
        return False
    if ok:
        _current[path] = (mtime, tuple(target))
    return ok


def ensure_display_copy(data_dir, stored_name, target):
    """Path of the file the display should show (copy if possible, else the original).
    A missing or stale copy (other size / older rules) is (re)made here, lazily on serve."""
    original = os.path.join(data_dir, "originals", stored_name)
    if not needs_copy(stored_name):
        return original
    dst = os.path.join(data_dir, "display", display_name(stored_name))
    if os.path.exists(dst) and copy_is_current(dst, target):
        return dst
    with _lock:
        if not (os.path.exists(dst) and copy_is_current(dst, target)):
            try:
                make_display_copy(original, dst, target)
            except Exception as e:  # noqa: BLE001 - never break the slideshow
                log.warning("display copy failed for %s: %s", stored_name, e)
                return dst if os.path.exists(dst) else original     # old copy beats nothing
    return dst


def backfill(conn, data_dir, target, force=False):
    """Create missing or stale display copies for every image. Returns (made, failed)."""
    made = failed = 0
    rows = conn.execute("SELECT stored_name FROM media WHERE kind = 'image' ORDER BY id").fetchall()
    for (name,) in rows:
        if not needs_copy(name):
            continue
        dst = os.path.join(data_dir, "display", display_name(name))
        if force and os.path.exists(dst):
            os.remove(dst)
        if os.path.exists(dst) and copy_is_current(dst, target):
            continue
        if ensure_display_copy(data_dir, name, target) == dst:
            made += 1
        else:
            failed += 1
    return made, failed


# ---------------- MF-16: thumbnails and removal ----------------

THUMB_SIZE = 240


def thumb_path(data_dir, stored_name):
    return os.path.join(data_dir, "thumbs", os.path.splitext(stored_name)[0] + ".jpg")


def make_thumb(src, dst, size=THUMB_SIZE):
    """Square 240 px JPEG (cover + centre crop), orientation applied, no metadata."""
    with Image.open(src) as im:
        im = ImageOps.exif_transpose(im)
        if im.mode not in ("RGB", "L"):
            bg = Image.new("RGB", im.size, (0, 0, 0))
            rgba = im.convert("RGBA")
            bg.paste(rgba, mask=rgba.getchannel("A"))
            im = bg
        im = ImageOps.fit(im, (size, size), Image.LANCZOS)
        tmp = dst + ".tmp"
        im.save(tmp, "JPEG", quality=75, optimize=True)
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


def over_pixel_cap(path, max_pixels):
    """True if the image header says width*height > max_pixels (no pixel data is decoded).
    Unreadable files return False; callers validate content separately."""
    from PIL import Image
    try:
        with Image.open(path) as im:
            w, h = im.size
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


def stage_delete(data_dir, stored_name, media_id):
    """Move the original and derivatives into tmp/trash as "<kind>-<media id>-<name>".
    Returns [(staged, original_path)]."""
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


def sweep(conn, data_dir):
    """Start-up cleanup: recover tmp/trash (see _sweep_trash), then remove display copies and
    thumbnails whose photo is no longer in the database. Originals are never deleted here."""
    known = {os.path.splitext(r[0])[0] for r in conn.execute("SELECT stored_name FROM media")}
    restored, swept, kept = _sweep_trash(conn, data_dir)
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
