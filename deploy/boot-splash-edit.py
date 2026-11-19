#!/usr/bin/env python3
"""#162: the boot-partition edits behind deploy/boot-splash.sh (quiet boot). Run as root by boot-splash.sh only.

  boot-splash-edit.py cmdline-on  CMDLINE STATE     append the missing quiet-boot tokens to cmdline.txt
  boot-splash-edit.py cmdline-off CMDLINE STATE     remove exactly the tokens cmdline-on added (recorded in STATE)
  boot-splash-edit.py config-on   CONFIG  STATE     disable_splash=1 in config.txt's [all] section
  boot-splash-edit.py config-off  CONFIG  STATE     undo config-on (byte-identical when untouched since)

cmdline.txt must be exactly ONE line with a root= token, else it is refused (warning, exit 0, nothing written).
Existing tokens are kept in order; a token whose key is already there (e.g. an existing loglevel=7) is never added
or changed, and is never removed by -off (only the recorded ones are). Before a change the file is copied to
<file>.memoryframe-bak.<timestamp>; the new text goes to a temp file in the same directory, is fsynced and renamed
over the old one (FAT on the Pi), so a power cut leaves the old or the new file. A run with nothing to do writes
nothing (no backup either). STATE (key=value lines, /etc/memoryframe/boot-splash.state) records what we added.
Never reads or writes anything else on the boot partition (memoryframe-mode.txt included)."""
import os
import re
import shutil
import sys
import time

TOKENS = ["quiet", "logo.nologo", "vt.global_cursor_default=0", "loglevel=3"]
MARK = "# MemoryFrame #162: no firmware rainbow splash (deploy/boot-splash.sh; undo: deploy/boot-splash.sh off)"
LINE = "disable_splash=1"
OFF_PREFIX = "#memoryframe-162 "            # an existing disable_splash=0 is commented with this, and restored by -off


def read_state(path):
    st = {}
    try:
        with open(path, encoding="utf-8") as f:
            for raw in f:
                k, sep, v = raw.rstrip("\n").partition("=")
                if sep and re.fullmatch(r"[a-z_]+", k):
                    st[k] = v
    except FileNotFoundError:
        pass
    return st


def write_state(path, st):
    st = {k: v for k, v in st.items() if v != ""}
    if not st:
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
        return
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".new"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write("".join(f"{k}={v}\n" for k, v in sorted(st.items())))
        f.flush()
        os.fsync(f.fileno())
    os.chmod(tmp, 0o644)
    os.replace(tmp, path)


def atomic_write(path, text):
    d = os.path.dirname(os.path.abspath(path))
    tmp = os.path.join(d, "." + os.path.basename(path) + ".new")
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    try:
        shutil.copymode(path, tmp)
    except OSError:
        pass                                  # vfat: mode comes from the mount
    os.replace(tmp, path)
    try:
        fd = os.open(d, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError:
        pass


def backup(path):
    dest = f"{path}.memoryframe-bak.{time.strftime('%Y%m%d%H%M%S')}"
    n = 1
    while os.path.exists(dest):
        dest = f"{path}.memoryframe-bak.{time.strftime('%Y%m%d%H%M%S')}.{n}"
        n += 1
    shutil.copyfile(path, dest)
    return dest


def read_text(path):
    with open(path, encoding="utf-8", newline="") as f:
        return f.read()


def split_cmdline(text):
    """(line, eol) for a one-line cmdline.txt, else None. eol is the final newline (kept as it was)."""
    eol = ""
    for e in ("\r\n", "\n"):
        if text.endswith(e):
            eol, text = e, text[:-len(e)]
            break
    if "\n" in text or "\r" in text or not text.strip():
        return None
    return text, eol


def key(tok):
    return tok.split("=", 1)[0]


def cmdline_on(path, state):
    text = read_text(path)
    parts = split_cmdline(text)
    if parts is None:
        print(f"boot-splash: WARNING: {path} is not exactly one line: left unchanged (quiet boot not set up)")
        return 0
    line, eol = parts
    toks = line.split()
    if not any(t.startswith("root=") for t in toks):
        print(f"boot-splash: WARNING: {path} has no root= token: left unchanged (quiet boot not set up)")
        return 0
    have = {key(t) for t in toks}
    add = [t for t in TOKENS if key(t) not in have]
    st = read_state(state)
    if not add:
        print("boot-splash: cmdline.txt already has the quiet-boot tokens")
        return 0
    body = line.rstrip()
    new = body + " " + " ".join(add) + line[len(body):] + eol
    b = backup(path)
    atomic_write(path, new)
    recorded = [t for t in st.get("cmdline_added", "").split() if t]
    st["cmdline_added"] = " ".join(recorded + [t for t in add if t not in recorded])
    write_state(state, st)
    print(f"boot-splash: added '{' '.join(add)}' to {path} (backup {os.path.basename(b)}; reboot to apply)")
    return 0


def cmdline_off(path, state):
    st = read_state(state)
    added = [t for t in st.get("cmdline_added", "").split() if t]
    if not added:
        print("boot-splash: no cmdline.txt tokens of ours to remove")
        return 0
    if not os.path.exists(path):
        print(f"boot-splash: WARNING: {path} not found")
        return 0
    parts = split_cmdline(read_text(path))
    if parts is None:
        print(f"boot-splash: WARNING: {path} is not exactly one line: left unchanged; remove '{' '.join(added)}' by hand")
        return 0
    line, eol = parts
    new = line
    for t in added:
        m = re.search(r"(^|[ \t]+)" + re.escape(t) + r"(?=[ \t]|$)", new)
        if m:
            end = m.end()
            if m.start() == 0 and new[end:end + 1] in (" ", "\t"):
                end += 1                       # token was first: drop the space after it instead
            new = new[:m.start()] + new[end:]
    st.pop("cmdline_added", None)
    if new != line:
        b = backup(path)
        atomic_write(path, new + eol)
        print(f"boot-splash: removed '{' '.join(added)}' from {path} (backup {os.path.basename(b)}; reboot to apply)")
    write_state(state, st)
    return 0


SECTION = re.compile(r"^\s*\[([^\]]*)\]\s*$")


def config_on(path, state):
    text = read_text(path)
    lines = text.split("\n")
    if any(l.strip() == MARK for l in lines):
        print("boot-splash: config.txt already has disable_splash=1")
        return 0
    section = "all"
    for l in lines:
        m = SECTION.match(l)
        if m:
            section = m.group(1).strip().lower()
        elif re.fullmatch(r"\s*disable_splash\s*=\s*1\s*", l) and section == "all":
            print("boot-splash: config.txt already has disable_splash=1 (not ours; left as is)")
            return 0
    st = read_state(state)
    commented = 0
    for i, l in enumerate(lines):
        if re.fullmatch(r"\s*disable_splash\s*=\s*0\s*", l):
            lines[i] = OFF_PREFIX + l
            commented += 1
    new = "\n".join(lines)
    added_nl = bool(new) and not new.endswith("\n")
    if added_nl:
        new += "\n"
    header = section != "all"
    new += ("[all]\n" if header else "") + MARK + "\n" + LINE + "\n"
    b = backup(path)
    atomic_write(path, new)
    st.update(config_added="1", config_header="1" if header else "", config_nl="1" if added_nl else "",
              config_commented=str(commented) if commented else "")
    write_state(state, st)
    print(f"boot-splash: added {LINE} to [all] in {path} (backup {os.path.basename(b)}; reboot to apply)")
    return 0


def config_off(path, state):
    st = read_state(state)
    if st.get("config_added") != "1" and not st.get("config_commented"):
        print("boot-splash: no config.txt lines of ours to remove")
        return 0
    if not os.path.exists(path):
        print(f"boot-splash: WARNING: {path} not found")
        return 0
    text = read_text(path)
    lines = text.split("\n")
    out = []
    i = 0
    while i < len(lines):
        l = lines[i]
        if l.strip() == MARK:
            j = i + 1
            if j < len(lines) and lines[j].strip() == LINE:
                j += 1
            # our [all] header goes too when our two lines were all that followed it
            if (st.get("config_header") == "1" and out and out[-1].strip().lower() == "[all]"
                    and all(not x.strip() for x in lines[j:])):
                out.pop()
            i = j
            continue
        if l.startswith(OFF_PREFIX):
            l = l[len(OFF_PREFIX):]
        out.append(l)
        i += 1
    new = "\n".join(out)
    if st.get("config_nl") == "1" and new.endswith("\n") and not new.endswith("\n\n"):
        new = new[:-1]
    for k in ("config_added", "config_header", "config_nl", "config_commented"):
        st.pop(k, None)
    if new != text:
        b = backup(path)
        atomic_write(path, new)
        print(f"boot-splash: removed our disable_splash lines from {path} (backup {os.path.basename(b)})")
    write_state(state, st)
    return 0


def main(argv):
    cmds = {"cmdline-on": cmdline_on, "cmdline-off": cmdline_off, "config-on": config_on, "config-off": config_off}
    if len(argv) != 4 or argv[1] not in cmds:
        print(__doc__, file=sys.stderr)
        return 2
    path, state = argv[2], argv[3]
    if not os.path.isfile(path):
        print(f"boot-splash: WARNING: {path} not found: skipped")
        return 0
    return cmds[argv[1]](path, state)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
