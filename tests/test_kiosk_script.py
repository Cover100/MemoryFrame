"""deploy/kiosk.sh with stub chromium/curl: flags, profile, logging, restart."""
import json
import os
import re
import signal
import shutil
import subprocess
import sys
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


# kiosk.sh forks a liveness subshell and Chromium; terminating only the launcher left them running ~20 s
# after each test. Every launcher here runs in its own session, and the whole session is killed and waited
# for at teardown, with a check that nothing from it is left.
_SESSIONS = []


def start_kiosk(e):
    p = subprocess.Popen([BASH, str(SCRIPT)], env=e, start_new_session=True)
    _SESSIONS.append(p.pid)
    return p


def session_pids(sid):
    out = []
    for d in Path("/proc").iterdir():
        if d.name.isdigit():
            try:
                st = (d / "stat").read_text()
            except OSError:
                continue
            f = st.rsplit(")", 1)[1].split()
            if int(f[3]) == sid and f[0] != "Z":
                out.append(int(d.name))
    return out


def kill_session(sid):
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(sid, sig)
        except ProcessLookupError:
            pass
        deadline = time.monotonic() + 5
        while session_pids(sid) and time.monotonic() < deadline:
            time.sleep(0.02)
        if not session_pids(sid):
            break


@pytest.fixture(autouse=True)
def _no_leftover_kiosk_processes():
    yield
    left = {}
    while _SESSIONS:
        sid = _SESSIONS.pop()
        kill_session(sid)
        if session_pids(sid):
            left[sid] = session_pids(sid)
    assert not left, f"kiosk.sh processes left behind: {left}"


def log_text(tmp_path):
    f = tmp_path / ".cache" / "memoryframe-kiosk.log"
    return f.read_text() if f.exists() else ""


def run_until(e, condition, timeout=15):
    """Run kiosk.sh until condition() is true (polling), then stop it.
    No fixed sleeps, so slow CI runners don't cause flaky failures."""
    p = start_kiosk(e)
    try:
        deadline = time.monotonic() + timeout
        while not condition():
            assert p.poll() is None, "kiosk.sh exited early"
            assert time.monotonic() < deadline, "condition not met in time"
            time.sleep(0.05)
    finally:
        kill_session(p.pid)
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
    p = start_kiosk(e)
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


def test_log_rotated_inside_a_crash_loop(env):
    """#96: rotation is checked before every launch, so a crash loop can't grow the log without bound."""
    tmp_path, _, _, e = env
    noisy = tmp_path / "bin" / "chromium"
    noisy.write_text(f'#!/bin/bash\nprintf "%s\\n" "$@" > "{tmp_path}/calls.$(date +%s%N)"\n'
                     'printf "%*s\\n" 400000 "" >&2\nexit 1\n')
    log = tmp_path / ".cache" / "memoryframe-kiosk.log"
    old = tmp_path / ".cache" / "memoryframe-kiosk.log.1"
    e["MEMORYFRAME_KIOSK_BACKOFF"] = "0"                           # #95 crash backoff would make 7 launches slow
    # Stop only once the current log exists: the launcher can be killed between rotate_log's `mv` and the
    # `exec >>"$LOG"` that recreates it, which made log.stat() raise FileNotFoundError now and then.
    run_until(e, lambda: len(launches(tmp_path)) >= 7 and log.exists(), timeout=60)
    assert old.exists()
    sizes = {p.name: p.stat().st_size for p in log.parent.glob("memoryframe-kiosk.log*")}
    assert set(sizes) <= {"memoryframe-kiosk.log", "memoryframe-kiosk.log.1"} and "memoryframe-kiosk.log.1" in sizes
    assert all(v <= 1048576 + 500000 for v in sizes.values()), sizes


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


# ---------------- #113: factory-reset guard ----------------

def test_no_launch_or_app_poll_while_factory_reset_marker_exists(env):
    tmp_path, _, _, e = env
    data = tmp_path / "data"
    data.mkdir()
    (data / ".factory-reset").write_text("erase 1\n")
    polls = tmp_path / "polls"
    (tmp_path / "bin" / "curl").write_text(f'#!/bin/bash\necho "$*" >> "{polls}"\necho \'{{"status":"ok"}}\'\n')
    e["MEMORYFRAME_DATA_DIR"] = str(data)
    run_until(e, lambda: "factory reset in progress" in log_text(tmp_path))
    time.sleep(1.5)
    assert not launches(tmp_path) and not polls.exists()
    (data / ".factory-reset").unlink()                         # reset done: the normal launch resumes
    run_until(e, lambda: len(launches(tmp_path)) >= 1)


def test_liveness_never_kills_during_factory_reset(env):
    tmp_path, e = _liveness_env(env, '{"kiosk_age_s": 200.5, "shutting_down": false}')
    data = tmp_path / "data"
    data.mkdir()
    e["MEMORYFRAME_DATA_DIR"] = str(data)
    p = start_kiosk(e)
    try:
        deadline = time.monotonic() + 15
        while not launches(tmp_path):
            assert time.monotonic() < deadline
            time.sleep(0.02)
        (data / ".factory-reset").write_text("erase 1\n")      # reset starts while Chromium runs
        time.sleep(1.5)
        assert "killing" not in log_text(tmp_path)
    finally:
        p.terminate()
        p.wait(timeout=5)


def test_lock_file_never_in_tmp():
    s = SCRIPT.read_text()
    assert 'LOCK="${XDG_RUNTIME_DIR:-$STATE_DIR}/memoryframe-kiosk.lock"' in s
    assert not [l for l in s.splitlines() if "/tmp" in l and not l.lstrip().startswith("#")]


def test_service_never_starts_during_factory_reset():
    """#113: ExecCondition gates every start (Restart=, watchdog restart, manual); marker path must match."""
    import re
    unit = (ROOT / "deploy" / "memoryframe.service").read_text()
    reset = (ROOT / "deploy" / "factory-reset.sh").read_text()
    cond = ("ExecCondition=/usr/bin/test ! -e /var/lib/memoryframe/.factory-reset"
            " -a ! -L /var/lib/memoryframe/.factory-reset")
    assert cond in unit
    assert re.search(r'^DATA="\$\{MEMORYFRAME_DATA_DIR:-/var/lib/memoryframe\}"', reset, re.M)
    assert 'MARKER="$DATA/.factory-reset"' in reset
    for code, kind in ((0, None), (1, "file"), (1, "dangling")):   # the condition command itself
        m = Path(subprocess.run(["mktemp", "-d"], capture_output=True, text=True).stdout.strip())
        p = m / ".factory-reset"
        if kind == "file":
            p.write_text("x")
        elif kind == "dangling":
            p.symlink_to(m / "nowhere")
        argv = cond.split("=", 1)[1].replace("/var/lib/memoryframe/.factory-reset", str(p)).split()
        assert subprocess.run(argv).returncode == code
        shutil.rmtree(m)


# ---------------- #122: main browser stopped (state T) for over STOPPED_MAX s in a row ----------------

def _browser_pid(sid):
    for pid in session_pids(sid):
        try:
            if Path(f"/proc/{pid}/cmdline").read_bytes().startswith(b"sleep\x0030"):
                return pid
        except OSError:
            pass
    return None


def _stopped_env(env, max_s):
    tmp_path, e = _liveness_env(env, '{"kiosk_age_s": 3.0, "shutting_down": false}')   # heartbeat is fresh
    e["MEMORYFRAME_STOPPED_MAX"] = str(max_s)
    return tmp_path, e


def _first_browser(p, tmp_path):
    deadline = time.monotonic() + 15
    while True:
        assert p.poll() is None and time.monotonic() < deadline
        pid = _browser_pid(p.pid)
        if pid and launches(tmp_path):
            return pid
        time.sleep(0.05)


def test_stopped_browser_is_killed_and_relaunched(env):
    tmp_path, e = _stopped_env(env, 1)
    p = start_kiosk(e)
    pid = _first_browser(p, tmp_path)
    os.kill(pid, signal.SIGSTOP)
    deadline = time.monotonic() + 10                               # > 1 s in a row, 1 s date granularity
    while len(launches(tmp_path)) < 2:
        assert time.monotonic() < deadline, log_text(tmp_path)
        time.sleep(0.05)
    t = log_text(tmp_path)
    assert f"liveness: browser stopped (state T) for 1 s or more; killing browser {pid}" in t
    assert "no heartbeat" not in t
    assert not Path(f"/proc/{pid}").exists() or Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0] == "Z"


def test_brief_stop_then_cont_is_not_relaunched(env):
    tmp_path, e = _stopped_env(env, 3)
    p = start_kiosk(e)
    pid = _first_browser(p, tmp_path)
    os.kill(pid, signal.SIGSTOP)
    time.sleep(1.5)                                                # under the 3 s threshold
    os.kill(pid, signal.SIGCONT)
    time.sleep(1)
    os.kill(pid, signal.SIGSTOP)                                   # a second short stop: the timer restarted
    time.sleep(1.5)
    os.kill(pid, signal.SIGCONT)
    time.sleep(3)
    assert len(launches(tmp_path)) == 1 and "killing" not in log_text(tmp_path)
    assert _browser_pid(p.pid) == pid


def test_stopped_kill_shares_the_rate_limit(env):
    tmp_path, e = _stopped_env(env, 0)
    kills = tmp_path / "memoryframe-kiosk.kills"
    now = int(time.time())
    kills.write_text(f"{now}\n{now}\n{now}\n")                     # 3 kills in the last 10 min already
    p = start_kiosk(e)
    pid = _first_browser(p, tmp_path)
    os.kill(pid, signal.SIGSTOP)
    deadline = time.monotonic() + 10
    while "not killing" not in log_text(tmp_path):
        assert time.monotonic() < deadline, log_text(tmp_path)
        time.sleep(0.05)
    assert len(launches(tmp_path)) == 1
    os.kill(pid, signal.SIGCONT)


# A process with the given comm, blocked (state S) on a pipe read. Copying `sleep` under that name does not
# work everywhere: on Ubuntu 26.04 (CI) `sleep` is the multicall uutils binary, which dispatches on argv[0]
# and exits at once on an unknown name.
COMM_CHILD = (
    "import sys\n"
    "open('/proc/self/comm', 'w').write(sys.argv[1])\n"
    "sys.stdout.write('ready\\n'); sys.stdout.flush()\n"
    "sys.stdin.read()\n"
)


@pytest.mark.parametrize("comm,state", [("a b", "S"), ("x) T (y", "S"), ("plain", "S")])
def test_proc_state_parses_after_the_last_paren(comm, state):
    p = subprocess.Popen([sys.executable, "-c", COMM_CHILD, comm], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                         text=True, start_new_session=True)
    try:
        assert p.stdout.readline() == "ready\n"
        assert f"({comm})" in Path(f"/proc/{p.pid}/stat").read_text()
        fn = re.search(r"^proc_state\(\) \{.*?^\}", SCRIPT.read_text(), re.S | re.M).group(0)
        got = lambda: subprocess.run([BASH, "-c", f"{fn}\nproc_state {p.pid}"], capture_output=True,
                                     text=True).stdout.strip()
        assert got() == state
        os.kill(p.pid, signal.SIGSTOP)
        deadline = time.monotonic() + 5
        while got() != "T":
            assert time.monotonic() < deadline
            time.sleep(0.02)
    finally:
        os.killpg(p.pid, signal.SIGKILL)
        p.wait()


def test_stopped_relaunch_timing_is_pinned(env):
    """Sage (#124): relaunch no earlier than STOPPED_MAX after the stop and no later than STOPPED_MAX + one
    poll (+ margin for the relaunch itself and a slow runner). Scaled down: poll 1 s, max 3 s."""
    tmp_path, e = _stopped_env(env, 3)
    e["MEMORYFRAME_LIVENESS_EVERY"] = "1"
    p = start_kiosk(e)
    pid = _first_browser(p, tmp_path)
    time.sleep(0.3)                                                # stop at an arbitrary phase of the poll
    stopped_ns = time.time_ns()
    os.kill(pid, signal.SIGSTOP)
    deadline = time.monotonic() + 15
    while len(launches(tmp_path)) < 2:
        assert time.monotonic() < deadline, log_text(tmp_path)
        time.sleep(0.02)
    relaunch_ns = int(sorted(tmp_path.glob("calls.*"))[1].name.split(".", 1)[1])   # stub writes date +%s%N
    took = (relaunch_ns - stopped_ns) / 1e9
    assert 3.0 <= took <= 3.0 + 1.0 + 1.5, took
