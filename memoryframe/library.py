"""Adding files to the media library (used by the legacy import; MF-14 uploads reuse it)."""
import logging
import hashlib
import os
import re
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


def sha_sidecar(data_dir, stored):
    """tmp/<stored>.sha256: the upload's hash while add_file is between os.replace and its commit."""
    return os.path.join(data_dir, "tmp", stored + ".sha256")


def _drop_sidecar(path):
    if path:
        try:
            os.remove(path)
        except OSError:
            pass               # a leftover is swept at the next start (imaging._sweep_tmp)


def _read_sidecar(data_dir, stored):
    try:
        with open(sha_sidecar(data_dir, stored)) as f:
            v = f.read(65).strip()
    except OSError:
        return None
    return v if re.fullmatch(r"[0-9a-f]{64}", v) else None


def _complete(path):
    """#100 F3: Pillow's verify() does not notice a cut-off JPEG (and reports a cut PNG only as an OSError). A JPEG must end with EOI (FFD9),
    allowing up to 30 bytes of trailing padding; a PNG must end with its IEND chunk."""
    with open(path, "rb") as f:
        f.seek(max(0, os.path.getsize(path) - 32))
        tail = f.read()
    ext = os.path.splitext(path)[1].lower()
    if ext in (".jpg", ".jpeg"):
        return b"\xff\xd9" in tail
    if ext == ".png":
        return b"IEND\xaeB`\x82" in tail
    return True


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
    # #100 F2: note the upload's hash next to it until the row exists, so a start-up reconcile after a cut
    # can record the real duplicate key (a shrunk copy cannot be hashed back to the upload).
    side = sha_sidecar(data_dir, stored)
    try:
        with open(side, "w") as f:
            f.write(digest)
            f.flush()
            os.fsync(f.fileno())
    except OSError:
        side = None                           # best effort: the reconcile then falls back to the file's hash
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
        _drop_sidecar(side)
        raise
    _drop_sidecar(side)                       # only after the commit; it is never a photo
    if kind == "image" and display_target:
        try:
            imaging.ensure_display_copy(data_dir, stored, display_target)
            imaging.ensure_thumb(data_dir, stored)
        except OSError as e:                  # photo is saved; derivatives are rebuilt later
            log.warning("display copy/thumbnail for %s failed: %s", stored, e)
    return stored


# A file the server itself stored: "<uuid4 hex>.<ext>" (library.add_file). Anything else in originals/ is
# not ours to adopt.
_SERVER_NAME = re.compile(r"[0-9a-f]{32}\.[a-z0-9]{1,5}")


def reconcile_orphans(conn, data_dir):
    """#91: find photos in originals/ that have no media row and adopt them, or report them.

    A power cut or kill between add_file's os.replace and its DB commit leaves a complete photo file (the
    file is fsynced and renamed atomically, so it is never partial) with no row, so it is invisible and
    wastes space. This is the start-up repair. It NEVER deletes or modifies a file. Per orphan:
      - a valid image (Pillow opens and verifies it) whose bytes match no row: adopted, i.e. a row is built
        from the stored file the way add_file builds it (kind, sha256, size; created_at = file mtime);
      - anything else (unreadable or corrupt, a video, a duplicate of a row, a name the server did not
        make): left in place and logged once as "orphan file left in place: <name> (<reason>)".
    The row's sha256 is the upload's hash from add_file's tmp/<name>.sha256 note (F2), so a retried upload
    is still a duplicate; without the note it falls back to the stored file's hash.
    original_name is not recoverable either; the stored name stands in for it (shown in the library only).

    Cost: one listdir and one set of known names from the DB; image work happens only for real orphans.
    Race: create_app calls this before it serves, but another process (a dev run, a second start) may be
    serving uploads. A live add_file has a file in originals/ for a moment before its commit; adopting it
    would make that commit fail, and add_file then removes its file (#100 F1). So files changed in the last
    storage.SETTLE_S are left for the next start. stored_name is UNIQUE, so a race never makes two rows. Returns (adopted, reported).
    """
    odir = os.path.join(data_dir, "originals")
    if not os.path.isdir(odir):
        return 0, 0
    known = {r[0] for r in conn.execute("SELECT stored_name FROM media")}
    adopted = reported = 0
    for name in sorted(os.listdir(odir)):
        path = os.path.join(odir, name)
        if name in known or name.startswith(".") or not os.path.isfile(path):
            continue                                   # a photo with a row, a hidden file, or a directory
        reason = None
        try:
            kind = storage.media_kind(name) if _SERVER_NAME.fullmatch(name) else None
            if kind is None:
                reason = "not a server-made photo name"
            elif kind == "video":
                reason = "video, cannot be verified"
            elif not storage.settled(path):            # checked first: no work on a file that may be live
                reason = "changed in the last minute, may be a live upload; retried next start"
            else:
                file_digest = sha256_file(path)
                digest = _read_sidecar(data_dir, name) or file_digest      # F2: the upload's hash if noted
                if conn.execute("SELECT 1 FROM media WHERE sha256 IN (?, ?)", (digest, file_digest)).fetchone():
                    reason = "same content as a photo already in the library"
                else:
                    if not _complete(path):                       # before Pillow, so a cut file says so
                        reason = "truncated"
                    else:
                        from PIL import Image
                        with Image.open(path) as im:
                            im.verify()
                if reason is None:
                    cur = conn.execute(
                        "INSERT OR IGNORE INTO media (stored_name, original_name, kind, sha256, size_bytes,"
                        " source, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (name, name, kind, digest, os.path.getsize(path), "upload", os.path.getmtime(path)))
                    conn.commit()
                    if cur.rowcount:
                        adopted += 1
                        log.warning("orphan file adopted into the library: %s", name)
                        _drop_sidecar(sha_sidecar(data_dir, name))
        except Exception as e:  # noqa: BLE001 - unreadable/corrupt/too big: report it, never fail start-up
            try:
                conn.rollback()
            except Exception:  # noqa: BLE001
                pass
            reason = f"unreadable: {type(e).__name__}"      # type only: never put file contents in a log
        if reason:
            reported += 1
            log.warning("orphan file left in place: %s (%s)", name, reason)
    return adopted, reported
