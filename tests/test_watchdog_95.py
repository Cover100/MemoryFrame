"""#95: app watchdog notifier, upload-progress middleware, kiosk liveness endpoint, unit text."""
import io
import os
import socket
from pathlib import Path

from memoryframe import watchdog

ROOT = Path(__file__).resolve().parents[1]
LOCAL = {"REMOTE_ADDR": "127.0.0.1"}


class Clock:
    t = 1000.0

    def __call__(self):
        return self.t


def notifier(health=True, db=True, progress=None, down=False):
    sent = []
    n = watchdog.Notifier(lambda: health() if callable(health) else health,
                          lambda: db() if callable(db) else db, progress, lambda: down, sent.append)
    return n, sent


def test_ready_then_watchdog_while_healthy():
    n, sent = notifier()
    n.tick(); n.tick(); n.tick()
    assert sent == ["READY=1", "WATCHDOG=1", "WATCHDOG=1"]


def test_no_ready_until_healthz_answers():
    n, sent = notifier(health=False)
    n.tick()
    assert sent == []


def test_hang_stops_petting():
    state = {"ok": True}
    n, sent = notifier(health=lambda: state["ok"])
    n.tick()
    state["ok"] = False
    n.tick(); n.tick()
    assert sent == ["READY=1"]


def test_db_miss_counts_only_after_three():
    n, sent = notifier(db=False)
    for _ in range(4):
        n.tick()
    assert sent == ["READY=1", "WATCHDOG=1", "WATCHDOG=1"]


def test_upload_progress_forgives_slow_healthz():
    clk = Clock()
    p = watchdog.Progress(lambda e, s: [], clock=clk)
    p.inflight = 1
    p.progress(100)
    state = {"ok": True}
    n, sent = notifier(health=lambda: state["ok"], progress=p)
    n.tick()
    state["ok"] = False
    n.tick()
    clk.t += 61                       # no byte progress for over 60 s: a hang after all
    n.tick()
    assert sent == ["READY=1", "WATCHDOG=1"]


def test_shutdown_always_pets():
    n, sent = notifier(down=True)
    n.tick()
    n.health = lambda: False
    n.tick()
    assert sent == ["READY=1", "WATCHDOG=1"]


def test_sd_notify_datagram(tmp_path):
    path = str(tmp_path / "notify")
    s = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    s.bind(path)
    try:
        assert watchdog.sd_notify("WATCHDOG=1", path)
        assert s.recv(64) == b"WATCHDOG=1"
    finally:
        s.close()
    assert watchdog.sd_notify("READY=1", sock_path="") is False or not os.environ.get("NOTIFY_SOCKET")


def test_middleware_counts_inflight_and_bytes():
    seen = {}

    def app(environ, start_response):
        seen["inflight"] = mw.inflight
        environ["wsgi.input"].read(5)
        environ["wsgi.input"].read(5)
        return [b""]

    mw = watchdog.Progress(app)
    mw({"wsgi.input": io.BytesIO(b"0123456789")}, lambda *a: None)
    assert seen["inflight"] == 1 and mw.inflight == 0 and mw.bytes_read == 2 and mw.last_progress


def test_liveness_endpoint(client):
    r = client.get("/frame/liveness", environ_base=LOCAL).get_json()
    assert r == {"kiosk_age_s": None, "shutting_down": False}
    client.get("/pairing/state", environ_base=LOCAL)
    r = client.get("/frame/liveness", environ_base=LOCAL).get_json()
    assert 0 <= r["kiosk_age_s"] < 5
    assert client.get("/frame/liveness", environ_base={"REMOTE_ADDR": "10.42.0.23"}).status_code in (403, 404)


def test_unit_directives():
    u = (ROOT / "deploy/memoryframe.service").read_text()
    for k in ("Type=notify", "NotifyAccess=main", "WatchdogSec=90", "TimeoutAbortSec=10", "LimitCORE=0",
              "Restart=always", "RestartSec=2", "RestartSteps=5", "RestartMaxDelaySec=60", "StartLimitIntervalSec=0"):
        assert k in u
    assert "Type=simple" not in u
