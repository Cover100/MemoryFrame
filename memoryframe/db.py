"""SQLite schema and connection handling (one connection per worker thread).

Waitress serves with several threads in one process. Opening and closing a WAL
database on every request produces an IN_CLOSE_WRITE on the *-wal file even for
SELECT-only work (#132 / #97 S2). Reuse one connection for the life of each
worker thread; request teardown only rolls back leftover transaction state.

In TESTING, teardown closes the worker connection unless DB_REUSE_IN_TESTS is
set: Werkzeug's threaded live_server creates short-lived request threads, and
leaving those handles open leaks FDs until select() breaks (fd > 1023).
Production Waitress keeps a fixed thread pool, so reuse there does not leak.
check_same_thread stays True; atexit only closes connections this thread still
owns — the OS reclaims any other FDs on process exit.
"""
import atexit
import sqlite3
import threading
import time

from flask import current_app, g

SCHEMA_VERSION = 3      # 2: #102 media.state + media.duration_ms; 3: #103 trim, progress, cancel, resumed

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
    hidden        INTEGER NOT NULL DEFAULT 0,  -- unused since #80 (cleared by init_db)
    created_at    REAL NOT NULL,
    state         TEXT NOT NULL DEFAULT 'ready'
                  CHECK (state IN ('queued', 'processing', 'ready', 'failed')),   -- #102: videos convert first
    duration_ms   INTEGER,                                                        -- #102: videos only
    -- #103 (videos only): the part to keep, picked on the phone and checked against the probe; NULL = default
    trim_start_ms INTEGER,
    trim_end_ms   INTEGER,
    progress_pct  INTEGER NOT NULL DEFAULT 0,  -- 0-99 while converting (100 only once ready)
    cancel_requested INTEGER NOT NULL DEFAULT 0,  -- Cancel while 'processing': the worker stops ffmpeg
    resumed       INTEGER NOT NULL DEFAULT 0   -- requeued after a restart (phone: "Resumed after restart")
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

# One SQLite connection per OS thread (Waitress worker). Do not share across threads
# (check_same_thread stays True). Flask `g` alone is not enough: teardown pops it
# every request, which would force a reconnect.
_local = threading.local()
_registry_lock = threading.Lock()
# Live worker connections for best-effort process-exit close (owner thread only).
_registry = []


def connect(db_path):
    conn = sqlite3.connect(db_path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def init_db(db_path):
    # Short-lived bootstrap connection: not the per-thread worker handle.
    conn = connect(db_path)
    try:
        conn.executescript(SCHEMA)
        row = conn.execute("SELECT version FROM schema_version").fetchone()
        if row is None:
            conn.execute("INSERT INTO schema_version (version) VALUES (?)", (SCHEMA_VERSION,))
        migrate(conn)
        # #80 (user decision 19 Oct 2026): Hide is gone and photos hidden before all show again. Clearing at
        # every start is idempotent and cheap; the column stays so older databases need no schema change.
        conn.execute("UPDATE media SET hidden = 0 WHERE hidden <> 0")
        conn.commit()
    finally:
        conn.close()


def migrate(conn):
    """Bring an older database up to SCHEMA_VERSION. Each step is idempotent (checked by column), so a
    power cut between steps is safe; the version is bumped in the same transaction as the last step."""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(media)")}
    if "state" not in cols:        # 1 -> 2 (#102): every existing row is a ready photo
        conn.execute("ALTER TABLE media ADD COLUMN state TEXT NOT NULL DEFAULT 'ready' "
                     "CHECK (state IN ('queued', 'processing', 'ready', 'failed'))")
    if "duration_ms" not in cols:
        conn.execute("ALTER TABLE media ADD COLUMN duration_ms INTEGER")
    for col, decl in (("trim_start_ms", "INTEGER"), ("trim_end_ms", "INTEGER"),            # 2 -> 3 (#103)
                      ("progress_pct", "INTEGER NOT NULL DEFAULT 0"),
                      ("cancel_requested", "INTEGER NOT NULL DEFAULT 0"),
                      ("resumed", "INTEGER NOT NULL DEFAULT 0")):
        if col not in cols:
            conn.execute(f"ALTER TABLE media ADD COLUMN {col} {decl}")
    conn.execute("UPDATE schema_version SET version = ? WHERE version < ?", (SCHEMA_VERSION, SCHEMA_VERSION))


def _worker_connection(db_path):
    """Return the thread-local connection for db_path, opening one if needed."""
    conn = getattr(_local, "db", None)
    cached_path = getattr(_local, "db_path", None)
    if conn is not None and cached_path == db_path:
        return conn
    # Path change (common in tests) or first use on this thread: drop the old handle.
    if conn is not None:
        _forget_connection(conn)
        try:
            conn.close()
        except sqlite3.Error:
            pass
    conn = connect(db_path)
    _local.db = conn
    _local.db_path = db_path
    with _registry_lock:
        _registry.append(conn)
    return conn


def _forget_connection(conn):
    with _registry_lock:
        try:
            _registry.remove(conn)
        except ValueError:
            pass


def get_db():
    path = current_app.config["DB_PATH"]
    # Cache on g for the rest of this request so callers share one handle, but the
    # durable reuse lives on the thread (g is popped at teardown).
    if "db" in g:
        return g.db
    g.db = _worker_connection(path)
    return g.db


def close_db(_exc=None):
    """Request teardown: drop request cache and roll back leftover txn state.

    Production (Waitress): do NOT close() — reuse the thread-local connection.
    Closing a WAL connection every request is what stormed the SD card (#132).

    TESTING (unless DB_REUSE_IN_TESTS): close after the request. Werkzeug's
    threaded live_server uses short-lived threads; leaving the handle open
    leaks one SQLite FD trio per request across the suite.
    """
    conn = g.pop("db", None)
    if conn is None:
        conn = getattr(_local, "db", None)
    if conn is not None:
        try:
            conn.rollback()
        except sqlite3.Error:
            pass
    try:
        testing = current_app.config.get("TESTING")
        keep = current_app.config.get("DB_REUSE_IN_TESTS")
    except RuntimeError:
        testing, keep = False, False
    if testing and not keep:
        dispose_worker_connection()


def dispose_worker_connection():
    """Close and forget this thread's worker connection (tests / path switches).

    Call after monkeypatching ``connect`` so the next ``get_db()`` opens a fresh
    instrumented connection. Production code should not need this.
    """
    # Safe outside a request (e.g. after a test forced a BaseException through teardown).
    try:
        g.pop("db", None)
    except RuntimeError:
        pass
    conn = getattr(_local, "db", None)
    _local.db = None
    _local.db_path = None
    if conn is not None:
        _forget_connection(conn)
        try:
            conn.close()
        except sqlite3.Error:
            pass


def _close_all_worker_connections():
    """Process exit: close worker connections this process still holds.

    check_same_thread stays True, so closing a connection owned by another
    (already-dead) thread can raise; ignore and rely on process teardown for
    those FDs. Prefer dispose/path-switch/TESTING close from the owner thread.
    """
    with _registry_lock:
        conns = list(_registry)
    for conn in conns:
        try:
            conn.close()
        except (sqlite3.Error, sqlite3.ProgrammingError):
            pass
    _local.db = None
    _local.db_path = None


atexit.register(_close_all_worker_connections)


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
