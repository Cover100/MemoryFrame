"""SQLite schema and connection handling (one connection per request)."""
import sqlite3
import time

from flask import current_app, g

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL);

CREATE TABLE IF NOT EXISTS media (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    stored_name   TEXT NOT NULL UNIQUE,      -- server-generated file name in originals/
    original_name TEXT NOT NULL,             -- name as uploaded/imported (display only)
    kind          TEXT NOT NULL CHECK (kind IN ('image', 'video')),
    sha256        TEXT NOT NULL,
    size_bytes    INTEGER NOT NULL,
    source        TEXT NOT NULL CHECK (source IN ('legacy', 'upload')),
    hidden        INTEGER NOT NULL DEFAULT 0,
    created_at    REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS media_sha256 ON media (sha256);

CREATE TABLE IF NOT EXISTS devices (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL,
    created_at REAL NOT NULL,
    last_seen  REAL
);

CREATE TABLE IF NOT EXISTS sessions (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id  INTEGER NOT NULL REFERENCES devices (id) ON DELETE CASCADE,
    token_hash TEXT NOT NULL UNIQUE,
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL,
    revoked    INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS wifi_change (           -- MF-18: at most one row (id = 1)
    id           INTEGER PRIMARY KEY CHECK (id = 1),
    state        TEXT NOT NULL,                    -- pending | committed | rolled_back
    old_ssid     TEXT NOT NULL,
    new_ssid     TEXT NOT NULL,
    new_password TEXT,                             -- only while pending; cleared afterwards
    old_profile  TEXT NOT NULL,
    new_profile  TEXT NOT NULL,
    deadline     REAL NOT NULL,
    reason       TEXT,                             -- rollback: timeout | activation | restart
    updated      REAL NOT NULL,
    notice_shown INTEGER NOT NULL DEFAULT 0,
    new_uuid     TEXT                              -- NM uuid of the pending profile (reconciliation)
);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def connect(db_path):
    conn = sqlite3.connect(db_path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def init_db(db_path):
    conn = connect(db_path)
    try:
        conn.executescript(SCHEMA)
        row = conn.execute("SELECT version FROM schema_version").fetchone()
        if row is None:
            conn.execute("INSERT INTO schema_version (version) VALUES (?)", (SCHEMA_VERSION,))
        conn.commit()
    finally:
        conn.close()


def get_db():
    if "db" not in g:
        g.db = connect(current_app.config["DB_PATH"])
    return g.db


def close_db(_exc=None):
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


def init_app(app):
    init_db(app.config["DB_PATH"])
    app.teardown_appcontext(close_db)


def get_setting(conn, key, default=None):
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(conn, key, value):
    conn.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?) "
        "ON CONFLICT (key) DO UPDATE SET value = excluded.value", (key, str(value)))


def now():
    return time.time()
