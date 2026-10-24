"""deploy/kiosk.sh with stub chromium/curl: flags, profile, logging, restart."""
import json
import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "deploy" / "kiosk.sh"
BASH = shutil.which("bash")
TOOLS = ["bash", "date", "mkdir", "stat", "mv", "flock", "sed", "rm", "grep", "wc",
         "sleep", "printf", "cat", "dirname"]


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
    # Hermetic PATH: only the stubs plus symlinks to the few system tools kiosk.sh
    # needs, so a real chromium/chromium-browser on the host can never be picked up.
    tools = tmp_path / "tools"
    tools.mkdir()
    for name in TOOLS:
        real = shutil.which(name)
        assert real, f"missing system tool {name}"
        (tools / name).symlink_to(real)
    e = {k: v for k, v in os.environ.items() if not k.startswith("MEMORYFRAME_")}
    e.update(PATH=f"{bin_}:{tools}", HOME=str(tmp_path),
             MEMORYFRAME_STATE_DIR=str(state), XDG_RUNTIME_DIR=str(tmp_path),
             MEMORYFRAME_BROWSER=str(bin_ / "chromium"),
             MEMORYFRAME_CHROMIUM_FLAGS="--disable-gpu")
    e.pop("DISPLAY", None)
    e.pop("WAYLAND_DISPLAY", None)
    e.pop("XDG_SESSION_TYPE", None)
    return tmp_path, state, calls, e


def log_text(tmp_path):
    f = tmp_path / ".cache" / "memoryframe-kiosk.log"
    return f.read_text() if f.exists() else ""


def run_until(e, condition, timeout=15):
    """Run kiosk.sh until condition() is true (polling), then stop it.
    No fixed sleeps, so slow CI runners don't cause flaky failures."""
    p = subprocess.Popen([BASH, str(SCRIPT)], env=e)
    try:
        deadline = time.monotonic() + timeout
        while not condition():
            assert p.poll() is None, "kiosk.sh exited early"
            assert time.monotonic() < deadline, "condition not met in time"
            time.sleep(0.05)
    finally:
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
    run_until(e, lambda: len(launches(tmp_path)) >= 2)
    runs = launches(tmp_path)
    assert len(runs) >= 2                          # restarted after exit
    args = runs[0]
    for flag in ["--kiosk", "--password-store=basic", "--no-first-run", "--noerrdialogs",
                 "--disable-infobars", "--disable-session-crashed-bubble",
                 "--autoplay-policy=no-user-gesture-required", "--ozone-platform-hint=auto",
                 f"--user-data-dir={state}/chromium-profile", "--disable-gpu"]:
        assert flag in args, flag
    assert args[-1] == "http://localhost:5000"
    assert not any(a.startswith("--remote-debugging") for a in args)   # debug is opt-in
    p = json.loads((prefs / "Preferences").read_text())["profile"]
    assert p == {"exited_cleanly": True, "exit_type": "Normal"}


def test_log_has_launcher_and_chromium_stderr(env):
    tmp_path, state, _, e = env
    run_until(e, lambda: "browser exited with status 1" in log_text(tmp_path))
    log = log_text(tmp_path)
    assert f"start: browser={tmp_path}/bin/chromium url=http://localhost:5000" in log
    assert "/media has 2 item(s)" in log
    assert "stub chromium stderr line" in log
    assert "browser exited with status 1" in log


def test_single_instance(env):
    tmp_path, state, _, e = env
    p = subprocess.Popen([BASH, str(SCRIPT)], env=e)
    deadline = time.monotonic() + 15
    while "start:" not in log_text(tmp_path):
        assert time.monotonic() < deadline
        time.sleep(0.05)
    r = subprocess.run([BASH, str(SCRIPT)], env=e, timeout=5)
    p.terminate()
    p.wait(timeout=5)
    assert r.returncode == 0
    assert "already running" in log_text(tmp_path)


def test_no_real_browser_reachable(env):
    tmp_path, _, _, e = env
    for name in ("chromium", "chromium-browser", "google-chrome"):
        r = subprocess.run([BASH, "-c", f"command -v {name}"], env=e, capture_output=True, text=True)
        assert r.stdout.strip() in ("", str(tmp_path / "bin" / name)), r.stdout


def test_browser_override_and_detection_use_stub(env):
    tmp_path, state, _, e = env
    run_until(e, lambda: len(launches(tmp_path)) >= 1)
    assert f"browser={tmp_path}/bin/chromium" in log_text(tmp_path)


def test_missing_override_fails_cleanly(env):
    tmp_path, state, _, e = env
    e["MEMORYFRAME_BROWSER"] = "/nonexistent/chromium"
    r = subprocess.run([BASH, str(SCRIPT)], env=e, timeout=5)
    assert r.returncode == 1
    assert "not found" in log_text(tmp_path)


def test_wayland_flags(env):
    tmp_path, _, _, e = env
    e["WAYLAND_DISPLAY"] = "wayland-0"
    run_until(e, lambda: len(launches(tmp_path)) >= 1)
    args = launches(tmp_path)[0]
    assert "--ozone-platform=wayland" in args
    assert "--ozone-platform-hint=auto" not in args


def test_debug_mode_localhost_only(env):
    tmp_path, _, _, e = env
    e["MEMORYFRAME_KIOSK_DEBUG"] = "1"
    run_until(e, lambda: len(launches(tmp_path)) >= 1)
    args = launches(tmp_path)[0]
    for flag in ["--remote-debugging-port=9222", "--remote-debugging-address=127.0.0.1",
                 "--enable-logging=stderr", "--v=0"]:
        assert flag in args, flag
    assert "--ozone-platform-hint=auto" in args       # no WAYLAND_DISPLAY in this test


def test_log_rotation(env):
    tmp_path, _, _, e = env
    log = tmp_path / ".cache" / "memoryframe-kiosk.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text("x" * 1100000)
    run_until(e, lambda: len(launches(tmp_path)) >= 1)
    assert (tmp_path / ".cache" / "memoryframe-kiosk.log.1").stat().st_size == 1100000
    assert log.stat().st_size < 100000


def test_autodetect_prefers_chromium_browser_when_override_unset(env):
    tmp_path, _, _, e = env
    del e["MEMORYFRAME_BROWSER"]
    stub = tmp_path / "bin" / "chromium"
    (tmp_path / "bin" / "chromium-browser").write_text(stub.read_text())
    (tmp_path / "bin" / "chromium-browser").chmod(0o755)
    run_until(e, lambda: len(launches(tmp_path)) >= 1)
    assert "start: browser=chromium-browser " in log_text(tmp_path)


def test_autodetect_falls_back_to_chromium(env):
    tmp_path, _, _, e = env
    del e["MEMORYFRAME_BROWSER"]
    run_until(e, lambda: len(launches(tmp_path)) >= 1)
    assert "start: browser=chromium " in log_text(tmp_path)


def test_wayland_detected_from_session_type(env):
    """labwc on Pi OS: XDG_SESSION_TYPE=wayland but no WAYLAND_DISPLAY."""
    tmp_path, _, _, e = env
    e["XDG_SESSION_TYPE"] = "wayland"
    run_until(e, lambda: len(launches(tmp_path)) >= 1)
    args = launches(tmp_path)[0]
    assert "--ozone-platform=wayland" in args
    assert "--ozone-platform-hint=auto" not in args


def test_log_records_full_launch_command(env):
    tmp_path, _, _, e = env
    run_until(e, lambda: "launch:" in log_text(tmp_path))
    line = next(l for l in log_text(tmp_path).splitlines() if "launch:" in l)
    for flag in ("--password-store=basic", "--no-first-run", "--ozone-platform-hint=auto",
                 "--disable-gpu", "http://localhost:5000"):
        assert flag in line, flag


# ---------------- #95: liveness kill + backoff ----------------

def _liveness_env(env, body):
    tmp_path, state, calls, e = env
    bin_ = tmp_path / "bin"
    (bin_ / "chromium").write_text(f'#!/bin/bash\nprintf "%s\\n" "$@" > "{calls}.$(date +%s%N)"\nexec sleep 30\n')
    (bin_ / "curl").write_text(
        "#!/bin/bash\ncase \"$*\" in *liveness*) echo '" + body + "';; *healthz*) echo '{\"status\":\"ok\"}';; "
        "*/media*) echo '[]';; esac\n")
    e.update(MEMORYFRAME_LIVENESS_EVERY="0.1", MEMORYFRAME_KIOSK_BACKOFF="0")
    return tmp_path, e


def test_liveness_kills_frozen_browser_then_rate_limits(env):
    tmp_path, e = _liveness_env(env, '{"kiosk_age_s": 200.5, "shutting_down": false}')
    run_until(e, lambda: "not killing" in log_text(tmp_path), timeout=20)
    t = log_text(tmp_path)
    assert t.count("liveness: no heartbeat for 200 s; killing") == 3
    assert len(launches(tmp_path)) == 4


@pytest.mark.parametrize("body", ['{"kiosk_age_s": 3.0, "shutting_down": false}',
                                  '{"kiosk_age_s": 500, "shutting_down": true}'])
def test_liveness_no_kill_when_fresh_or_shutting_down(env, body):
    tmp_path, e = _liveness_env(env, body)
    run_until(e, lambda: "liveness" in log_text(tmp_path) or (len(launches(tmp_path)) >= 1 and time.sleep(1) is None))
    assert "killing" not in log_text(tmp_path) and len(launches(tmp_path)) == 1


def test_crash_backoff_doubles():
    s = SCRIPT.read_text()
    assert 'delay=$((delay * 2))' in s and "BACKOFF_MAX=60" in s and '-gt 60 ] && delay="$BACKOFF_BASE"' in s


def test_kills_file_never_in_tmp():
    """Sage (#111): the kill-rate file lives in $XDG_RUNTIME_DIR or the kiosk state dir, never /tmp."""
    s = SCRIPT.read_text()
    assert 'KILLS_FILE="${XDG_RUNTIME_DIR:-$STATE_DIR}/memoryframe-kiosk.kills"' in s
    assert "/tmp" not in [l for l in s.splitlines() if l.startswith("KILLS_FILE=")][0]
