"""MF-14: upload validation and storage.

Validation is by CONTENT (Pillow decodes the file), never by the client's name
or Content-Type. Stored names are server-generated (library.add_file); the
client's file name is only kept, sanitised, as a display label.
"""
import errno
import logging
import os
import re
import sqlite3
import unicodedata
import shutil
import uuid

from werkzeug.utils import secure_filename

from . import imaging, library, video

log = logging.getLogger(__name__)

FORMAT_EXT = {"JPEG": ".jpg", "MPO": ".jpg", "PNG": ".png", "WEBP": ".webp", "GIF": ".gif",
              "HEIF": ".heic", "AVIF": ".heic"}
MAX_PIXELS = 150_000_000         # 150 MP (#82: 108 MP phones). JPEGs are decoded at reduced size (imaging.draft_jpeg),
                                 # so peak RAM stays small; override with MEMORYFRAME_MAX_MEGAPIXELS.
MAX_NONJPEG_PIXELS = 50_000_000  # HEIC/PNG/WebP/AVIF cannot be decoded at reduced size. Measured peak RSS just under
                                 # the cap (49 MP, whole upload): WebP 789 MB, PNG palette 509 MB, AVIF 471 MB, PNG RGBA 462 MB,
                                 # HEIC 415 MB, PNG RGB 275 MB (see README).
                                 # Override with MEMORYFRAME_MAX_NONJPEG_MEGAPIXELS.
                                 # GIFs (kept as sent) count as non-JPEG: same cap.
TOO_BIG = "This photo is too large for the frame (over {mp} megapixels). Please send a smaller copy."
TOO_BIG_OTHER = ("This {fmt} photo is too large for the frame (over {mp} megapixels). "
                 "Please send it as a JPEG or a smaller copy.")
TOO_BIG_GIF = ("This GIF is too large for the frame (over {mp} megapixels). "
               "Please send a smaller GIF or a still photo.")
NOT_A_PHOTO = "This file is not a photo the frame can show (JPEG, PNG, WebP, GIF or HEIC)."
NOT_JPEG = ("JPEG", "MPO")
FORMAT_NAME = {"HEIF": "HEIC", "AVIF": "HEIC", "PNG": "PNG", "WEBP": "WebP", "GIF": "GIF"}
SAVE_FAILED = "The frame couldn't save this photo. Please try again; if it keeps happening, restart the frame."
FULL = "The frame is almost full. Delete some photos and try again."
VIDEO_EXT = {".mp4", ".mov", ".webm", ".mkv", ".avi", ".3gp", ".m4v"}
VIDEO_MAGIC = (b"ftypmp4", b"ftypisom", b"ftypqt", b"ftyp3gp", b"ftypM4V", b"ftypmp42", b"ftypavc1",
               b"ftypiso", b"ftyp3g2", b"ftypMSNV", b"ftypdash")
VIDEO_LATER = "Videos are coming later. For now, please upload photos only."
PHOTO_TOO_LARGE = ("This file is too large. The limit is {mb} MB per photo. "
                   "Please try a smaller photo (a lower-resolution copy).")


class UploadError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status
        self.message = message


def free_bytes(path):
    return shutil.disk_usage(path).free


def storage_info(data_dir, min_free, avg_photo):
    du = shutil.disk_usage(data_dir)
    usable = max(0, du.free - min_free)
    return {"total": du.total, "used": du.used, "free": du.free, "min_free": min_free,
            "photos_remaining_estimate": int(usable // avg_photo) if avg_photo else None}


def _video_magic(head):
    """#102: a video container by content (ISO-BMFF ftyp brand or EBML/Matroska/WebM), never by name."""
    return any(m in head[:16] for m in VIDEO_MAGIC) or head[:4] == b"\x1aE\xdf\xa3"


def _looks_like_video(head, name):
    if os.path.splitext(name or "")[1].lower() in VIDEO_EXT:
        return True
    return _video_magic(head)


def pixel_cap(fmt, max_pixels=MAX_PIXELS, max_nonjpeg_pixels=MAX_NONJPEG_PIXELS):
    """The pixel cap for an image format (PIL format name): JPEG max_pixels; everything else, GIF included
    (cannot be decoded at reduced size), the lower of max_pixels and max_nonjpeg_pixels."""
    return max_pixels if fmt in NOT_JPEG else min(max_pixels, max_nonjpeg_pixels)


def sniff_image(path, max_pixels=MAX_PIXELS, max_nonjpeg_pixels=MAX_NONJPEG_PIXELS):
    """Return the stored extension for a real image, else None.
    Raises UploadError(415) if the header says it is larger than pixel_cap(format): checked before any
    pixel data is decoded. The message quotes the megapixel number that applies."""
    from PIL import Image
    try:
        with Image.open(path) as im:           # reads the header only
            fmt = im.format
            w, h = im.size
            cap = pixel_cap(fmt, max_pixels, max_nonjpeg_pixels)
            if w * h > cap:
                mp = cap // 1_000_000
                if fmt == "GIF":
                    raise UploadError(415, TOO_BIG_GIF.format(mp=mp))
                if cap < max_pixels:
                    raise UploadError(415, TOO_BIG_OTHER.format(fmt=FORMAT_NAME.get(fmt, fmt), mp=mp))
                raise UploadError(415, TOO_BIG.format(mp=mp))
            im.verify()
    except UploadError:
        raise
    except Image.DecompressionBombError:
        raise UploadError(415, TOO_BIG.format(mp=max_pixels // 1_000_000))
    except Exception:  # noqa: BLE001 - anything Pillow cannot decode is not an image
        return None
    return FORMAT_EXT.get(fmt)


def label(client_name, ext):
    """Display label from the client's name. Unicode letters/digits are kept (NFC); path
    parts, control and punctuation characters are dropped; falls back to "photo"."""
    name = unicodedata.normalize("NFC", str(client_name or "")).replace("\\", "/").split("/")[-1]
    base = re.sub(r"\.[A-Za-z0-9]{1,5}$", "", name)       # drop the extension (also for "....jpg")
    base = "".join(c if (c.isalnum() or c in "-_ .") else "_" for c in base)
    base = re.sub(r"[\s_]+", "_", base).strip("._- ")[:80].strip("._- ")
    return (base or "photo") + ext


def _is_enospc(e):
    return isinstance(e, OSError) and e.errno in (errno.ENOSPC, errno.EDQUOT)


def save_upload(conn, data_dir, filestorage, min_free, display_target, max_pixels=MAX_PIXELS,
                max_nonjpeg_pixels=MAX_NONJPEG_PIXELS, videos=False, max_photo_bytes=None, meta=None):
    """Validate and add one uploaded file. Returns (stored_name or None if duplicate, label).

    #102: with videos=True (ffmpeg is installed) a video container (by content) is queued for conversion
    (video.accept) instead of refused; the stored name ends in .mp4. max_photo_bytes keeps the photo limit
    when the request limit was raised for videos (350 MB)."""
    if free_bytes(data_dir) < min_free:
        raise UploadError(507, FULL)
    tmp = os.path.join(data_dir, "tmp", "upload-" + uuid.uuid4().hex)
    try:
        filestorage.save(tmp)
        with open(tmp, "rb") as f:
            head = f.read(32)
        if videos and _video_magic(head):
            return _save_video(conn, data_dir, tmp, filestorage.filename, min_free, meta)
        if _looks_like_video(head, filestorage.filename):
            raise UploadError(415, VIDEO_LATER if not videos else video.NOT_A_VIDEO)
        if max_photo_bytes and os.path.getsize(tmp) > max_photo_bytes:
            raise UploadError(413, PHOTO_TOO_LARGE.format(mb=max_photo_bytes // (1024 * 1024)))
        ext = sniff_image(tmp, max_pixels, max_nonjpeg_pixels)
        if ext is None:
            raise UploadError(415, NOT_A_PHOTO)
        if free_bytes(data_dir) < min_free:
            raise UploadError(507, FULL)
        if display_target and ext != ".gif":
            ext = ".jpg"                     # #82: stored as one shrunk JPEG, whatever was sent
        name = label(filestorage.filename, ext)
        stored = library.add_file(conn, data_dir, tmp, name, "upload", display_target=display_target,
                                  store_target=display_target)
        return stored, name
    except imaging.ImageUnreadable as e:
        log.warning("upload could not be decoded: %s", e)
        raise UploadError(415, NOT_A_PHOTO) from e
    except OSError as e:
        if _is_enospc(e):                     # disk filled up while writing
            raise UploadError(507, FULL) from e
        log.error("upload failed: %s", e)
        raise UploadError(500, SAVE_FAILED) from e
    except sqlite3.Error as e:
        if getattr(e, "sqlite_errorcode", None) == 13 or "full" in str(e).lower():   # SQLITE_FULL
            raise UploadError(507, FULL) from e
        log.error("upload DB insert failed: %s", e)
        raise UploadError(500, SAVE_FAILED) from e
    finally:
        try:
            os.remove(tmp)
        except FileNotFoundError:
            pass


def _save_video(conn, data_dir, tmp, client_name, min_free, meta=None):
    """#102: tmp is the upload on the card. Space (#96 reserve) is checked against its real size, then it
    is hashed (duplicates), ffprobed (refused here, not later, if the frame cannot use it) and queued."""
    size = os.path.getsize(tmp)
    if size > video.MAX_UPLOAD_BYTES:
        raise UploadError(413, video.TOO_LARGE)
    if video.pending(conn) >= video.MAX_PENDING:          # Sage: cap what can sit on the card unconverted
        raise UploadError(429, video.QUEUE_FULL)
    if not video.space_ok(data_dir, min_free, size):     # the converted clip + poster + thumb fit
        raise UploadError(507, FULL)
    try:
        info = video.probe(tmp)
    except video.VideoError as e:
        raise UploadError(415, str(e)) from e
    name = label(client_name, ".mp4")
    stored = video.accept(conn, data_dir, tmp, name, library.sha256_file(tmp))
    if meta is not None and info.get("high_res"):       # over 4K: allowed, but slow on the Pi
        meta.update(high_res=True, prep_minutes=video.prep_minutes(info))
    return stored, name


__all__ = ["UploadError", "save_upload", "storage_info", "imaging"]
