"""One-time import of the legacy uploads/ folder into the new data dir.

Steps (python -m memoryframe migrate, also run automatically by app.py main()):
 1. BACKUP: tar of every legacy media file plus a SHA-256 manifest, written to
    <DATA_DIR>/backups/legacy-uploads-<timestamp>.tar and verified by
    re-reading it. Aborts if there isn't room for backup + copies.
 2. IMPORT: each file is COPIED (never moved or deleted) to originals/ under a
    server-generated name, checksum-verified, and recorded in the media table.
    Files whose SHA-256 is already in the library are skipped.
 3. Marks the import done (settings.legacy_import_done), so a second run
    imports nothing. The legacy folder is left untouched.
"""
import hashlib
import io
import json
import os
import shutil
import tarfile
import time

from . import db, library, storage
from .library import sha256_file

SPACE_MARGIN = 50 * 1024 * 1024   # keep at least 50 MB free after migrating


class MigrationError(Exception):
    pass


def legacy_files(legacy_dir):
    if not os.path.isdir(legacy_dir):
        return []
    out = []
    for name in sorted(os.listdir(legacy_dir)):
        p = os.path.join(legacy_dir, name)
        if os.path.isfile(p) and not os.path.islink(p) and storage.media_kind(name):
            out.append(p)
    return out


def backup_legacy(files, backups_dir, stamp=None):
    """Write and verify a tar backup. Returns (tar_path, manifest)."""
    stamp = stamp or time.strftime("%Y%m%d-%H%M%S")
    tar_path = os.path.join(backups_dir, f"legacy-uploads-{stamp}.tar")
    manifest = {os.path.basename(p): sha256_file(p) for p in files}
    tmp = tar_path + ".partial"
    with tarfile.open(tmp, "w") as tar:
        for p in files:
            tar.add(p, arcname=f"uploads/{os.path.basename(p)}")
        data = json.dumps(manifest, indent=1, sort_keys=True).encode()
        info = tarfile.TarInfo("MANIFEST.sha256.json")
        info.size = len(data)
        info.mtime = int(time.time())
        tar.addfile(info, io.BytesIO(data))
    # Verify: every file in the manifest is in the tar with the same hash.
    with tarfile.open(tmp, "r") as tar:
        for name, digest in manifest.items():
            member = tar.extractfile(f"uploads/{name}")
            if member is None or hashlib.sha256(member.read()).hexdigest() != digest:
                raise MigrationError(f"backup verification failed for {name}")
    os.replace(tmp, tar_path)
    with open(tar_path + ".sha256.json", "w") as f:
        json.dump(manifest, f, indent=1, sort_keys=True)
    return tar_path, manifest


def migrate_legacy(config, backup=True, log=print):
    """Run the migration. Returns a summary dict. Safe to run repeatedly."""
    data_dir = config["DATA_DIR"]
    storage.ensure_layout(data_dir)
    db.init_db(config["DB_PATH"])
    conn = db.connect(config["DB_PATH"])
    try:
        done = db.get_setting(conn, "legacy_import_done")
        if done:
            log(f"Legacy import already done at {done}; nothing to do.")
            return {"imported": 0, "skipped": 0, "backup": None, "already_done": True}

        legacy_dir = config["LEGACY_UPLOADS"]
        files = legacy_files(legacy_dir)
        log(f"Legacy folder {legacy_dir}: {len(files)} media file(s).")

        backup_path = None
        if files and backup:
            needed = 2 * sum(os.path.getsize(p) for p in files) + SPACE_MARGIN
            free = shutil.disk_usage(data_dir).free
            if free < needed:
                raise MigrationError(
                    f"Not enough free space in {data_dir}: need {needed // 2**20} MB, "
                    f"have {free // 2**20} MB. Nothing was changed.")
            backup_path, _ = backup_legacy(files, os.path.join(data_dir, "backups"))
            log(f"Backup written and verified: {backup_path}")

        imported = skipped = 0
        for src in files:
            try:
                stored = library.add_file(conn, data_dir, src, os.path.basename(src), "legacy")
            except library.CopyVerificationError as e:
                raise MigrationError(str(e))
            if stored:
                imported += 1
            else:
                skipped += 1

        db.set_setting(conn, "legacy_import_done", time.strftime("%Y-%m-%d %H:%M:%S"))
        if backup_path:
            db.set_setting(conn, "legacy_import_backup", backup_path)
        conn.commit()
        log(f"Imported {imported}, skipped {skipped} duplicate(s). "
            f"Legacy folder left untouched.")
        return {"imported": imported, "skipped": skipped, "backup": backup_path, "already_done": False}
    finally:
        conn.close()
