"""#73: the giver's message shown in the frame's double-tap menu.

Stored as UTF-8 text in DATA_DIR/frame_message.txt (/var/lib/memoryframe on the Pi), outside
the repo, so deploys and install.sh never touch it. Writes are atomic (temp file + fsync +
os.replace). The text is plain: it is only ever rendered through Jinja autoescaping or
textContent, never as HTML.
"""
import os
import tempfile
import unicodedata

MAX_LEN = 140
FILENAME = "frame_message.txt"


def _path(data_dir):
    return os.path.join(data_dir, FILENAME)


def clean(raw):
    """-> (text, error). Control/format characters are stripped (newlines and tabs become
    spaces), runs of spaces collapse, the ends are trimmed. Empty means "clear"."""
    if not isinstance(raw, str):
        return None, "message must be a string"
    out = []
    for ch in raw:
        cat = unicodedata.category(ch)
        if ch in "\n\r\t\u2028\u2029":
            out.append(" ")
        elif cat in ("Cc", "Cf", "Cs", "Co", "Cn", "Zl", "Zp"):
            continue
        else:
            out.append(ch)
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


def save(data_dir, text):
    """Atomically store text (already cleaned); "" removes the file."""
    path = _path(data_dir)
    if not text:
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
        return
    fd, tmp = tempfile.mkstemp(prefix=".frame_message.", dir=data_dir)
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
    try:
        dfd = os.open(data_dir, os.O_RDONLY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)
    except OSError:
        pass
