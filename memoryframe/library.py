"""Adding files to the media library (used by the legacy import; MF-14 uploads reuse it)."""
import errno
import logging
import hashlib
import os
import re
import shutil
import stat
import uuid

from . import db, imaging, storage


class CopyVerificationError(Exception):
    pass


log = logging.getLogger(__name__)


def sha256_file(path):
    """Hash a file by path, or an open binary file object (read from its start)."""
    h = hashlib.sha256()
    if hasattr(path, "read"):
        path.seek(0)
        for chunk in iter(lambda: path.read(1024 * 1024), b""):
            h.update(chunk)
        return h.hexdigest()
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


_RST_OR_STANDALONE = set(range(0xD0, 0xD8)) | {0x01}


def jpeg_ends_cleanly(data):
    """#100 L1 (Hale): True if the JPEG's main image reaches its EOI (FFD9). Parses the markers instead of
    looking at the last bytes, so a valid file with a trailer after FFD9 (Samsung SEF data, padding) passes
    while a cut file fails even if an embedded EXIF thumbnail contains an FFD9 (it is inside APP1 and skipped
    by its length). Entropy-coded data never contains FF followed by anything but 00 or RSTn."""
    n = len(data)
    if n < 4 or data[:2] != b"\xff\xd8":
        return False
    i = 2
    while i < n:
        if data[i] != 0xFF:
            return False                                  # lost sync: not a well-formed JPEG
        while i < n and data[i] == 0xFF:                  # fill bytes
            i += 1
        if i >= n:
            return False
        m = data[i]
        i += 1
        if m == 0xD9:
            return True
        if m in _RST_OR_STANDALONE:
            continue
        if i + 2 > n:
            return False
        seglen = (data[i] << 8) | data[i + 1]
        if seglen < 2 or i + seglen > n:
            return False                                  # segment cut off
        i += seglen
        if m == 0xDA:                                     # SOS: skip entropy-coded data to the next marker
            while True:
                j = data.find(b"\xff", i)
                if j < 0 or j + 1 >= n:
                    return False                          # ran out mid-scan: truncated
                nxt = data[j + 1]
                if nxt == 0x00 or nxt in _RST_OR_STANDALONE or nxt == 0xFF:
                    i = j + (2 if nxt != 0xFF else 1)
                    continue
                i = j
                break
    return False


def _complete(f, ext):
    """#100 F3: Pillow's verify() does not notice a cut-off JPEG (and reports a cut PNG only as an OSError).
    A JPEG's markers must reach EOI (trailing bytes after it are fine: L1); a PNG must end with its IEND
    chunk (up to 32 bytes of padding). `f` is the open file (the caller checked size <= ORPHAN_MAX_BYTES)."""
    if ext in (".jpg", ".jpeg"):
        f.seek(0)
        return jpeg_ends_cleanly(f.read())
    if ext == ".png":
        size = f.seek(0, os.SEEK_END)
        f.seek(max(0, size - 32))
        return b"IEND\xaeB`\x82" in f.read()
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


# Above any upload (MEMORYFRAME_MAX_UPLOAD_MB, default 50) and any stored copy; bigger strays are reported only.
ORPHAN_MAX_BYTES = 60 * 1024 * 1024


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
        if name in known or name.startswith("."):
            continue                                   # a photo with a row, or a hidden file
        try:
            # #100 follow-up: open once with O_NOFOLLOW and check the open file (fstat), so the file that is
            # checked is the file that is read (no lstat-then-open race). O_NONBLOCK: a FIFO never hangs start-up.
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | getattr(os, "O_CLOEXEC", 0))
        except OSError as e:
            if e.errno == errno.ENOENT:
                continue                               # vanished since the listdir
            if e.errno == errno.ELOOP:
                reported += 1
                log.warning("orphan file left in place: %s (not a regular file (symlink or special), not followed)", name)
            elif e.errno != errno.EISDIR:
                reported += 1
                log.warning("orphan file left in place: %s (unreadable: %s)", name, errno.errorcode.get(e.errno, "OSError"))
            continue
        try:
            st = os.fstat(fd)
        except OSError:
            os.close(fd)
            continue
        if stat.S_ISDIR(st.st_mode):
            os.close(fd)
            continue
        with os.fdopen(fd, "rb") as f:
            reason = _check_orphan(conn, data_dir, name, path, f, st)
        if reason:
            reported += 1
            log.warning("orphan file left in place: %s (%s)", name, reason)
        else:
            adopted += 1
    return adopted, reported


def _check_orphan(conn, data_dir, name, path, f, st):
    """Adopt one orphan read through its open file `f` (fstat `st`). Returns None if adopted, else the reason."""
    reason = None
    try:
        kind = storage.media_kind(name) if _SERVER_NAME.fullmatch(name) else None
        if not stat.S_ISREG(st.st_mode):
            reason = "not a regular file (symlink or special), not followed"
        elif st.st_size > ORPHAN_MAX_BYTES:
            reason = "too large"                   # not hashed, so a stray big file costs nothing each start
        elif kind is None:
            reason = "not a server-made photo name"
        elif kind == "video":
            reason = "video, cannot be verified"
        elif not storage.settled(path):            # checked first: no work on a file that may be live
            reason = "changed in the last minute, may be a live upload; retried next start"
        else:
            file_digest = sha256_file(f)
            digest = _read_sidecar(data_dir, name) or file_digest      # F2: the upload's hash if noted
            if conn.execute("SELECT 1 FROM media WHERE sha256 IN (?, ?)", (digest, file_digest)).fetchone():
                reason = "same content as a photo already in the library"
            elif not _complete(f, os.path.splitext(name)[1].lower()):  # before Pillow, so a cut file says so
                reason = "truncated"
            else:
                from PIL import Image
                f.seek(0)
                with Image.open(f) as im:
                    im.verify()
            if reason is None:
                cur = conn.execute(
                    "INSERT OR IGNORE INTO media (stored_name, original_name, kind, sha256, size_bytes,"
                    " source, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (name, name, kind, digest, st.st_size, "upload", st.st_mtime))
                conn.commit()
                if not cur.rowcount:
                    return "already has a row"  # unreachable in practice (UNIQUE race); not counted as adopted
                log.warning("orphan file adopted into the library: %s", name)
                _drop_sidecar(sha_sidecar(data_dir, name))
    except Exception as e:  # noqa: BLE001 - unreadable/corrupt/too big: report it, never fail start-up
        try:
            conn.rollback()
        except Exception:  # noqa: BLE001
            pass
        reason = f"unreadable: {type(e).__name__}"      # type only: never put file contents in a log
    return reason
