"""#155: kiosk.sh presses labwc's hide-pointer keybind (Alt+Super+H, added to rc.xml by install.sh) with wtype at
launcher start and after every Chromium launch. Best effort: no wtype / no Wayland -> skipped and logged once;
a failing or hanging wtype never delays or breaks the browser launches."""
import shutil
import time

from test_kiosk_script import env, launches, log_text, run_until  # noqa: F401  (fixture + helpers)

ARGS = "-M alt -M logo h -m alt -m logo"


def wtype(tmp_path, body="exit 0"):
    f = tmp_path / "bin" / "wtype"
    f.write_text(f'#!/bin/bash\necho "$*" >> "{tmp_path}/wtype.calls"\n{body}\n')
    f.chmod(0o755)
    (tmp_path / "tools" / "timeout").symlink_to(shutil.which("timeout"))


def calls(tmp_path):
    f = tmp_path / "wtype.calls"
    return f.read_text().splitlines() if f.exists() else []


def test_kiosk_hides_cursor_on_wayland(env):
    tmp_path, _state, _, e = env
    wtype(tmp_path)
    e.update(XDG_SESSION_TYPE="wayland", MEMORYFRAME_HIDE_CURSOR_DELAYS="0 1")
    run_until(e, lambda: len(launches(tmp_path)) >= 2 and len(calls(tmp_path)) >= 5)
    assert set(calls(tmp_path)) == {ARGS}                                          # the keybind, nothing else
    log = log_text(tmp_path)
    assert "hide-cursor: pointer hidden (launcher start, +0s)" in log
    assert "hide-cursor: pointer hidden (after launch, +0s)" in log and "(after launch, +1s)" in log


def test_kiosk_without_wtype_still_launches(env):
    tmp_path, _state, _, e = env
    e.update(XDG_SESSION_TYPE="wayland", MEMORYFRAME_HIDE_CURSOR_DELAYS="0")
    run_until(e, lambda: len(launches(tmp_path)) >= 2)
    log = log_text(tmp_path)
    assert log.count("hide-cursor: skipped (wtype not installed") == 1             # once, not per launch
    assert "pointer hidden" not in log and "wtype failed" not in log


def test_kiosk_not_wayland_never_runs_wtype(env):
    tmp_path, _state, _, e = env
    wtype(tmp_path)
    run_until(e, lambda: len(launches(tmp_path)) >= 2)
    assert calls(tmp_path) == [] and "hide-cursor: skipped (not a Wayland session)" in log_text(tmp_path)


def test_failing_wtype_is_logged_and_kiosk_goes_on(env):
    tmp_path, _state, _, e = env
    wtype(tmp_path, "exit 3")
    e.update(WAYLAND_DISPLAY="wayland-0", MEMORYFRAME_HIDE_CURSOR_DELAYS="0")
    run_until(e, lambda: len(launches(tmp_path)) >= 2 and "wtype failed" in log_text(tmp_path))
    assert "hide-cursor: wtype failed with status 3" in log_text(tmp_path)


def test_hanging_wtype_never_delays_the_launch(env):
    tmp_path, _state, _, e = env
    wtype(tmp_path, "sleep 30")
    e.update(XDG_SESSION_TYPE="wayland", MEMORYFRAME_HIDE_CURSOR_DELAYS="0")
    t0 = time.monotonic()
    run_until(e, lambda: len(launches(tmp_path)) >= 1, timeout=10)
    assert time.monotonic() - t0 < 8                                               # not held by the 30 s wtype
    run_until(e, lambda: "wtype failed with status 124" in log_text(tmp_path), timeout=15)   # capped at 5 s
