"""#162 (13.2) error screens chosen by kiosk.sh, with stub systemctl / curl / browser and short MEMORYFRAME_* timings:
E2 on `failed` and on NRestarts>=3 (two polls in a row), and when the app never answers; E1 only after E1_AFTER with
a null kiosk_age_s; E3 at the 3rd exit within the window and the static swaybg PNG at the 6th; no error during a long
legitimate netmode wait (activating); a heartbeat clears the state and the next launch is the splash again; the
error URL carries only the code; the factory-reset marker still wins."""
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from test_kiosk_script import env, launches, log_text, kill_session, start_kiosk  # noqa: F401
from test_kiosk_splash_162 import BOOT, SPLASH_URL, stubs, urls

ROOT = Path(__file__).resolve().parents[1]
E2_URL = f"file://{BOOT}/error.html?e=E2&next=http://localhost:5000"
E1_URL = "http://localhost:5000/frame/error?code=E1"
E3_URL = "http://localhost:5000/frame/error?code=E3"
NULL = '{"kiosk_age_s": null, "shutting_down": false}'
FRESH = '{"kiosk_age_s": 1.5, "shutting_down": false}'


def wait(cond, timeout=15):
    deadline = time.monotonic() + timeout
    while not cond():
        assert time.monotonic() < deadline, "condition not met in time"
        time.sleep(0.05)


class Kiosk:
    def __init__(self, e):
        self.p = start_kiosk(e)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        kill_session(self.p.pid)


def test_e2_when_the_unit_has_failed(env):
    tmp_path, _s, _c, e = env
    e = stubs(tmp_path, e, systemctl="ActiveState=failed\nNRestarts=0")
    with Kiosk(e):
        wait(lambda: len(urls(tmp_path)) >= 2)
    assert urls(tmp_path)[:2] == [SPLASH_URL, E2_URL]
    log = log_text(tmp_path)
    assert "error E2: memoryframe.service is failed" in log and "uptime=" in log
    calls = (tmp_path / "systemctl.calls").read_text()
    assert "systemctl show -p ActiveState,NRestarts memoryframe.service" in calls


def test_e2_when_the_unit_keeps_restarting(env):
    tmp_path, _s, _c, e = env
    e = stubs(tmp_path, e, systemctl="ActiveState=activating\nNRestarts=3")
    with Kiosk(e):
        wait(lambda: len(urls(tmp_path)) >= 2)
    assert urls(tmp_path)[1] == E2_URL and "NRestarts=3" in log_text(tmp_path)


def test_one_bad_poll_is_not_enough(env):
    """failed on every other poll only (two polls in a row are needed)."""
    tmp_path, _s, _c, e = env
    e = stubs(tmp_path, e)
    b = tmp_path / "bin" / "systemctl"
    b.write_text(f'#!/bin/bash\nn=$(cat "{tmp_path}/n" 2>/dev/null || echo 0); echo $((n+1)) > "{tmp_path}/n"\n'
                 'if [ $((n % 2)) = 0 ]; then echo ActiveState=failed; else echo ActiveState=active; fi\n'
                 'echo NRestarts=0\n')
    with Kiosk(e):
        wait(lambda: int((tmp_path / "n").read_text() if (tmp_path / "n").exists() else 0) >= 8)
    assert urls(tmp_path) == [SPLASH_URL] and "error E2" not in log_text(tmp_path)


def test_no_error_during_a_long_legitimate_netmode_wait(env):
    """memoryframe.service waits for netmode (activating, no restarts), the app does not answer yet."""
    tmp_path, _s, _c, e = env
    e = stubs(tmp_path, e, systemctl="ActiveState=activating\nNRestarts=0")
    with Kiosk(e):
        time.sleep(3)
        assert urls(tmp_path) == [SPLASH_URL] and "error E" not in log_text(tmp_path)
        (tmp_path / "up").write_text("")                                          # the app comes up late: fine
        (tmp_path / "liveness").write_text(FRESH)
        wait(lambda: "first page heartbeat" in log_text(tmp_path))
    assert urls(tmp_path) == [SPLASH_URL]


def test_e2_when_the_app_never_answers(env):
    tmp_path, _s, _c, e = env
    e = stubs(tmp_path, e, systemctl="ActiveState=inactive\nNRestarts=0")
    e["MEMORYFRAME_E2_AFTER"] = "2"
    t0 = time.monotonic()
    with Kiosk(e):
        wait(lambda: len(urls(tmp_path)) >= 2)
    assert time.monotonic() - t0 >= 0.95                                         # whole seconds (date +%s): 2 s - 1
    assert urls(tmp_path) == [SPLASH_URL, E2_URL] and "the app did not answer" in log_text(tmp_path)


def test_e1_only_after_its_deadline_with_a_null_heartbeat(env):
    tmp_path, _s, _c, e = env
    e = stubs(tmp_path, e, up=True, liveness=NULL)
    e["MEMORYFRAME_E1_AFTER"] = "2"
    t0 = time.monotonic()
    with Kiosk(e):
        wait(lambda: len(urls(tmp_path)) >= 2)
        took = time.monotonic() - t0
        time.sleep(1)
    assert took >= 0.95                                                          # whole seconds: 2 s - 1
    assert urls(tmp_path) == [SPLASH_URL, E1_URL]                                 # once, not every poll
    assert "error E1: the app answers but the page sent no heartbeat" in log_text(tmp_path)


def test_no_e1_while_the_page_heartbeats(env):
    tmp_path, _s, _c, e = env
    e = stubs(tmp_path, e, up=True, liveness=FRESH)
    e["MEMORYFRAME_E1_AFTER"] = "1"
    with Kiosk(e):
        time.sleep(3)
    assert urls(tmp_path) == [SPLASH_URL] and "first page heartbeat" in log_text(tmp_path)


def test_recovery_clears_the_state_and_the_next_launch_is_the_splash(env):
    tmp_path, _s, _c, e = env
    e = stubs(tmp_path, e, up=True, liveness=NULL)
    e["MEMORYFRAME_E1_AFTER"] = "1"
    with Kiosk(e):
        wait(lambda: len(urls(tmp_path)) >= 2)
        assert urls(tmp_path)[1] == E1_URL
        (tmp_path / "liveness").write_text(FRESH)                                 # the page works again
        wait(lambda: "recovered from E1: page heartbeat" in log_text(tmp_path))
        pid = int((tmp_path / "state" / "kiosk-browser.pid").read_text())
        import os
        import signal
        os.kill(pid, signal.SIGKILL)                                              # a later crash ...
        wait(lambda: len(urls(tmp_path)) >= 3)
    assert urls(tmp_path)[2] == SPLASH_URL                                        # ... relaunches the normal flow


def test_e3_at_the_third_exit_and_the_static_png_at_the_sixth(env):
    tmp_path, _s, _c, e = env
    e = stubs(tmp_path, e, browser="crash", up=True, liveness=NULL)
    swaybg = tmp_path / "bin" / "swaybg"
    swaybg.write_text(f'#!/bin/bash\necho "swaybg $*" >> "{tmp_path}/swaybg.calls"\nexec sleep 60\n')
    swaybg.chmod(0o755)
    e.update(MEMORYFRAME_KIOSK_BACKOFF="0", XDG_SESSION_TYPE="wayland")
    with Kiosk(e):
        wait(lambda: len(urls(tmp_path)) >= 7)
        wait(lambda: (tmp_path / "swaybg.calls").exists())
        got = urls(tmp_path)
        assert got[:3] == [SPLASH_URL] * 3 and got[3:7] == [E3_URL] * 4          # E3 from the 4th launch
        assert "error E3: 3 browser exits" in log_text(tmp_path)
        assert (tmp_path / "swaybg.calls").read_text().splitlines()[0] == \
            f"swaybg -c #0E1311 -i {BOOT}/error-e3.png -m center"
        assert "static error screen; Chromium is still retried" in log_text(tmp_path)
        n = len(urls(tmp_path))
        wait(lambda: len(urls(tmp_path)) > n)                                     # still retried
        (tmp_path / "liveness").write_text(FRESH)                                 # a page heartbeat arrives
        wait(lambda: "recovered from E3" in log_text(tmp_path))
        wait(lambda: len((tmp_path / "swaybg.calls").read_text().splitlines()) >= 2)
        assert (tmp_path / "swaybg.calls").read_text().splitlines()[1] == \
            f"swaybg -c #0E1311 -i {BOOT}/splash-A.png -m center"                  # the splash is back under it
        wait(lambda: urls(tmp_path)[-1] == SPLASH_URL)


def test_e3_uses_the_file_page_when_the_app_is_down(env):
    tmp_path, _s, _c, e = env
    e = stubs(tmp_path, e, browser="crash", up=False)
    e["MEMORYFRAME_KIOSK_BACKOFF"] = "0"
    with Kiosk(e):
        wait(lambda: len(urls(tmp_path)) >= 4)
    assert urls(tmp_path)[3] == f"file://{BOOT}/error.html?e=E3&next=http://localhost:5000"


def test_error_urls_carry_only_the_code():
    for u in (E1_URL, E3_URL):
        assert parse_qs(urlparse(u).query) == {"code": [u[-2:]]}
    for u in (E2_URL,):
        assert set(parse_qs(urlparse(u).query)) == {"e", "next"}
        assert parse_qs(urlparse(u).query)["next"] == ["http://localhost:5000"]
    src = (ROOT / "deploy" / "kiosk.sh").read_text()
    body = src[src.index("launch_url() {"):src.index("swaybg_show() {")]
    assert body.count("?code=") == 2 and "token" not in body.lower()


def test_factory_reset_marker_wins(env):
    tmp_path, _s, _c, e = env
    e = stubs(tmp_path, e, systemctl="ActiveState=failed\nNRestarts=9")
    data = tmp_path / "data"
    data.mkdir()
    (data / ".factory-reset").write_text("")
    e.update(MEMORYFRAME_DATA_DIR=str(data), MEMORYFRAME_E2_AFTER="1")
    with Kiosk(e):
        time.sleep(3)
    assert urls(tmp_path) == [] and "error E" not in log_text(tmp_path)


def test_liveness_never_kills_an_error_screen(env):
    tmp_path, _s, _c, e = env
    e = stubs(tmp_path, e, up=True, liveness='{"kiosk_age_s": 500, "shutting_down": false}')
    e.update(MEMORYFRAME_LIVENESS_EVERY="1", MEMORYFRAME_LIVENESS_MAX_AGE="1")
    (tmp_path / "state").mkdir(exist_ok=True)
    with Kiosk(e):
        wait(lambda: len(urls(tmp_path)) >= 1)
        (tmp_path / "state" / "kiosk-boot-state").write_text("E1\n")           # an error screen is up
        time.sleep(3)
    assert "liveness:" not in log_text(tmp_path) and len(urls(tmp_path)) == 1
