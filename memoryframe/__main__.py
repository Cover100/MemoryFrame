"""Command line: python -m memoryframe <command>

  migrate [--no-backup]   back up and import the legacy uploads/ folder (one-time)
  status                  show data dir, database and media counts
"""
import argparse
import sys

from . import db, storage
from .migrate import MigrationError, migrate_legacy


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m memoryframe")
    sub = ap.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("migrate", help="back up and import legacy uploads/")
    m.add_argument("--no-backup", action="store_true",
                   help="skip the tar backup (not recommended)")
    sub.add_parser("status", help="show data dir and counts")
    args = ap.parse_args(argv)
    config = storage.default_config()

    if args.cmd == "migrate":
        try:
            migrate_legacy(config, backup=not args.no_backup)
        except MigrationError as e:
            print(f"ERROR: {e}", file=sys.stderr)
            return 1
        return 0

    storage.ensure_layout(config["DATA_DIR"])
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
