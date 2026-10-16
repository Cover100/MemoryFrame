"""CI robustness: DevTools page target polling and one Chrome launch retry (no real browser)."""
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

import cdp as cdp_mod
import test_frontend_browser as tfb


def _fake_devtools(state):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, obj):
            body = json.dumps(obj).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            self._send([{"type": "page", "webSocketDebuggerUrl": "ws://x/page"}] if state["page"] else [])

        def do_PUT(self):
            state["page"] = True
            self._send({"type": "page"})
    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def test_no_page_target_asks_for_one():
    state = {"page": False}
    srv = _fake_devtools(state)
    try:
        assert cdp_mod.page_ws_url(srv.server_port, timeout=2) == "ws://x/page"
    finally:
        srv.shutdown()


def test_exited_browser_fails_fast():
    class P:
        returncode = 1

        def poll(self):
            return 1
    with pytest.raises(TimeoutError, match="exited"):
        cdp_mod.page_ws_url(1, timeout=30, proc=P())


def test_launch_retried_once(monkeypatch):
    calls = []

    class P:
        def terminate(self):
            pass

        def wait(self, timeout=None):
            return 0

    monkeypatch.setattr(tfb, "_launch", lambda: (calls.append(1) or (1, "/nonexistent-mf-x", P())))
    results = iter([TimeoutError("no DevTools page target"), "ws://ok"])

    def fake(port, proc=None):
        r = next(results)
        if isinstance(r, Exception):
            raise r
        return r
    monkeypatch.setattr(tfb, "page_ws_url", fake)
    monkeypatch.setattr(tfb, "CDP", lambda url: url)
    c, _, _ = tfb.start_browser()
    assert c == "ws://ok" and len(calls) == 2
