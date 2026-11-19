#!/usr/bin/env python3
"""#162: add or remove Memory Frame's marked block in the desktop user's ~/.config/labwc/autostart, which starts
deploy/session-start.sh (splash, hide the desktop and panel, #155 pointer) as the first thing of the session.
labwc on Pi OS runs with -m (merge), so this user file is run IN ADDITION to /etc/xdg/labwc/autostart (that
package-owned file is never touched).

  labwc-autostart.py add    AUTOSTART SESSION_START_SH
  labwc-autostart.py remove AUTOSTART

add:    the block (marker line + one command line) goes at the top of AUTOSTART. Already there with the same command:
        nothing is written (idempotent); with another path: the command line is updated in place. Every other byte is
        kept, the first edit of an existing file keeps AUTOSTART.mf-bak, a missing file (and directory) is created.
remove: takes out exactly the block. If the file then holds nothing else and add created it, it is deleted.
Writes are atomic (temp file + rename, mode kept). Exit 0 on success or nothing to do."""
import os
import shlex
import sys
import tempfile

MARK = "memoryframe-managed boot-splash"
HEAD = (f"# {MARK} (#162): splash, no desktop or panel at boot. "
        "Undo: deploy/uninstall.sh, or delete this line and the next one")
CREATED = f"# {MARK}: file created by Memory Frame"


def block(cmd):
    return f"{HEAD}\n{shlex.quote(cmd)} &  # {MARK}\n"


def write(path, text, mode):
    d = os.path.dirname(path)
    fd, tmp = tempfile.mkstemp(prefix=".autostart.", dir=d)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def strip_block(text):
    """text without our marked lines (the head line, the command line, and our 'created' line)."""
    out = [l for l in text.splitlines(keepends=True)
           if MARK not in l]
    return "".join(out)


def add(path, cmd):
    want = block(cmd)
    if not os.path.exists(path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        write(path, CREATED + "\n" + want, 0o644)
        print(f"labwc autostart: created {path}")
        return 0
    with open(path, encoding="utf-8", newline="") as f:
        text = f.read()
    mode = os.stat(path).st_mode & 0o7777
    if MARK in text:
        lines = text.splitlines(keepends=True)
        new = []
        for l in lines:
            if MARK in l and not l.startswith("#"):
                l = want.splitlines(keepends=True)[1]
            new.append(l)
        new = "".join(new)
        if new == text:
            print("labwc autostart: already set up")
            return 0
        write(path, new, mode)
        print(f"labwc autostart: command updated in {path}")
        return 0
    bak = path + ".mf-bak"
    if not os.path.exists(bak):
        with open(bak, "w", encoding="utf-8", newline="") as f:
            f.write(text)
    write(path, want + text, mode)
    print(f"labwc autostart: block added at the top of {path} (backup {os.path.basename(bak)})")
    return 0


def remove(path):
    if not os.path.exists(path):
        print("labwc autostart: no file")
        return 0
    with open(path, encoding="utf-8", newline="") as f:
        text = f.read()
    if MARK not in text:
        print("labwc autostart: no block of ours")
        return 0
    created = text.startswith(CREATED + "\n")
    rest = strip_block(text)
    if created and not rest.strip():
        os.remove(path)
        print(f"labwc autostart: removed {path} (it was ours)")
        return 0
    write(path, rest, os.stat(path).st_mode & 0o7777)
    print(f"labwc autostart: block removed from {path}")
    return 0


def main(argv):
    if len(argv) == 4 and argv[1] == "add":
        return add(argv[2], argv[3])
    if len(argv) == 3 and argv[1] == "remove":
        return remove(argv[2])
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
