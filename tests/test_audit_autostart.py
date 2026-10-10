"""deploy/audit-autostart.sh must flag real Memory Frame/kiosk launchers only,
never files that merely contain the home path (/home/memoryframe/...)."""
import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "deploy" / "audit-autostart.sh"

PI_APPS = """[Desktop Entry]
Name=Pi-Apps Updater
Comment=Checks for Pi-Apps updates on boot
Exec=/home/memoryframe/pi-apps/updater onboot
Icon=/home/memoryframe/pi-apps/icons/logo.png
Terminal=false
Type=Application
"""

OLD_KIOSK = """[Desktop Entry]
Type=Application
Name=MemoryFrame
Exec=chromium --kiosk --noerrdialogs http://localhost:5000
"""

OLD_APP = """[Desktop Entry]
Type=Application
Name=MemoryFrame server
Exec=python3 /home/memoryframe/Documents/GitHub/MemoryFrame/app.py
"""

OUR_KIOSK = """[Desktop Entry]
Type=Application
Name=Memory Frame kiosk
Exec=/home/memoryframe/Documents/GitHub/MemoryFrame/deploy/kiosk.sh
"""

BASHRC = 'export PATH="/home/memoryframe/.local/bin:$PATH"\nalias mf="cd /home/memoryframe/Documents/GitHub/MemoryFrame"\n'

LABWC = ("/usr/bin/kanshi &\n"
         "/home/memoryframe/Documents/GitHub/MemoryFrame/deploy/kiosk.sh &  # memoryframe-managed\n"
         "chromium-browser --kiosk http://127.0.0.1:5000 &\n")

CRON = ("@reboot /home/memoryframe/pi-apps/updater onboot\n"
        "@reboot cd /home/memoryframe/Documents/GitHub/MemoryFrame && python3 app.py\n")


@pytest.fixture
def fake(tmp_path):
    home = tmp_path / "home"
    root = tmp_path / "root"
    auto = home / ".config" / "autostart"
    auto.mkdir(parents=True)
    (auto / "pi-apps-updater.desktop").write_text(PI_APPS)
    (auto / "memoryframe-old.desktop").write_text(OLD_KIOSK)
    (auto / "mf-server.desktop").write_text(OLD_APP)
    (auto / "memoryframe-kiosk.desktop").write_text(OUR_KIOSK)   # installed by install.sh: ignored
    (home / ".bashrc").write_text(BASHRC)
    (home / ".config" / "labwc").mkdir()
    (home / ".config" / "labwc" / "autostart").write_text(LABWC)
    (root / "etc" / "xdg" / "autostart").mkdir(parents=True)
    # Stateful crontab stub
    cron = tmp_path / "cron.txt"
    cron.write_text(CRON)
    stub = tmp_path / "crontab"
    stub.write_text(f'#!/bin/bash\nif [ "$1" = -l ]; then [ "$3" = root ] && exit 1; cat {cron}; '
                    f'else cat > {cron}.new && mv {cron}.new {cron}; fi\n')
    stub.chmod(0o755)
    env = dict(os.environ, AUDIT_HOME=str(home), AUDIT_ROOT=str(root), SUDO="",
               CRONTAB=str(stub), USER="memoryframe")
    env.pop("SUDO_USER", None)
    return home, root, cron, env


def run(env, *args):
    return subprocess.run(["bash", str(SCRIPT), *args], env=env, capture_output=True, text=True)


def found(out):
    return [l for l in out.splitlines() if l.startswith("FOUND:")]


def test_report_flags_only_real_launchers(fake):
    home, _, _, env = fake
    r = run(env)
    assert r.returncode == 0, r.stderr
    hits = "\n".join(found(r.stdout))
    assert "pi-apps-updater.desktop" not in hits          # path-only match: must NOT be flagged
    assert "memoryframe-old.desktop" in hits              # chromium --kiosk localhost:5000
    assert "mf-server.desktop" in hits                    # .../MemoryFrame/app.py
    assert "memoryframe-kiosk.desktop" not in hits        # our own entry
    assert ".bashrc" not in hits                          # only home paths
    assert "labwc/autostart" in hits                      # chromium-browser --kiosk line
    assert "crontab of memoryframe" in hits               # cd MemoryFrame && python3 app.py
    # report mode changes nothing
    assert (home / ".config/autostart/memoryframe-old.desktop").exists()


def test_disable_leaves_pi_apps_and_unrelated_lines(fake):
    home, _, cron, env = fake
    r = run(env, "--disable")
    assert r.returncode == 0, r.stderr
    auto = home / ".config" / "autostart"
    assert (auto / "pi-apps-updater.desktop").read_text() == PI_APPS
    assert (auto / "memoryframe-kiosk.desktop").exists()
    assert not (auto / "memoryframe-old.desktop").exists()
    assert list(auto.glob("memoryframe-old.desktop.disabled-by-memoryframe.*"))
    assert not (auto / "mf-server.desktop").exists()
    assert (home / ".bashrc").read_text() == BASHRC
    labwc = (home / ".config/labwc/autostart").read_text().splitlines()
    assert labwc[0] == "/usr/bin/kanshi &"
    assert labwc[1].endswith("# memoryframe-managed") and not labwc[1].startswith("#")
    assert labwc[2].startswith("# disabled by memoryframe")
    assert list((home / ".config/labwc").glob("autostart.bak-memoryframe.*"))
    cron_lines = cron.read_text().splitlines()
    assert cron_lines[0] == "@reboot /home/memoryframe/pi-apps/updater onboot"
    assert cron_lines[1].startswith("# disabled by memoryframe")
    assert list(home.glob("crontab-memoryframe.bak-memoryframe.*"))   # backup kept in $HOME, not /tmp


def test_clean_system_reports_nothing(fake):
    home, _, cron, env = fake
    for f in ("memoryframe-old.desktop", "mf-server.desktop"):
        (home / ".config/autostart" / f).unlink()
    (home / ".config/labwc/autostart").write_text("/usr/bin/kanshi &\n")
    cron.write_text("@reboot /home/memoryframe/pi-apps/updater onboot\n")
    r = run(env)
    assert found(r.stdout) == []
    assert "No other autostart entries" in r.stdout
