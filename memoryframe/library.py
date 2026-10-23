"""Adding files to the media library (used by the legacy import; MF-14 uploads reuse it)."""
import logging
import hashlib
import os
import shutil
import uuid

from . import db, imaging, storage


class CopyVerificationError(Exception):
    pass


log = logging.getLogger(__name__)


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def add_file(conn, data_dir, src, original_name, source, display_target=None, store_target=None):
    """Copy src into originals/ under a server-generated name and record it.

    Returns the stored name, or None if the type is unsupported or an identical
    file (same SHA-256) is already in the library. src is never modified.

    #82: with store_target (the display target) an image is NOT copied: the library keeps one
    shrunk, upright, metadata-free JPEG instead (imaging.make_stored_copy; GIFs stay as they are).
    The sha256 column is then the hash of the file as uploaded, so re-sending the same file is still
    detected as a duplicate. Raises imaging.ImageUnreadable for a file that cannot be decoded.
    """
    kind = storage.media_kind(original_name)
    if kind is None:
        return None
    digest = sha256_file(src)
    if conn.execute("SELECT 1 FROM media WHERE sha256 = ?", (digest,)).fetchone():
        return None
    ext = os.path.splitext(original_name)[1].lower()
    shrink = bool(store_target) and kind == "image" and ext != ".gif"
    stored = uuid.uuid4().hex + (".jpg" if shrink else ext)
    tmp = os.path.join(data_dir, "tmp", stored)
    try:
        if shrink:
            imaging.make_stored_copy(src, tmp, store_target)
        else:
            shutil.copy2(src, tmp)
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)                    # e.g. disk full: never leave a partial copy
        raise
    if not shrink and sha256_file(tmp) != digest:
        os.remove(tmp)
        raise CopyVerificationError(f"copy verification failed for {original_name}")
    original = os.path.join(data_dir, "originals", stored)
    if not shrink:
        imaging.fsync_path(tmp)               # the stored copy is already fsynced by make_stored_copy
    os.replace(tmp, original)
    imaging.fsync_dir(os.path.dirname(original))   # file + directory entry are on disk before the row exists
    try:
        conn.execute(
            "INSERT INTO media (stored_name, original_name, kind, sha256, size_bytes, source, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (stored, original_name, kind, digest, os.path.getsize(original), source, db.now()))
        conn.commit()
    except BaseException:
        # no row -> no files: remove the original (and any derivative) we just placed
        try:
            conn.rollback()
        except Exception:  # noqa: BLE001
            pass
        imaging.remove_all(data_dir, stored)
        raise
    if kind == "image" and display_target:
        try:
            imaging.ensure_display_copy(data_dir, stored, display_target)
            imaging.ensure_thumb(data_dir, stored)
        except OSError as e:                  # photo is saved; derivatives are rebuilt later
            log.warning("display copy/thumbnail for %s failed: %s", stored, e)
    return stored
