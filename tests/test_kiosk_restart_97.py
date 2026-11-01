"""#97: install.sh restarts the kiosk LAUNCHER via deploy/kiosk-restart.sh: the old kiosk.sh loop and its Chromium
stop, a new launcher starts with the desktop session's environment, and the browser comes back on the tmpfs."""
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
RESTART = ROOT / "deploy" / "kiosk-restart.sh"
BASH = shutil.which("bash")


def alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    try:                                   # a zombie child of ours counts as gone
        return Path(f"/proc/{pid}/stat").read_text().split()[2] != "Z"
    except FileNotFoundError:
        return False


@pytest.fixture
def box(tmp_path):
    tmpfs = tmp_path / "run-mfkiosk"
    tmpfs.mkdir(mode=0o700)
    starts = tmp_path / "starts"
    k = tmp_path / "deploy" / "kiosk.sh"
    k.parent.mkdir()
    # fake launcher: records its env, starts a fake "chromium" on the tmpfs profile, then loops like kiosk.sh
    k.write_text(f'#!/usr/bin/env bash\necho "$$ ${{WAYLAND_DISPLAY:-none}} ${{XDG_RUNTIME_DIR:-none}}" >> "{starts}"\n'
                 f'"{sys.executable}" -c "import time; time.sleep(60)" --user-data-dir={tmpfs}/chromium-profile &\n'
                 'while true; do sleep 0.2; done\n')
    k.chmod(0o755)
    procs = []
    yield tmp_path, tmpfs, starts, k, procs
    for p in procs:
        if p.poll() is None:
            p.kill()
    subprocess.run(["pkill", "-f", "--", f"--user-data-dir={tmpfs}/chromium-profile"])
    subprocess.run(["pkill", "-f", "--", str(k)])


def run_restart(tmp_path, tmpfs, k):
    env = {"PATH": os.environ["PATH"], "HOME": str(tmp_path), "MEMORYFRAME_KIOSK_TMPFS": str(tmpfs),
           "MEMORYFRAME_KIOSK_RESTART_WAIT": "10"}
    return subprocess.run([BASH, str(RESTART), str(k)], env=env, capture_output=True, text=True, timeout=60)


def test_restarts_launcher_with_session_env_and_tmpfs_browser(box):
    tmp_path, tmpfs, starts, k, procs = box
    old_profile = tmp_path / ".cache" / "memoryframe" / "chromium-profile"
    sess = {"PATH": os.environ["PATH"], "HOME": str(tmp_path), "WAYLAND_DISPLAY": "wayland-7",
            "XDG_RUNTIME_DIR": str(tmp_path)}
    old = subprocess.Popen(["bash", str(k)], env=sess)
    old_browser = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)",
                                    f"--user-data-dir={old_profile}"])
    procs += [old, old_browser]
    deadline = time.monotonic() + 10
    while not (starts.exists() and starts.read_text().strip()):
        assert time.monotonic() < deadline
        time.sleep(0.05)
    r = run_restart(tmp_path, tmpfs, k)
    assert r.returncode == 0, r.stderr
    assert "launcher restarted" in r.stdout
    assert f"browser running with --user-data-dir={tmpfs}/chromium-profile" in r.stdout
    old.wait(timeout=10)
    old_browser.wait(timeout=10)                                   # old loop and old-profile Chromium gone
    lines = starts.read_text().splitlines()
    assert len(lines) == 2
    new_pid, wl, rt = lines[1].split()
    assert int(new_pid) != old.pid and alive(int(new_pid))
    assert (wl, rt) == ("wayland-7", str(tmp_path))                # the desktop session's env, not ours
    ps = subprocess.run(["pgrep", "-af", "--", f"--user-data-dir={tmpfs}/chromium-profile"], capture_output=True, text=True)
    assert ps.stdout.strip()


def test_no_launcher_running_starts_nothing(box):
    tmp_path, tmpfs, starts, k, _ = box
    r = run_restart(tmp_path, tmpfs, k)
    assert r.returncode == 0 and "no launcher running" in r.stdout and not starts.exists()


def test_restart_script_shape():
    s = RESTART.read_text()
    assert 'env -i "${vars[@]}" setsid -f "$K"' in s and "/proc/$first/environ" in s
    assert "WAYLAND_DISPLAY" in s and "DBUS_SESSION_BUS_ADDRESS" in s
