#!/usr/bin/env python3
"""#155: add or remove Memory Frame's marked labwc keybind (WarpCursor + HideCursor on Alt+Super+H) in the
desktop user's ~/.config/labwc/rc.xml. kiosk.sh presses that key with wtype so labwc hides its pointer at boot
(labwc draws it from session start; the page's CSS cursor:none only applies after a pointer event).

  labwc-hide-cursor.py add    RC_XML [--system-rc FILE] [--merge-config]
  labwc-hide-cursor.py remove RC_XML

add:    the marker already in RC_XML -> nothing is written (idempotent). Otherwise the ONE marked block is inserted
        right after the first <keyboard> open tag (outside comments), or inside a new <keyboard> just before the
        last </labwc_config>; every other byte is kept. The first edit keeps a copy, RC_XML.mf-bak.
        A missing RC_XML is created: a minimal file when labwc merges configs (--merge-config, labwc -m: the
        user file augments /etc/xdg/labwc/rc.xml), else a copy of --system-rc plus the block (without -m labwc
        reads only the first rc.xml it finds, so a minimal file would hide the system one).
remove: takes out exactly what add inserted (byte-identical to before when untouched since), else the lines from
        the marker to the end marker.
Writes are atomic (temp file + rename, mode kept). Never touches anything but RC_XML (and RC_XML.mf-bak).
Exit 0 on success or nothing to do; 3 if RC_XML can't be edited safely (no <labwc_config>), left unchanged."""
import os
import re
import sys
import tempfile

MARK = "memoryframe-managed hide-cursor"
BLOCK = ("    <!-- memoryframe-managed hide-cursor (#155): kiosk.sh presses Alt+Super+H (wtype) to hide the pointer."
         " Undo: delete down to the end marker, then run: labwc -r -->\n"
         '    <keybind key="A-W-h">\n'
         '      <action name="WarpCursor" x="-1" y="-1" />\n'
         '      <action name="HideCursor" />\n'
         "    </keybind>\n"
         "    <!-- /memoryframe-managed hide-cursor -->")   # no "--" inside an XML comment (rc.xml must parse)
WRAPPED = "  <keyboard>\n" + BLOCK + "\n  </keyboard>\n"
MINIMAL = '<?xml version="1.0"?>\n<labwc_config>\n' + WRAPPED + "</labwc_config>\n"


def _outside_comments(text, pattern):
    """Matches of pattern that are not inside <!-- ... -->."""
    spans = [(m.start(), m.end()) for m in re.finditer(r"<!--.*?-->", text, re.S)]
    return [m for m in re.finditer(pattern, text) if not any(a <= m.start() < b for a, b in spans)]


def insert(text):
    """Return text with the block added, or None if there is no safe place for it."""
    opens = _outside_comments(text, r"<keyboard(\s[^>]*)?(/?)>")
    if opens:
        m = opens[0]
        if m.group(2):                                          # <keyboard/>: open it up
            tag = m.group(0)[:-2].rstrip() + ">"
            return text[:m.start()] + tag + "\n" + BLOCK + "\n  </keyboard>" + text[m.end():]
        return text[:m.end()] + "\n" + BLOCK + text[m.end():]
    ends = _outside_comments(text, r"</labwc_config\s*>")
    if not ends:
        return None
    m = ends[-1]
    line_start = text.rfind("\n", 0, m.start()) + 1
    if text[line_start:m.start()].strip() == "":                # the end tag starts its line: new lines before it
        return text[:line_start] + WRAPPED + text[line_start:]
    return text[:m.start()] + "\n" + WRAPPED + text[m.start():]


def remove(text):
    for added in (WRAPPED, "\n" + BLOCK):                      # exactly what insert() added
        if added in text:
            return text.replace(added, "", 1)
    start = text.find("<!-- " + MARK)
    if start < 0:
        return text
    end = text.find("<!-- /" + MARK, start)
    if end < 0:
        return text
    end = text.find("-->", end) + 3
    line_start = text.rfind("\n", 0, start) + 1
    if text[line_start:start].strip() == "":
        start = line_start
    if text[end:end + 1] == "\n":
        end += 1
    return text[:start] + text[end:]


def write_atomic(path, data, mode):
    d = os.path.dirname(path) or "."
    fd, tmp = tempfile.mkstemp(prefix=".rc.xml.", dir=d)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def main(argv):
    if len(argv) < 3 or argv[1] not in ("add", "remove"):
        print(__doc__.split("\n\n")[1], file=sys.stderr)
        return 2
    cmd, rc = argv[1], argv[2]
    system_rc = argv[argv.index("--system-rc") + 1] if "--system-rc" in argv else ""
    merge = "--merge-config" in argv
    if cmd == "remove":
        if not os.path.exists(rc):
            print(f"labwc: {rc} missing: nothing to remove")
            return 0
        raw = open(rc, "rb").read()
        new = remove(raw.decode("utf-8", "surrogateescape")).encode("utf-8", "surrogateescape")
        if new == raw:
            print(f"labwc: no Memory Frame hide-cursor block in {rc}")
            return 0
        write_atomic(rc, new, os.stat(rc).st_mode & 0o7777)
        print(f"labwc: removed the hide-cursor block from {rc}")
        return 0
    if os.path.exists(rc):
        raw = open(rc, "rb").read()
        text = raw.decode("utf-8", "surrogateescape")
        if MARK in text:
            print(f"labwc: hide-cursor keybind already in {rc}: unchanged")
            return 0
        new = insert(text)
        if new is None:
            print(f"WARNING: {rc} has no <labwc_config> element: left unchanged, pointer hiding not set up",
                  file=sys.stderr)
            return 3
        bak = rc + ".mf-bak"
        if not os.path.exists(bak):
            write_atomic(bak, raw, os.stat(rc).st_mode & 0o7777)
        write_atomic(rc, new.encode("utf-8", "surrogateescape"), os.stat(rc).st_mode & 0o7777)
        print(f"labwc: added the hide-cursor keybind to {rc} (copy of the old file: {bak})")
        return 0
    os.makedirs(os.path.dirname(rc) or ".", exist_ok=True)
    if not merge and system_rc and os.path.isfile(system_rc):
        base = open(system_rc, "rb").read().decode("utf-8", "surrogateescape")
        new = insert(base)
        if new is None:
            new = MINIMAL
        how = f"a copy of {system_rc} plus the hide-cursor keybind (labwc without --merge-config)"
    else:
        new, how = MINIMAL, "a minimal file with the hide-cursor keybind"
    write_atomic(rc, new.encode("utf-8", "surrogateescape"), 0o644)
    print(f"labwc: created {rc}: {how}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
