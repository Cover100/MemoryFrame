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
