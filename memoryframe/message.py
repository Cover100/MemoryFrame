"""#73: the giver's message shown in the frame's double-tap menu.

Stored as UTF-8 text in DATA_DIR/frame_message.txt (/var/lib/memoryframe on the Pi), outside
the repo, so deploys and install.sh never touch it. Writes are atomic (temp file + fsync +
os.replace). The text is plain: it is only ever rendered through Jinja autoescaping or
textContent, never as HTML.

Note: unicodedata follows the running Python (3.11 = Unicode 14), so emoji newer than that
are "unassigned" (Cn) here and get stripped. Accepted as-is (Juniper, #75 review).
"""
import glob
import os
import tempfile
import unicodedata

MAX_LEN = 140
MAX_BODY = 4096           # bytes; larger PUT bodies get 413 before parsing
MAX_MARKS = 3             # combining marks kept per base character (anti-"Zalgo")
FILENAME = "frame_message.txt"
TMP_PREFIX, TMP_SUFFIX = "frame_message.", ".tmp"

# Characters that render as nothing: a message made only of these must count as empty.
INVISIBLE = {"\u115f", "\u1160", "\u3164", "\uffa0", "\u2800"}
JOINERS = {"\u200c", "\u200d"}       # ZWNJ / ZWJ: kept only between letters, marks or emoji
_NEWLINES = set("\n\r\t\u2028\u2029\x0b\x0c\x85")


def _path(data_dir):
    return os.path.join(data_dir, FILENAME)


def _joinable(ch):
    if not ch:
        return False
    cat = unicodedata.category(ch)
    return cat[0] in ("L", "M") or cat == "So" or ch == "\ufe0f" or 0x1F000 <= ord(ch) <= 0x1FAFF


def clean(raw):
    """-> (text, error). NFC first; then newlines/tabs become spaces, control/format/invisible
    characters are stripped (ZWJ/ZWNJ kept only between joinable neighbours), combining marks
    are capped at MAX_MARKS per base, spaces collapse and the ends are trimmed. The 140 limit
    applies to the result (code points). Empty means "clear"."""
    if not isinstance(raw, str):
        return None, "message must be a string"
    s = unicodedata.normalize("NFC", raw)
    out, marks = [], 0
    for i, ch in enumerate(s):
        cat = unicodedata.category(ch)
        if ch in _NEWLINES:
            out.append(" "); marks = 0
        elif ch in INVISIBLE:
            continue
        elif ch in JOINERS:
            nxt = s[i + 1] if i + 1 < len(s) else ""
            if out and _joinable(out[-1]) and _joinable(nxt):
                out.append(ch)
        elif ch == "\ufe0f" or cat.startswith("M"):           # VS16 behaves like a mark here
            if out and out[-1] != " " and marks < MAX_MARKS:
                out.append(ch); marks += 1
        elif cat in ("Cc", "Cf", "Cs", "Co", "Cn", "Zl", "Zp"):
            continue
        else:
            out.append(ch); marks = 0
    text = " ".join("".join(out).split())
    if len(text) > MAX_LEN:
        return None, f"message must be at most {MAX_LEN} characters"
    return text, None


def get(data_dir):
    """The stored message, or "" if none/unreadable. Re-cleaned on read (defence in depth)."""
    try:
        with open(_path(data_dir), encoding="utf-8", errors="replace") as f:
            raw = f.read(MAX_LEN * 8)
    except OSError:
        return ""
    text, err = clean(raw)
    return "" if err else text


def _fsync_dir(data_dir):
    try:
        dfd = os.open(data_dir, os.O_RDONLY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)
    except OSError:
        pass


def save(data_dir, text):
    """Atomically store text (already cleaned); "" removes the file. Returns False (and
    touches nothing) when it equals what is stored."""
    if text == get(data_dir) and (text or not os.path.exists(_path(data_dir))):
        return False
    path = _path(data_dir)
    if not text:
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
        _fsync_dir(data_dir)
        return True
    fd, tmp = tempfile.mkstemp(prefix=TMP_PREFIX, suffix=TMP_SUFFIX, dir=data_dir)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, 0o640)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise
    _fsync_dir(data_dir)
    return True


def sweep_tmp(data_dir):
    """Startup: remove temp files left by a crash mid-write."""
    from . import storage
    for p in glob.glob(os.path.join(glob.escape(data_dir), TMP_PREFIX + "*" + TMP_SUFFIX)):
        if not storage.settled(p):            # F1: may be a save in flight in another process
            continue
        try:
            os.remove(p)
        except OSError:
            pass
