"""Command line: python -m memoryframe <command>

  migrate [--no-backup]   back up and import the legacy uploads/ folder (one-time)
  status                  show data dir, database and media counts
  display-copies [--force] create missing display copies (MF-15 backfill)
  check                   SQLite integrity check of the database (#69; exit 1 unless "ok" or if missing)
"""
import argparse
import sys

from . import db, storage
from .migrate import MigrationError, migrate_legacy


def check(db_path):
    """#69: read-only PRAGMA integrity_check (mode=ro: never creates or migrates the db). 0 only for "ok";
    a missing database is an error too (Hale)."""
    import os
    import sqlite3
    if not os.path.exists(db_path):
        print(f"ERROR: database not found: {db_path}", file=sys.stderr)
        return 1
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=10)
        try:
            rows = [r[0] for r in conn.execute("PRAGMA integrity_check")]
        finally:
            conn.close()
    except sqlite3.Error as e:
        print(f"ERROR: integrity check failed: {e}", file=sys.stderr)
        return 1
    print("integrity: " + "; ".join(rows[:5]))
    return 0 if rows == ["ok"] else 1


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m memoryframe")
    sub = ap.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("migrate", help="back up and import legacy uploads/")
    m.add_argument("--no-backup", action="store_true",
                   help="skip the tar backup (not recommended)")
    sub.add_parser("status", help="show data dir and counts")
    d = sub.add_parser("display-copies", help="create missing display copies")
    d.add_argument("--force", action="store_true", help="re-create all copies (e.g. new size)")
    sub.add_parser("check", help="database integrity check")
    args = ap.parse_args(argv)
    config = storage.default_config()

    if args.cmd == "check":
        return check(config["DB_PATH"])

    if args.cmd == "migrate":
        try:
            migrate_legacy(config, backup=not args.no_backup)
        except MigrationError as e:
            print(f"ERROR: {e}", file=sys.stderr)
            return 1
        return 0

    storage.ensure_layout(config["DATA_DIR"])
    if args.cmd == "display-copies":
        from . import imaging
        db.init_db(config["DB_PATH"])
        conn = db.connect(config["DB_PATH"])
        try:
            made, failed = imaging.backfill(conn, config["DATA_DIR"],
                                            imaging.target_or_default(config["DISPLAY_SIZE"]), args.force)
        finally:
            conn.close()
        print(f"display copies: {made} made, {failed} failed")
        return 1 if failed else 0
    db.init_db(config["DB_PATH"])
    conn = db.connect(config["DB_PATH"])
    try:
        total = conn.execute("SELECT COUNT(*) FROM media").fetchone()[0]
        print(f"data dir:      {config['DATA_DIR']}")
        print(f"database:      {config['DB_PATH']}")
        print(f"media:         {total}")
        print(f"legacy folder: {config['LEGACY_UPLOADS']}")
        print(f"legacy import: {db.get_setting(conn, 'legacy_import_done', 'not done')}")
        print(f"backup:        {db.get_setting(conn, 'legacy_import_backup', '-')}")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
