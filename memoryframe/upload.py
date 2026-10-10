"""MF-14: upload validation and storage.

Validation is by CONTENT (Pillow decodes the file), never by the client's name
or Content-Type. Stored names are server-generated (library.add_file); the
client's file name is only kept, sanitised, as a display label.
"""
import os
import shutil
import uuid

from werkzeug.utils import secure_filename

from . import imaging, library

FORMAT_EXT = {"JPEG": ".jpg", "MPO": ".jpg", "PNG": ".png", "WEBP": ".webp", "GIF": ".gif",
              "HEIF": ".heic", "AVIF": ".heic"}
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


def sniff_image(path):
    """Return the stored extension for a real image, else None."""
    from PIL import Image
    try:
        with Image.open(path) as im:
            fmt = im.format
            im.verify()
    except Exception:  # noqa: BLE001 - anything Pillow cannot decode is not an image
        return None
    return FORMAT_EXT.get(fmt)


def label(client_name, ext):
    base = os.path.splitext(secure_filename(os.path.basename(client_name or "")))[0][:80] or "photo"
    return base + ext


def save_upload(conn, data_dir, filestorage, min_free, display_target):
    """Validate and add one uploaded file. Returns (stored_name or None if duplicate, label)."""
    if free_bytes(data_dir) < min_free:
        raise UploadError(507, "The frame is almost full. Delete some photos and try again.")
    tmp = os.path.join(data_dir, "tmp", "upload-" + uuid.uuid4().hex)
    try:
        filestorage.save(tmp)
        with open(tmp, "rb") as f:
            head = f.read(32)
        if _looks_like_video(head, filestorage.filename):
            raise UploadError(415, "Videos are coming later. For now, please upload photos only.")
        ext = sniff_image(tmp)
        if ext is None:
            raise UploadError(415, "This file is not a photo the frame can show (JPEG, PNG, WebP, GIF or HEIC).")
        if free_bytes(data_dir) < min_free:
            raise UploadError(507, "The frame is almost full. Delete some photos and try again.")
        name = label(filestorage.filename, ext)
        stored = library.add_file(conn, data_dir, tmp, name, "upload", display_target=display_target)
        return stored, name
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


__all__ = ["UploadError", "save_upload", "storage_info", "imaging"]
