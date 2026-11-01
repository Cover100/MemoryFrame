"""#97: install.sh restarts the kiosk LAUNCHER via deploy/kiosk-restart.sh: the old kiosk.sh loop and its Chromium
stop, a new launcher starts with the desktop session's environment, and the browser comes back on the tmpfs."""
import os
import shutil
import signal
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


# ---- #97 (Hale, Pi): a Chromium helper that keeps the launcher's lock fd must not leave the frame blank ----

LEAKY = r'''#!/usr/bin/env bash
# fake kiosk.sh: same single-instance lock as the real one, but its "browser" LEAKS fd 9 into a helper
exec 9>"$XDG_RUNTIME_DIR/kiosk.lock"
if ! flock -n 9; then echo "already-running $$" >> "@T@/starts"; exit 0; fi
echo "started $$" >> "@T@/starts"
if [ ! -e "@T@/nobrowser" ]; then
  "@PY@" -c '
import subprocess, time
# helper (zygote-like): no --user-data-dir, keeps fd 9, double-forked so it is re-parented at once
subprocess.Popen(["bash", "-c", "sleep @SLEEP@ & disown"], pass_fds=(9,), start_new_session=True)
time.sleep(300)' --user-data-dir=@TMPFS@/chromium-profile &
fi
while true; do sleep 0.2; done
'''


@pytest.fixture
def leaky(tmp_path):
    tmpfs = tmp_path / "run-mfkiosk"; tmpfs.mkdir(mode=0o700)
    rt = tmp_path / "rt"; rt.mkdir(mode=0o700)
    sleep_s = f"{3000 + os.getpid() % 997}.{abs(hash(str(tmp_path))) % 9973}"
    k = tmp_path / "deploy" / "kiosk.sh"; k.parent.mkdir()
    k.write_text(LEAKY.replace("@T@", str(tmp_path)).replace("@PY@", sys.executable)
                 .replace("@TMPFS@", str(tmpfs)).replace("@SLEEP@", sleep_s))
    k.chmod(0o755)
    sess = {"PATH": os.environ["PATH"], "HOME": str(tmp_path), "XDG_RUNTIME_DIR": str(rt),
            "WAYLAND_DISPLAY": "wayland-9"}
    old = subprocess.Popen(["bash", str(k)], env=sess, start_new_session=True)
    yield tmp_path, tmpfs, k, old, sleep_s
    if old.poll() is None:
        old.kill()
    for pat in [str(k), f"sleep {sleep_s}", f"--user-data-dir={tmpfs}/chromium-profile"]:
        subprocess.run(["pkill", "-f", "--", pat])


def _wait_for(pred, t=10):
    end = time.monotonic() + t
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.05)
    return False


def _helper_up(sleep_s):
    return subprocess.run(["pgrep", "-f", f"^sleep {sleep_s}$"], capture_output=True).returncode == 0


def test_helper_holding_the_lock_is_stopped_and_new_launcher_comes_up(leaky):
    tmp_path, tmpfs, k, old, sleep_s = leaky
    assert _wait_for(lambda: _helper_up(sleep_s))                    # the leaked lock holder exists
    r = run_restart(tmp_path, tmpfs, k)
    assert r.returncode == 0, r.stdout + r.stderr
    old.wait(timeout=10)
    starts = (tmp_path / "starts").read_text().split()
    assert "already-running" not in starts, starts                   # Hale's Pi bug: new launcher refused
    assert starts.count("started") == 2
    assert f"browser running with --user-data-dir={tmpfs}/chromium-profile" in r.stdout


def test_browser_not_up_after_restart_is_an_error(leaky):
    tmp_path, tmpfs, k, old, sleep_s = leaky
    assert _wait_for(lambda: _helper_up(sleep_s))
    (tmp_path / "nobrowser").write_text("")                           # the new launcher starts no browser
    env_wait = {"MEMORYFRAME_KIOSK_RESTART_WAIT": "1"}
    r = subprocess.run([BASH, str(RESTART), str(k)], capture_output=True, text=True, timeout=60,
                       env={"PATH": os.environ["PATH"], "HOME": str(tmp_path),
                            "MEMORYFRAME_KIOSK_TMPFS": str(tmpfs), **env_wait})
    assert r.returncode != 0 and "ERROR: kiosk browser not running" in r.stderr


def test_kiosk_never_gives_the_lock_fd_to_its_children():
    s = (ROOT / "deploy" / "kiosk.sh").read_text()
    assert '"$BROWSER" "$@" 9>&- &' in s and 'watch_liveness "$bpid" 9>&- &' in s


# A process whose comm is set to an awkward name, blocked (state S) on a pipe read until teardown. Copying
# `sleep` under that name does not work everywhere: on Ubuntu 26.04 (CI) `sleep` is the multicall uutils
# binary, which dispatches on argv[0], fails on an unknown name and exits at once (seen as a zombie).
COMM_CHILD = (
    "import sys\n"
    "open('/proc/self/comm', 'w').write(sys.argv[1])\n"
    "sys.stdout.write('ready\\n'); sys.stdout.flush()\n"
    "sys.stdin.read()\n"
)


def start_comm_child(comm, start=None):
    argv = [sys.executable, "-c", COMM_CHILD, comm]
    kw = dict(stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    p = start(argv, **kw) if start else subprocess.Popen(argv, start_new_session=True, **kw)
    assert p.stdout.readline() == "ready\n"
    stat = Path(f"/proc/{p.pid}/stat").read_text()
    assert f"({comm})" in stat, stat
    return p


# ---- #119 follow-up (Sage nits): /proc stat parsing and a literal lock-path match ----

def lib(script, tmp_path):
    """Run bash with deploy/kiosk-restart.sh's helpers sourced (KIOSK_RESTART_LIB=1: restarts nothing)."""
    env = {"PATH": os.environ["PATH"], "HOME": str(tmp_path), "KIOSK_RESTART_LIB": "1"}
    return subprocess.run([BASH, "-c", f'set -- /nonexistent/kiosk.sh; source "{RESTART}"; {script}'],
                          env=env, capture_output=True, text=True, timeout=30)


@pytest.fixture
def spawn():
    procs = []

    def start(argv, **kw):
        p = subprocess.Popen(argv, start_new_session=True, **kw)
        procs.append(p)
        return p
    yield start
    for p in procs:
        try:
            os.killpg(p.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        p.wait(timeout=5)


def test_state_parsed_after_the_last_paren_when_comm_has_space_and_paren(tmp_path, spawn):
    p = start_comm_child("x) T (y z", spawn)                      # comm "x) T (y z": a naive split sees "T"
    r = lib(f"proc_state {p.pid}; alive {p.pid}", tmp_path)
    assert r.stdout.split() == ["S", str(p.pid)], r.stdout + r.stderr
    os.kill(p.pid, signal.SIGSTOP)
    assert _wait_for(lambda: lib(f"proc_state {p.pid}", tmp_path).stdout.strip() == "T")
    os.kill(p.pid, signal.SIGKILL)
    assert _wait_for(lambda: lib(f"proc_state {p.pid}", tmp_path).stdout.strip() == "Z")   # unreaped child
    assert lib(f"alive {p.pid}", tmp_path).stdout.strip() == ""


def test_lock_holders_match_the_lock_path_literally(tmp_path, spawn):
    lock = tmp_path / "lk[1]*.lock"
    other = tmp_path / "lk1-x.lock"                               # the glob lk[1]*.lock would match this one
    holders = {}
    for f in (lock, other):
        f.write_text("")
        holders[f] = spawn([BASH, "-c", 'exec 9>"$0"; exec sleep 300', str(f)])
    assert _wait_for(lambda: all(os.path.exists(f"/proc/{p.pid}/fd/9") and
                                 os.readlink(f"/proc/{p.pid}/fd/9") == str(f) for f, p in holders.items()))
    r = lib(f'uid="$(id -u)"; LOCK="{lock}"; lock_holders', tmp_path)
    assert r.stdout.split() == [str(holders[lock].pid)], r.stdout + r.stderr
    r = lib(f'uid="$(id -u)"; LOCK="{tmp_path}/lk?-x.lock"; lock_holders', tmp_path)
    assert r.stdout.strip() == ""                                 # a glob-looking path matches nothing else


def test_sourcing_as_a_library_restarts_nothing(tmp_path):
    r = lib("echo sourced", tmp_path)
    assert r.returncode == 0 and r.stdout.strip() == "sourced" and "kiosk:" not in r.stdout
