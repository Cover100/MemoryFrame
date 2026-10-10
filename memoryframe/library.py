"""Adding files to the media library (used by the legacy import; MF-14 uploads reuse it)."""
import hashlib
import os
import shutil
import uuid

from . import db, storage


class CopyVerificationError(Exception):
    pass


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def add_file(conn, data_dir, src, original_name, source):
    """Copy src into originals/ under a server-generated name and record it.

    Returns the stored name, or None if the type is unsupported or an identical
    file (same SHA-256) is already in the library. src is never modified.
    """
    kind = storage.media_kind(original_name)
    if kind is None:
        return None
    digest = sha256_file(src)
    if conn.execute("SELECT 1 FROM media WHERE sha256 = ?", (digest,)).fetchone():
        return None
    stored = uuid.uuid4().hex + os.path.splitext(original_name)[1].lower()
    tmp = os.path.join(data_dir, "tmp", stored)
    shutil.copy2(src, tmp)
    if sha256_file(tmp) != digest:
        os.remove(tmp)
        raise CopyVerificationError(f"copy verification failed for {original_name}")
    os.replace(tmp, os.path.join(data_dir, "originals", stored))
    conn.execute(
        "INSERT INTO media (stored_name, original_name, kind, sha256, size_bytes, source, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (stored, original_name, kind, digest, os.path.getsize(src), source, db.now()))
    conn.commit()
    return stored
