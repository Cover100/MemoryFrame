"""Data directory layout.

    <DATA_DIR>/
        memoryframe.db   SQLite database
        originals/       files as uploaded/imported (never modified)
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


def _env_int(name, default):
    """Positive int from the environment; anything else falls back to default (and is logged)."""
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        v = int(raw)
        if v > 0:
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
        "MAX_IMAGE_PIXELS": _env_int("MEMORYFRAME_MAX_MEGAPIXELS", 64) * 1_000_000,
        "MIN_FREE_BYTES": int(os.environ.get("MEMORYFRAME_MIN_FREE_MB", "500")) * 1024 * 1024,
        "AVG_PHOTO_BYTES": 5 * 1024 * 1024 + 300 * 1024,   # ~5 MB original + display copy
        "DISPLAY_SIZE": os.environ.get("MEMORYFRAME_DISPLAY_SIZE", "1000x600"),
    }


def ensure_layout(data_dir):
    for sub in SUBDIRS:
        os.makedirs(os.path.join(data_dir, sub), exist_ok=True)


def path(app, sub, name=""):
    return os.path.join(app.config["DATA_DIR"], sub, name)
