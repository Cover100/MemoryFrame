"""#155: the mouse pointer is hidden at boot (Sage's S1 + P, cursor-155/index.md + addendum).

install.sh apt-installs wtype and merges ONE marked labwc keybind block (WarpCursor + HideCursor on Alt+Super+H)
into the desktop user's ~/.config/labwc/rc.xml: an existing file (on the Pi: touch mapping with mouseEmulation for
DSI-1, plus the theme) keeps every other byte, a re-run changes nothing, a missing file is created. Then labwc is
reloaded, only when the file changed and a labwc session runs. The mode file and netmode are never touched.
style.css: no pointer image anywhere on the frame page. (kiosk.sh pressing the key: test_kiosk_hide_cursor_155.py.)
Hermetic: install.sh with stubbed sudo/apt/systemctl/pgrep/labwc (test_install_never_arms_product.run_install)."""
import hashlib
import importlib.util
import os
import re
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from test_install_never_arms_product import STUB, SUDO, run_install
from test_netmode_script import env, mutations  # noqa: F401  (fixture)

ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "deploy" / "labwc-hide-cursor.py"
MARK = "memoryframe-managed hide-cursor"
# The Pi's ~/.config/labwc/rc.xml as Hale found it (findings.md): touch mapping + theme, no <keyboard>
PI_RC = ('<?xml version="1.0"?>\n<labwc_config>\n'
         '  <touch deviceName="10-0038 generic ft5x06 (00)" mapToOutput="DSI-1" mouseEmulation="yes"/>\n'
         '  <theme>\n    <name>PiXonyx</name>\n    <font place="">\n      <name>Nunito Sans</name>\n'
         '      <size>12</size>\n    </font>\n  </theme>\n</labwc_config>\n')
SYSTEM_RC = ('<?xml version="1.0"?>\n<!-- system defaults -->\n<labwc_config>\n  <core><gap>0</gap></core>\n'
             '  <keyboard>\n    <keybind key="A-Tab"><action name="NextWindow"/></keybind>\n  </keyboard>\n'
             '</labwc_config>\n')


def _helper():
    spec = importlib.util.spec_from_file_location("labwc_hide_cursor", HELPER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def rc_path(tmp_path):
    return tmp_path / "home" / ".config" / "labwc" / "rc.xml"


def install(tmp_path, env, **kw):
    r, log = run_install(tmp_path, env, **kw)
    assert r.returncode == 0, r.stdout + r.stderr
    assert not re.search(r"^netmode\.sh ", log, re.M) and mutations(env) == []      # #40: no network change
    return r, log


def keybinds(xml_text):
    root = ET.fromstring(xml_text.encode())
    return [(kb.get("key"), [(a.get("name"), a.get("x"), a.get("y")) for a in kb.findall("action")])
            for kb in root.iter("keybind")]


HIDE = ("A-W-h", [("WarpCursor", "-1", "-1"), ("HideCursor", None, None)])


def test_existing_pi_rc_xml_survives_two_installs_with_one_block(env, tmp_path):
    rc = rc_path(tmp_path)
    rc.parent.mkdir(parents=True)
    rc.write_text(PI_RC)
    os.chmod(rc, 0o640)
    r1, _ = install(tmp_path, env)
    once = rc.read_bytes()
    text = once.decode()
    assert text.count(MARK) == 2 and text.count("<!-- " + MARK) == 1               # ONE block (open + end marker)
    assert text.replace(_helper().WRAPPED, "", 1) == PI_RC                          # every other byte kept
    assert keybinds(text) == [HIDE]                                                 # parses; the one keybind
    root = ET.fromstring(once)
    assert root.find("touch").attrib == {"deviceName": "10-0038 generic ft5x06 (00)", "mapToOutput": "DSI-1",
                                         "mouseEmulation": "yes"}
    assert root.find("theme/name").text == "PiXonyx"
    assert (rc.parent / "rc.xml.mf-bak").read_text() == PI_RC and oct(rc.stat().st_mode & 0o777) == "0o640"
    assert "added the hide-cursor keybind" in r1.stdout
    mtime = rc.stat().st_mtime_ns
    r2, log2 = install(tmp_path, env)                                               # re-run (deploy again)
    assert rc.read_bytes() == once and rc.stat().st_mtime_ns == mtime                # byte-identical, not rewritten
    assert (rc.parent / "rc.xml.mf-bak").read_text() == PI_RC
    assert "already in" in r2.stdout and "--reconfigure" not in log2
    assert sorted(p.name for p in rc.parent.iterdir()) == ["rc.xml", "rc.xml.mf-bak"]   # no temp files left


def test_block_goes_inside_an_existing_keyboard_element(env, tmp_path):
    rc = rc_path(tmp_path)
    rc.parent.mkdir(parents=True)
    before = ('<labwc_config>\n  <!-- <keyboard> in a comment is skipped -->\n  <keyboard>\n'
              '    <repeatRate>25</repeatRate>\n  </keyboard>\n</labwc_config>')            # no final newline
    rc.write_text(before)
    install(tmp_path, env)
    after = rc.read_text()
    assert after == before.replace("  <keyboard>\n", "  <keyboard>\n" + _helper().BLOCK + "\n", 1)
    assert keybinds(after) == [HIDE] and _helper().remove(after) == before


def test_missing_rc_xml_is_created_minimal_when_labwc_merges(env, tmp_path):
    (tmp_path / "xdg" / "labwc").mkdir(parents=True)
    (tmp_path / "xdg" / "labwc" / "rc.xml").write_text(SYSTEM_RC)
    r, log = install(tmp_path, env, extra_env={"STUB_LABWC_PS": "4321 /usr/bin/labwc -m"})
    rc = rc_path(tmp_path)
    assert rc.read_text() == _helper().MINIMAL and keybinds(rc.read_text()) == [HIDE]
    assert not (rc.parent / "rc.xml.mf-bak").exists() and "created" in r.stdout
    assert "labwc --reconfigure" in log and "LABWC_PID=4321" in log                  # reloaded: session running


def test_missing_rc_xml_copies_the_system_one_without_merge_config(env, tmp_path):
    (tmp_path / "xdg" / "labwc").mkdir(parents=True)
    (tmp_path / "xdg" / "labwc" / "rc.xml").write_text(SYSTEM_RC)
    _r, log = install(tmp_path, env, extra_env={"STUB_LABWC_PS": "77 /usr/bin/labwc"})
    text = rc_path(tmp_path).read_text()
    assert _helper().remove(text) == SYSTEM_RC                                     # system content + the block
    assert keybinds(text)[0] == HIDE and ("A-Tab", [("NextWindow", None, None)]) in keybinds(text)
    assert "LABWC_PID=77" in log


def test_labwc_pi_wrapper_with_m_counts_as_merge_config(env, tmp_path):
    (tmp_path / "xdg" / "labwc").mkdir(parents=True)
    (tmp_path / "xdg" / "labwc" / "rc.xml").write_text(SYSTEM_RC)
    (tmp_path / "labwc-pi").write_text("#!/bin/sh\nexec /usr/bin/labwc -m \"$@\"\n")
    _r, log = install(tmp_path, env)                                                  # no session running
    assert rc_path(tmp_path).read_text() == _helper().MINIMAL
    assert "labwc --reconfigure" not in log                                          # nothing to reload


def test_no_labwc_session_no_reconfigure_and_reconfigure_failure_only_warns(env, tmp_path):
    _r, log = install(tmp_path, env)
    assert rc_path(tmp_path).exists() and "labwc --reconfigure" not in log
    rc_path(tmp_path).unlink()
    r, log = install(tmp_path, env, extra_env={"STUB_LABWC_PS": "9 labwc -m", "STUB_LABWC_RC": "1"})
    assert "labwc --reconfigure" in log and "WARNING: labwc --reconfigure failed" in r.stderr


@pytest.mark.parametrize("junk", ["", "not xml at all\n", '<?xml version="1.0"?>\n<labwc_config/>\n',
                                  "<labwc_config>\n  <theme/>\n"])          # empty, no root, self-closing, cut off
def test_unusable_rc_xml_is_left_alone_and_install_goes_on(env, tmp_path, junk):
    rc = rc_path(tmp_path)
    rc.parent.mkdir(parents=True)
    rc.write_text(junk)
    r, log = install(tmp_path, env, extra_env={"STUB_LABWC_PS": "9 labwc -m"})
    assert rc.read_text() == junk and not (rc.parent / "rc.xml.mf-bak").exists()
    assert "WARNING" in r.stderr and "labwc --reconfigure" not in log


def test_wtype_is_installed_and_its_failure_only_warns(env, tmp_path):
    _r, log = install(tmp_path, env)
    assert "sudo apt-get install -y wtype" in log
    no_wtype = SUDO.replace("exit 0\n", '[[ " $* " == *" install -y wtype "* ]] && exit 100\nexit 0\n')
    r, _log = install(tmp_path, env, sudo=no_wtype)
    assert "WARNING: wtype could not be installed" in r.stderr


@pytest.mark.parametrize("existing", ["product\n", "dev\n", None])
def test_cursor_fix_never_touches_the_mode_file_or_netmode(env, tmp_path, existing):
    f = env["boot"] / "memoryframe-mode.txt"
    if existing is not None:
        f.write_text(existing)
    install(tmp_path, env, upgrade=True, extra_env={"STUB_LABWC_PS": "9 labwc -m"})
    assert rc_path(tmp_path).exists()
    if existing is not None:
        assert f.read_text() == existing                                           # #94 rules unchanged
    src = (ROOT / "deploy" / "install.sh").read_text()
    section = src[src.index('echo "== Hide the mouse pointer'):src.index('echo "== Session type')]
    assert "MODE_FILE" not in section and "netmode" not in section and "sudo" not in section


def test_remove_restores_the_file_exactly(tmp_path):
    h = _helper()
    for before in (PI_RC, SYSTEM_RC, "<labwc_config><keyboard/></labwc_config>\n"):
        rc = tmp_path / "rc.xml"
        rc.write_text(before)
        assert h.main(["x", "add", str(rc)]) == 0 and h.main(["x", "add", str(rc)]) == 0
        assert rc.read_text().count("<!-- " + MARK) == 1 and keybinds(rc.read_text())[0] == HIDE
        assert h.main(["x", "remove", str(rc)]) == 0
        got = rc.read_text()
        assert MARK not in got
        if "<keyboard/>" not in before:
            assert got == before
        (tmp_path / "rc.xml.mf-bak").unlink()


def test_block_is_valid_xml_and_uses_labwc_actions():
    h = _helper()
    assert all("--" not in c for c in re.findall(r"<!--(.*?)-->", h.BLOCK, re.S))       # XML: no -- in a comment
    assert keybinds(h.MINIMAL) == [HIDE]


def test_uninstall_removes_the_block():
    src = (ROOT / "deploy" / "uninstall.sh").read_text()
    assert 'labwc-hide-cursor.py" remove "$LABWC_RC"' in src and "labwc --reconfigure" in src


def test_readme_documents_the_rollback():
    readme = (ROOT / "README.md").read_text()
    sec = readme[readme.index("#155"):]
    assert "labwc --reconfigure" in sec[:2500] and MARK in sec[:2500] and "</openbox_config>" in sec[:2500]


# ---------- P: the frame page ----------

def test_frame_css_hides_the_cursor_everywhere():
    css = re.sub(r"/\*.*?\*/", "", (ROOT / "static" / "style.css").read_text(), flags=re.S)
    assert "*, *::before, *::after { cursor: none !important; }" in css
    assert "cursor: none !important" not in (ROOT / "static" / "phone.css").read_text()   # phone pages keep theirs
    linking = sorted(p.name for p in (ROOT / "templates").glob("*.html") if "style.css" in p.read_text())
    assert linking == ["index.html"]                                                   # the frame page only


# ---------- Hale (PR156 review): the frame's real rc.xml root is <openbox_config xmlns=...> ----------

DEVICE_RC = (ROOT / "tests" / "fixture_labwc_rc_device_155.xml").read_bytes()   # byte copy of Hale's transcription


def _device_variants():
    # the transcription as-is, and with the tab Hale reports on the device in front of <touch>
    tabbed = re.sub(rb"(?m)^ +(<touch )", rb"\t\1", DEVICE_RC)
    assert tabbed != DEVICE_RC or b"\t<touch" in DEVICE_RC
    return [DEVICE_RC, tabbed]


def test_device_fixture_is_the_transcribed_file():
    lines = DEVICE_RC.split(b"\n")
    assert lines[0] == b'<?xml version="1.0"?>' and lines[1] == b'<openbox_config xmlns="http://openbox.org/3.4/rc">'
    assert lines[2].lstrip().startswith(b"<touch ") and lines[3].startswith(b"<theme>")
    assert lines[3].endswith(b"</theme></openbox_config>") and lines[4:] == [b""]   # one line, then the final newline
    assert len(DEVICE_RC) == 457


@pytest.mark.parametrize("variant", [0, 1], ids=["as-transcribed", "tab-indented"])
def test_openbox_config_root_gets_the_block_before_its_end_tag(tmp_path, variant):
    h = _helper()
    before = _device_variants()[variant]
    rc = tmp_path / "rc.xml"
    rc.write_bytes(before)
    assert h.main(["x", "add", str(rc)]) == 0
    once = rc.read_bytes()
    head, tail = before.split(b"</openbox_config>")
    assert once == head + h.INLINE.encode() + b"</openbox_config>" + tail        # just before the root end tag
    assert once.replace(h.INLINE.encode(), b"", 1) == before                         # file minus block == original
    assert once.count(b"<!-- " + MARK.encode()) == 1
    root = ET.fromstring(once)                                                       # parses
    ns = "{http://openbox.org/3.4/rc}"
    assert root.tag == ns + "openbox_config"
    assert root.find(ns + "touch").attrib == {"deviceName": "10-0038 generic ft5x06 (00)",
                                              "mapToOutput": "DSI-1", "mouseEmulation": "yes"}
    assert root.find(ns + "theme/" + ns + "name").text == "PiXonyx"
    kb = root.findall(ns + "keyboard/" + ns + "keybind")
    assert [(k.get("key"), [(a.get("name"), a.get("x"), a.get("y")) for a in k]) for k in kb] == [HIDE]
    assert (tmp_path / "rc.xml.mf-bak").read_bytes() == before
    mtime, sha = rc.stat().st_mtime_ns, hashlib.sha256(once).hexdigest()
    assert h.main(["x", "add", str(rc)]) == 0                                       # second add: no-op
    assert hashlib.sha256(rc.read_bytes()).hexdigest() == sha and rc.stat().st_mtime_ns == mtime
    assert h.main(["x", "remove", str(rc)]) == 0
    assert rc.read_bytes() == before                                                # restored exactly


def test_openbox_config_root_with_a_keyboard_inserts_inside_it(tmp_path):
    h = _helper()
    before = DEVICE_RC.replace(b"<theme>", b"<keyboard><repeatRate>25</repeatRate></keyboard><theme>", 1)
    rc = tmp_path / "rc.xml"
    rc.write_bytes(before)
    assert h.main(["x", "add", str(rc)]) == 0
    once = rc.read_bytes()
    assert once == before.replace(b"<keyboard>", b"<keyboard>\n" + h.BLOCK.encode(), 1)
    ET.fromstring(once)
    assert h.main(["x", "remove", str(rc)]) == 0 and rc.read_bytes() == before


def test_device_rc_xml_through_install_twice(env, tmp_path):
    rc = rc_path(tmp_path)
    rc.parent.mkdir(parents=True)
    rc.write_bytes(DEVICE_RC)
    r1, log1 = install(tmp_path, env, extra_env={"STUB_LABWC_PS": "4321 /usr/bin/labwc -m"})
    once = rc.read_bytes()
    assert once.replace(_helper().INLINE.encode(), b"", 1) == DEVICE_RC and once.count(b"<!-- " + MARK.encode()) == 1
    ET.fromstring(once)
    assert "added the hide-cursor keybind" in r1.stdout and "LABWC_PID=4321" in log1 and "WARNING" not in r1.stderr
    _r2, log2 = install(tmp_path, env, extra_env={"STUB_LABWC_PS": "4321 /usr/bin/labwc -m"})
    assert hashlib.sha256(rc.read_bytes()).hexdigest() == hashlib.sha256(once).hexdigest()   # second add: same sha256
    assert log2.count("labwc --reconfigure") == 1                                   # the stub log is cumulative:
    #                                                                                  only the first run reloaded
