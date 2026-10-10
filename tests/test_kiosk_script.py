"""deploy/kiosk.sh with stub chromium/curl: flags, profile, logging, restart."""
import json
import os
import subprocess
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "deploy" / "kiosk.sh"


@pytest.fixture
def env(tmp_path):
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    calls = tmp_path / "calls"
    # chromium stub: record args, write to stderr, exit (so the launcher restarts it)
    (bin_ / "chromium").write_text(
        f'#!/bin/bash\nprintf "%s\\n" "$@" > "{calls}.$(date +%s%N)"\n'
        'echo "stub chromium stderr line" >&2\nsleep 0.3\nexit 1\n')
    # curl stub: healthy server with 2 media items
    (bin_ / "curl").write_text(
        '#!/bin/bash\ncase "$*" in *healthz*) echo \'{"status":"ok"}\';; '
        '*/media*) echo \'[{"filename":"a.jpg","type":"image"},{"filename":"b.mp4","type":"video"}]\';; esac\n')
    for f in bin_.iterdir():
        f.chmod(0o755)
    state = tmp_path / "state"
    e = dict(os.environ, PATH=f"{bin_}:/usr/bin:/bin", HOME=str(tmp_path),
             MEMORYFRAME_STATE_DIR=str(state), XDG_RUNTIME_DIR=str(tmp_path),
             MEMORYFRAME_CHROMIUM_FLAGS="--ozone-platform=wayland")
    e.pop("DISPLAY", None)
    return tmp_path, state, calls, e


def run_for(e, seconds):
    p = subprocess.Popen(["bash", str(SCRIPT)], env=e)
    time.sleep(seconds)
    p.terminate()
    p.wait(timeout=5)


def launches(tmp_path):
    return [f.read_text().splitlines() for f in sorted(tmp_path.glob("calls.*"))]


def test_flags_profile_and_restart(env):
    tmp_path, state, _, e = env
    # Simulate a crashed previous run.
    prefs = state / "chromium-profile" / "Default"
    prefs.mkdir(parents=True)
    (prefs / "Preferences").write_text(json.dumps(
        {"profile": {"exited_cleanly": False, "exit_type": "Crashed"}}, separators=(",", ":")))
    run_for(e, 3.5)
    runs = launches(tmp_path)
    assert len(runs) >= 2                          # restarted after exit
    args = runs[0]
    for flag in ["--kiosk", "--password-store=basic", "--no-first-run", "--noerrdialogs",
                 "--disable-infobars", "--disable-session-crashed-bubble",
                 "--autoplay-policy=no-user-gesture-required", "--ozone-platform-hint=auto",
                 f"--user-data-dir={state}/chromium-profile", "--ozone-platform=wayland"]:
        assert flag in args, flag
    assert args[-1] == "http://localhost:5000"
    p = json.loads((prefs / "Preferences").read_text())["profile"]
    assert p == {"exited_cleanly": True, "exit_type": "Normal"}


def test_log_has_launcher_and_chromium_stderr(env):
    _, state, _, e = env
    run_for(e, 1.5)
    log = (state / "kiosk.log").read_text()
    assert "start: browser=chromium url=http://localhost:5000" in log
    assert "/media has 2 item(s)" in log
    assert "stub chromium stderr line" in log
    assert "browser exited with status 1" in log


def test_single_instance(env):
    tmp_path, state, _, e = env
    p = subprocess.Popen(["bash", str(SCRIPT)], env=e)
    time.sleep(0.5)
    r = subprocess.run(["bash", str(SCRIPT)], env=e, timeout=5)
    p.terminate()
    p.wait(timeout=5)
    assert r.returncode == 0
    assert "already running" in (state / "kiosk.log").read_text()
