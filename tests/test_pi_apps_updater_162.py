"""#162 (13.6): deploy/boot-splash.sh pi-apps-updater off|on moves ONLY a Pi-Apps updater entry aside (Exec= runs
pi-apps/updater), restores it byte-identical, is idempotent, and MEMORYFRAME_KEEP_PI_APPS_UPDATER=1 skips it."""
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "deploy" / "boot-splash.sh"
# Hale's file (findings.md follow-up)
ENTRY = """[Desktop Entry]
Name=Pi-Apps Updater
Exec=/home/memoryframe/pi-apps/updater onboot
Icon=/home/memoryframe/pi-apps/icons/logo.png
Terminal=false
StartupWMClass=Pi-Apps
Type=Application
X-GNOME-Autostart-enabled=true
Hidden=false
NoDisplay=false
"""


def run(home, *args, **extra):
    e = {"PATH": os.environ["PATH"], "HOME": str(home), **extra}
    r = subprocess.run(["bash", str(SCRIPT), "pi-apps-updater", *args], env=e, capture_output=True, text=True,
                       timeout=30)
    assert r.returncode == 0, r.stdout + r.stderr
    return r


def setup(tmp_path, text=ENTRY):
    home = tmp_path / "home"
    d = home / ".config" / "autostart"
    d.mkdir(parents=True)
    (d / "pi-apps-updater.desktop").write_text(text)
    (d / "memoryframe-kiosk.desktop").write_text("[Desktop Entry]\nExec=/opt/memoryframe/deploy/kiosk.sh\n")
    return home, d


def moved(d):
    return sorted(d.glob("pi-apps-updater.desktop.disabled-by-memoryframe.*"))


def test_off_moves_aside_on_restores_byte_identical(tmp_path):
    home, d = setup(tmp_path)
    st = os.stat(d / "pi-apps-updater.desktop")
    r = run(home, "off")
    assert not (d / "pi-apps-updater.desktop").exists() and len(moved(d)) == 1
    assert moved(d)[0].read_text() == ENTRY and "no longer starts" in r.stdout
    assert (d / "memoryframe-kiosk.desktop").exists()                             # nothing else touched
    run(home, "off")                                                               # idempotent: nothing to move
    assert len(moved(d)) == 1
    run(home, "on")
    f = d / "pi-apps-updater.desktop"
    assert f.read_text() == ENTRY and moved(d) == [] and os.stat(f).st_mtime_ns == st.st_mtime_ns
    assert not (home / ".config" / "memoryframe").exists()                         # state cleaned up
    run(home, "on")                                                                # twice: nothing to restore
    assert f.read_text() == ENTRY


def test_other_file_with_the_same_name_is_never_moved(tmp_path):
    other = "[Desktop Entry]\nExec=/usr/bin/something-else\n"
    home, d = setup(tmp_path, other)
    r = run(home, "off")
    assert (d / "pi-apps-updater.desktop").read_text() == other and moved(d) == []
    assert "does not run pi-apps/updater" in r.stdout


def test_keep_variable_skips(tmp_path):
    home, d = setup(tmp_path)
    r = run(home, "off", MEMORYFRAME_KEEP_PI_APPS_UPDATER="1")
    assert (d / "pi-apps-updater.desktop").read_text() == ENTRY and moved(d) == [] and "kept" in r.stdout


def test_recreated_entry_is_moved_again_and_on_never_overwrites(tmp_path):
    home, d = setup(tmp_path)
    run(home, "off")
    first = moved(d)[0]
    (d / "pi-apps-updater.desktop").write_text(ENTRY + "# recreated\n")             # Pi-Apps put it back
    r = run(home, "on")                                                            # live name taken: kept as is
    assert "exists again" in r.stdout and (d / "pi-apps-updater.desktop").read_text().endswith("# recreated\n")
    assert first.exists()


def test_no_entry_is_fine(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    assert "no Pi-Apps updater" in run(home, "off").stdout
    assert "nothing" in run(home, "on").stdout or "no Pi-Apps" in run(home, "on").stdout


def test_never_touches_pi_apps_itself():
    code = "\n".join(l.split("#", 1)[0] for l in SCRIPT.read_text().splitlines())
    assert "~/pi-apps" not in code and "$HOME/pi-apps" not in code and "rm -rf" not in code
