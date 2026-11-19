"""#162 S4: kiosk.sh launches Chromium at once on the local splash page (no /healthz wait), `next` carries
MEMORYFRAME_URL, the factory-reset marker still delays the launch, MEMORYFRAME_SPLASH=0 (or no splash.html) keeps the
old flow, liveness never kills during a long splash, /proc/uptime is logged. Stub browser/curl/systemctl only."""
import time
from pathlib import Path

import pytest

from test_kiosk_script import env, launches, log_text, run_until, start_kiosk, kill_session  # noqa: F401

ROOT = Path(__file__).resolve().parents[1]
BOOT = ROOT / "static" / "boot"
SPLASH_URL = f"file://{BOOT}/splash.html?next=http://localhost:5000"


def stubs(tmp_path, e, *, browser="alive", up=False, liveness='{"kiosk_age_s": null, "shutting_down": false}',
          systemctl="ActiveState=active\nNRestarts=0"):
    """Switchable stubs: the app is up while tmp/up exists; /frame/liveness answers tmp/liveness; systemctl show
    answers tmp/systemctl. browser: 'alive' (records args, then stays up) or 'crash' (records args, exits 1)."""
    b = tmp_path / "bin"
    calls = tmp_path / "calls"
    tail = "exec sleep 60\n" if browser == "alive" else "sleep 0.05\nexit 1\n"
    (b / "chromium").write_text(f'#!/bin/bash\nprintf "%s\\n" "$@" > "{calls}.$(date +%s%N)"\n{tail}')
    (b / "curl").write_text(
        "#!/bin/bash\n"
        f'case "$*" in *liveness*) [ -e "{tmp_path}/up" ] || exit 7; cat "{tmp_path}/liveness";;\n'
        f'  *healthz*) [ -e "{tmp_path}/up" ] || exit 7; echo \'{{"status":"ok"}}\';;\n'
        f'  */media*) [ -e "{tmp_path}/up" ] || exit 7; echo \'[]\';; esac\n')
    (b / "systemctl").write_text(f'#!/bin/bash\necho "systemctl $*" >> "{tmp_path}/systemctl.calls"\n'
                                 f'cat "{tmp_path}/systemctl"\n')
    for f in b.iterdir():
        f.chmod(0o755)
    (tmp_path / "liveness").write_text(liveness)
    (tmp_path / "systemctl").write_text(systemctl + "\n")
    if up:
        (tmp_path / "up").write_text("")
    (tmp_path / "uptime").write_text("25.50 40.00\n")
    for t in ("sleep", "date"):
        assert (tmp_path / "tools" / t).exists()
    e.update(MEMORYFRAME_SPLASH="1", MEMORYFRAME_UPTIME_FILE=str(tmp_path / "uptime"),
             MEMORYFRAME_BOOT_POLL="0.2", MEMORYFRAME_E1_AFTER="100", MEMORYFRAME_E2_AFTER="100")
    return e


def urls(tmp_path):
    return [r[-1] for r in launches(tmp_path)]


def test_splash_launch_does_not_wait_for_healthz(env):
    tmp_path, _s, _c, e = env
    e = stubs(tmp_path, e, up=False)                                               # the app is not answering
    t0 = time.monotonic()
    run_until(e, lambda: len(launches(tmp_path)) >= 1, timeout=10)
    assert time.monotonic() - t0 < 5
    assert urls(tmp_path) == [SPLASH_URL]
    log = log_text(tmp_path)
    assert "waiting for http://localhost:5000/healthz" not in log
    assert "uptime=25.50" in log and "launch uptime=25.50" in log


def test_same_flags_as_before_only_the_url_differs(env):
    tmp_path, _s, _c, e = env
    run_until(dict(e), lambda: len(launches(tmp_path)) >= 1)                       # pre-#162 flow (SPLASH=0)
    old = launches(tmp_path)[0]
    for f in tmp_path.glob("calls.*"):
        f.unlink()
    e = stubs(tmp_path, e)
    run_until(e, lambda: len(launches(tmp_path)) >= 1)
    new = launches(tmp_path)[0]
    assert old[:-1] == new[:-1] and old[-1] == "http://localhost:5000" and new[-1] == SPLASH_URL


def test_next_carries_memoryframe_url(env):
    tmp_path, _s, _c, e = env
    e = stubs(tmp_path, e)
    e["MEMORYFRAME_URL"] = "http://127.0.0.1:5005"
    run_until(e, lambda: len(launches(tmp_path)) >= 1)
    assert urls(tmp_path) == [f"file://{BOOT}/splash.html?next=http://127.0.0.1:5005"]


def test_factory_reset_marker_still_delays_the_launch(env):
    tmp_path, _s, _c, e = env
    e = stubs(tmp_path, e, up=True)
    data = tmp_path / "data"
    data.mkdir()
    (data / ".factory-reset").write_text("")
    e["MEMORYFRAME_DATA_DIR"] = str(data)
    p = start_kiosk(e)
    try:
        time.sleep(2.5)
        assert launches(tmp_path) == [] and "factory reset in progress" in log_text(tmp_path)
        (data / ".factory-reset").unlink()
        deadline = time.monotonic() + 10
        while not launches(tmp_path):
            assert time.monotonic() < deadline
            time.sleep(0.05)
        assert urls(tmp_path) == [SPLASH_URL]
    finally:
        kill_session(p.pid)


@pytest.mark.parametrize("how", ["env", "no-file"])
def test_splash_off_or_missing_keeps_the_old_flow(env, how):
    tmp_path, _s, _c, e = env
    e = stubs(tmp_path, e, up=False)
    if how == "env":
        e["MEMORYFRAME_SPLASH"] = "0"
    else:
        (tmp_path / "noboot").mkdir()
        e["MEMORYFRAME_BOOT_ASSETS"] = str(tmp_path / "noboot")
    p = start_kiosk(e)
    try:
        time.sleep(2.5)
        assert launches(tmp_path) == []                                            # waits for /healthz again
        assert "splash: off" in log_text(tmp_path)
        (tmp_path / "up").write_text("")
        deadline = time.monotonic() + 10
        while not launches(tmp_path):
            assert time.monotonic() < deadline
            time.sleep(0.05)
        assert urls(tmp_path) == ["http://localhost:5000"]
    finally:
        kill_session(p.pid)


def test_liveness_never_kills_during_a_long_splash(env):
    tmp_path, _s, _c, e = env
    e = stubs(tmp_path, e, up=True)                                               # app up, page never heartbeats
    e.update(MEMORYFRAME_LIVENESS_EVERY="1", MEMORYFRAME_LIVENESS_MAX_AGE="1")
    p = start_kiosk(e)
    try:
        time.sleep(4)
        assert len(launches(tmp_path)) == 1 and "liveness:" not in log_text(tmp_path)
    finally:
        kill_session(p.pid)


def test_hide_cursor_still_first_at_launcher_start():
    src = (ROOT / "deploy" / "kiosk.sh").read_text()
    assert src.index('hide_cursor "launcher start" 0') < src.index("watch_boot 9>&- &")
    assert src.index('hide_cursor "launcher start" 0') < src.index('"$BROWSER" "$@" 9>&- &')
