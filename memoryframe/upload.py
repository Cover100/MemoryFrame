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

from . import imaging, library

log = logging.getLogger(__name__)

FORMAT_EXT = {"JPEG": ".jpg", "MPO": ".jpg", "PNG": ".png", "WEBP": ".webp", "GIF": ".gif",
              "HEIF": ".heic", "AVIF": ".heic"}
MAX_PIXELS = 64_000_000          # ~64 MP: decoding more risks running the Pi out of memory
TOO_BIG = "This photo is too large for the frame (over {mp} megapixels). Please send a smaller copy."
SAVE_FAILED = "The frame couldn't save this photo. Please try again; if it keeps happening, restart the frame."
FULL = "The frame is almost full. Delete some photos and try again."
VIDEO_EXT = {".mp4", ".mov", ".webm", ".mkv", ".avi", ".3gp", ".m4v"}
VIDEO_MAGIC = (b"ftypmp4", b"ftypisom", b"ftypqt", b"ftyp3gp", b"ftypM4V", b"ftypmp42", b"ftypavc1")


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


def _looks_like_video(head, name):
    if os.path.splitext(name or "")[1].lower() in VIDEO_EXT:
        return True
    return any(m in head[:16] for m in VIDEO_MAGIC) or head[:4] == b"\x1aE\xdf\xa3"


def sniff_image(path, max_pixels=MAX_PIXELS):
    """Return the stored extension for a real image, else None.
    Raises UploadError(415) if the header says it is larger than max_pixels (checked before
    any pixel data is decoded)."""
    from PIL import Image
    try:
        with Image.open(path) as im:           # reads the header only
            fmt = im.format
            w, h = im.size
            if w * h > max_pixels:
                raise UploadError(415, TOO_BIG.format(mp=max_pixels // 1_000_000))
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


def save_upload(conn, data_dir, filestorage, min_free, display_target, max_pixels=MAX_PIXELS):
    """Validate and add one uploaded file. Returns (stored_name or None if duplicate, label)."""
    if free_bytes(data_dir) < min_free:
        raise UploadError(507, FULL)
    tmp = os.path.join(data_dir, "tmp", "upload-" + uuid.uuid4().hex)
    try:
        filestorage.save(tmp)
        with open(tmp, "rb") as f:
            head = f.read(32)
        if _looks_like_video(head, filestorage.filename):
            raise UploadError(415, "Videos are coming later. For now, please upload photos only.")
        ext = sniff_image(tmp, max_pixels)
        if ext is None:
            raise UploadError(415, "This file is not a photo the frame can show (JPEG, PNG, WebP, GIF or HEIC).")
        if free_bytes(data_dir) < min_free:
            raise UploadError(507, FULL)
        name = label(filestorage.filename, ext)
        stored = library.add_file(conn, data_dir, tmp, name, "upload", display_target=display_target)
        return stored, name
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


__all__ = ["UploadError", "save_upload", "storage_info", "imaging"]
