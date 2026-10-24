"""#95: systemd watchdog for the app (Type=notify + WatchdogSec) and kiosk liveness.

- `Progress` WSGI middleware: in-flight requests and the time bytes were last read from wsgi.input.
- `Notifier`: READY=1 once /healthz answers, then every INTERVAL s WATCHDOG=1 only if a loopback
  /healthz answers and SELECT 1 works (a DB miss counts only after DB_MISSES in a row). A slow
  /healthz is forgiven while an upload made byte progress in the last PROGRESS_GRACE s, and a
  shutdown in progress always pets the watchdog. Stdlib only.
"""
import logging
import os
import socket
import sqlite3
import threading
import time
import urllib.request

log = logging.getLogger(__name__)

INTERVAL = 15
HEALTH_TIMEOUT = 10
PROGRESS_GRACE = 60
DB_MISSES = 3


class _Input:
    def __init__(self, inner, prog):
        self._i, self._p = inner, prog

    def _n(self, data):
        if data:
            self._p.progress()
        return data

    def read(self, *a):
        return self._n(self._i.read(*a))

    def readline(self, *a):
        return self._n(self._i.readline(*a))

    def readlines(self, *a):
        return self._n(self._i.readlines(*a))

    def __iter__(self):
        for chunk in self._i:
            yield self._n(chunk)


class Progress:
    """Wraps a WSGI app; counts in-flight requests and bytes read from the request bodies."""

    def __init__(self, app, clock=time.monotonic):
        self.app, self.clock = app, clock
        self.lock = threading.Lock()
        self.inflight = 0
        self.bytes_read = 0
        self.last_progress = None

    def progress(self, n=1):
        with self.lock:
            self.bytes_read += n
            self.last_progress = self.clock()

    def recent(self, within=PROGRESS_GRACE):
        lp = self.last_progress
        return self.inflight > 0 and lp is not None and self.clock() - lp <= within

    def __call__(self, environ, start_response):
        with self.lock:
            self.inflight += 1
        if "wsgi.input" in environ:
            environ["wsgi.input"] = _Input(environ["wsgi.input"], self)
        try:
            return self.app(environ, start_response)
        finally:
            with self.lock:
                self.inflight -= 1


def sd_notify(msg, sock_path=None):
    path = sock_path or os.environ.get("NOTIFY_SOCKET")
    if not path:
        return False
    if path.startswith("@"):
        path = "\0" + path[1:]
    with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as s:
        s.connect(path)
        s.sendall(msg.encode())
    return True


def http_ok(url, timeout=HEALTH_TIMEOUT):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status == 200
    except Exception:
        return False


def db_ok(db_path):
    try:
        c = sqlite3.connect(db_path, timeout=2)
        try:
            c.execute("SELECT 1").fetchone()
        finally:
            c.close()
        return True
    except sqlite3.Error:
        return False


class Notifier:
    def __init__(self, health, db, progress, shutting_down, notify=sd_notify, interval=INTERVAL):
        self.health, self.db, self.progress = health, db, progress
        self.shutting_down, self.notify, self.interval = shutting_down, notify, interval
        self.db_misses = 0
        self.ready = False

    def tick(self):
        """One check. Returns the message sent, or None."""
        if not self.ready:
            if self.health():
                self.ready = True
                self.notify("READY=1")
                return "READY=1"
            return None
        if self.shutting_down():
            self.notify("WATCHDOG=1")
            return "WATCHDOG=1"
        self.db_misses = 0 if self.db() else self.db_misses + 1
        healthy = self.health() and self.db_misses < DB_MISSES
        if not healthy and self.progress is not None and self.progress.recent():
            log.warning("watchdog: /healthz slow but an upload is making progress; not a hang")
            healthy = True
        if healthy:
            self.notify("WATCHDOG=1")
            return "WATCHDOG=1"
        log.warning("watchdog: unhealthy (db misses %d); not petting", self.db_misses)
        return None

    def run(self):
        while True:
            try:
                self.tick()
            except Exception:
                log.exception("watchdog tick failed")
            time.sleep(1 if not self.ready else self.interval)


def start(app, progress, port):
    """Start the notifier thread when run under systemd (NOTIFY_SOCKET set)."""
    if not os.environ.get("NOTIFY_SOCKET"):
        return None
    url = f"http://127.0.0.1:{port}/healthz"
    n = Notifier(lambda: http_ok(url), lambda: db_ok(app.config["DB_PATH"]), progress,
                 lambda: bool((app.extensions.get("mf_shutdown") or {}).get("synced")))
    t = threading.Thread(target=n.run, name="mf-watchdog", daemon=True)
    t.start()
    return t
