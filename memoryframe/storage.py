"""Data directory layout.

    <DATA_DIR>/
        memoryframe.db   SQLite database
        originals/       the stored photo: since #82 one shrunk, upright JPEG per upload (no full-size
                         original is kept); older photos and legacy imports are untouched originals
        display/         display copies (800x480 cover, MF-15)
        thumbs/          thumbnails (MF-15)
        tmp/             in-progress uploads
        backups/         migration backups

DATA_DIR comes from MEMORYFRAME_DATA_DIR (the Pi service sets
/var/lib/memoryframe). Without it, /var/lib/memoryframe is used if writable,
otherwise ./data next to the code (development / Windows).
"""
import logging
import os

log = logging.getLogger("memoryframe")

REPO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SYSTEM_DATA_DIR = "/var/lib/memoryframe"
SUBDIRS = ("originals", "display", "thumbs", "tmp", "backups")

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".heic", ".heif"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".webm"}


def media_kind(filename):
    ext = os.path.splitext(filename)[1].lower()
    if ext in IMAGE_EXTENSIONS:
        return "image"
    if ext in VIDEO_EXTENSIONS:
        return "video"
    return None


def _writable_dir(path):
    if os.path.isdir(path):
        return os.access(path, os.W_OK | os.X_OK)
    parent = os.path.dirname(path)
    return os.path.isdir(parent) and os.access(parent, os.W_OK | os.X_OK)


def resolve_data_dir():
    env = os.environ.get("MEMORYFRAME_DATA_DIR")
    if env:
        return os.path.abspath(env)
    if _writable_dir(SYSTEM_DATA_DIR):
        return SYSTEM_DATA_DIR
    fallback = os.path.join(REPO_DIR, "data")
    log.warning("Using development data dir %s (set MEMORYFRAME_DATA_DIR for production)", fallback)
    return fallback


# Hollis on #100 (F1): start-up cleanup can run while another process serves uploads (a dev run, or a
# second start racing the service). A file that changed in the last minute may belong to a live upload,
# so the tmp sweeps and the orphan reconcile leave it for the next start. An age check needs no lock
# shared between processes and cannot deadlock; a real upload's temp file never sits unchanged for 60 s.
SETTLE_S = 60


def settled(path, now=None):
    """True if path was last modified at least SETTLE_S ago (False if it vanished)."""
    import time
    try:
        return (now if now is not None else time.time()) - os.path.getmtime(path) >= SETTLE_S
    except OSError:
        return False


MAX_CAP_MP = 150      # no pixel cap may be set above this (Pillow's own limit is raised to exactly this, imaging.py)


def _env_int(name, default, maximum=None):
    """Positive int from the environment; anything else falls back to default (and is logged).
    Values above `maximum` are clamped to it (and logged)."""
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        v = int(raw)
        if v > 0:
            if maximum is not None and v > maximum:
                logging.getLogger(__name__).warning("%s=%d is above the limit; using %d", name, v, maximum)
                return maximum
            return v
    except ValueError:
        pass
    logging.getLogger(__name__).warning("%s=%r is not a positive whole number; using %d", name, raw, default)
    return default


def default_config():
    data_dir = resolve_data_dir()
    return {
        "DATA_DIR": data_dir,
        "DB_PATH": os.path.join(data_dir, "memoryframe.db"),
        "LEGACY_UPLOADS": os.environ.get(
            "MEMORYFRAME_LEGACY_UPLOADS", os.path.join(REPO_DIR, "uploads")),
        # MF-15: display copies cover this size (user decision 2026-10-11: 1000x600, exactly the 5:3 screen).
        # MF-14 upload limits
        "MAX_CONTENT_LENGTH": int(os.environ.get("MEMORYFRAME_MAX_UPLOAD_MB", "50")) * 1024 * 1024,
        "MAX_IMAGE_PIXELS": _env_int("MEMORYFRAME_MAX_MEGAPIXELS", 150, MAX_CAP_MP) * 1_000_000,
        # HEIC/PNG/WebP can't be decoded at reduced size, so they have their own, lower cap (#82).
        "MAX_NONJPEG_PIXELS": _env_int("MEMORYFRAME_MAX_NONJPEG_MEGAPIXELS", 50, MAX_CAP_MP) * 1_000_000,
        # #96: None = max(500 MB, 2% of the card) (Rowan's decision); MEMORYFRAME_MIN_FREE_MB overrides it
        "MIN_FREE_BYTES": (int(os.environ["MEMORYFRAME_MIN_FREE_MB"]) * 1024 * 1024
                           if os.environ.get("MEMORYFRAME_MIN_FREE_MB") else None),
        "AVG_PHOTO_BYTES": 1024 * 1024,   # #82: ~0.5 MB stored copy + ~0.25 MB display copy + thumb, rounded up
        "DISPLAY_SIZE": os.environ.get("MEMORYFRAME_DISPLAY_SIZE", "1000x600"),
    }


def ensure_layout(data_dir):
    for sub in SUBDIRS:
        os.makedirs(os.path.join(data_dir, sub), exist_ok=True)


def path(app, sub, name=""):
    return os.path.join(app.config["DATA_DIR"], sub, name)


MIN_FREE_FLOOR = 500 * 1024 * 1024
MIN_FREE_PCT = 0.02


def min_free(config, data_dir=None):
    """#96: the upload reserve. The configured value if set, else max(500 MB, 2% of the card)."""
    v = config.get("MIN_FREE_BYTES")
    if v is not None:
        return v
    import shutil
    try:
        total = shutil.disk_usage(data_dir or config["DATA_DIR"]).total
    except OSError:
        return MIN_FREE_FLOOR
    return max(MIN_FREE_FLOOR, int(total * MIN_FREE_PCT))
