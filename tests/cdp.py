"""Minimal Chrome DevTools Protocol client (standard library only) for tests."""
import base64
import json
import os
import socket
import struct
import time
import urllib.request
from urllib.parse import urlparse


class CDP:
    def __init__(self, ws_url):
        u = urlparse(ws_url)
        self.s = socket.create_connection((u.hostname, u.port), timeout=10)
        key = base64.b64encode(os.urandom(16)).decode()
        self.s.sendall((f"GET {u.path} HTTP/1.1\r\nHost: {u.hostname}:{u.port}\r\n"
                        "Upgrade: websocket\r\nConnection: Upgrade\r\n"
                        f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
        resp = b""
        while b"\r\n\r\n" not in resp:
            resp += self.s.recv(1)
        assert b" 101 " in resp.split(b"\r\n")[0], resp
        self.n = 0
        self.events = []

    def send(self, method, params=None):
        self.n += 1
        data = json.dumps({"id": self.n, "method": method, "params": params or {}}).encode()
        mask = os.urandom(4)
        ln = len(data)
        hdr = bytes([0x81]) + (bytes([0x80 | ln]) if ln < 126 else
                               bytes([0x80 | 126]) + struct.pack(">H", ln) if ln < 65536 else
                               bytes([0x80 | 127]) + struct.pack(">Q", ln))
        self.s.sendall(hdr + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))
        return self.n

    def _exact(self, n):
        buf = b""
        while len(buf) < n:
            chunk = self.s.recv(n - len(buf))
            if not chunk:
                raise ConnectionError("closed")
            buf += chunk
        return buf

    def _recv(self):
        b1, b2 = self._exact(2)
        ln = b2 & 0x7F
        if ln == 126:
            ln = struct.unpack(">H", self._exact(2))[0]
        elif ln == 127:
            ln = struct.unpack(">Q", self._exact(8))[0]
        payload = self._exact(ln)
        return json.loads(payload) if b1 & 0x0F == 0x1 else None

    def call(self, method, params=None, timeout=10):
        i = self.send(method, params)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            msg = self._recv()
            if msg is None:
                continue
            if msg.get("id") == i:
                return msg
            self.events.append(msg)
        raise TimeoutError(method)

    def pump(self, seconds):
        """Collect events for `seconds`."""
        end = time.monotonic() + seconds
        self.s.settimeout(0.2)
        try:
            while time.monotonic() < end:
                try:
                    msg = self._recv()
                except socket.timeout:
                    continue
                if msg:
                    self.events.append(msg)
        finally:
            self.s.settimeout(10)

    def exceptions(self):
        return [e["params"]["exceptionDetails"] for e in self.events
                if e.get("method") == "Runtime.exceptionThrown"]


def page_ws_url(port, timeout=20, proc=None):
    """Poll DevTools for a page target. If the browser endpoint is up but has no page yet
    (seen on slow CI runners), ask it to open one. Fails fast if the browser process exited."""
    deadline = time.monotonic() + timeout
    asked = False
    while True:
        if proc is not None and proc.poll() is not None:
            raise TimeoutError(f"no DevTools page target (browser exited with {proc.returncode})")
        try:
            targets = json.load(urllib.request.urlopen(f"http://127.0.0.1:{port}/json", timeout=2))
            pages = [t for t in targets if t.get("type") == "page" and t.get("webSocketDebuggerUrl")]
            if pages:
                return pages[0]["webSocketDebuggerUrl"]
            if not asked and time.monotonic() > deadline - timeout / 2:
                asked = True
                req = urllib.request.Request(f"http://127.0.0.1:{port}/json/new?about:blank", method="PUT")
                urllib.request.urlopen(req, timeout=2).close()
        except (OSError, ValueError):
            pass
        if time.monotonic() > deadline:
            raise TimeoutError("no DevTools page target")
        time.sleep(0.2)
