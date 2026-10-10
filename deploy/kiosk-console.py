#!/usr/bin/env python3
"""Read the kiosk page's console/errors and slideshow state over DevTools.

Needs the kiosk started with MEMORYFRAME_KIOSK_DEBUG=1 (DevTools on 127.0.0.1:9222).
Standard library only (no pip install needed).

    python3 deploy/kiosk-console.py            # listen 30 s
    python3 deploy/kiosk-console.py --seconds 60
    python3 deploy/kiosk-console.py --screenshot /tmp/kiosk.png
        # what Chromium itself rendered: if this shows the photo but the physical
        # screen is grey, the problem is the compositor/GPU path, not the page.
"""
import argparse
import base64
import json
import os
import socket
import struct
import time
import urllib.request
from urllib.parse import urlparse

STATE_JS = """(() => {
  const i = document.getElementById('image');
  const cs = i ? getComputedStyle(i) : null;
  return JSON.stringify({
    url: location.href, readyState: document.readyState,
    playlist: (typeof playlist !== 'undefined') ? playlist.length : 'n/a',
    isPlaying: (typeof isPlaying !== 'undefined') ? isPlaying : 'n/a',
    imageSrc: i ? i.src : null, imageClass: i ? i.className : null,
    imageOpacity: cs ? cs.opacity : null, naturalWidth: i ? i.naturalWidth : null,
    bodyBg: getComputedStyle(document.body).backgroundColor,
    viewport: innerWidth + 'x' + innerHeight, dpr: devicePixelRatio
  });
})()"""


class WS:
    def __init__(self, url):
        u = urlparse(url)
        self.s = socket.create_connection((u.hostname, u.port or 80), timeout=5)
        key = base64.b64encode(os.urandom(16)).decode()
        self.s.sendall((f"GET {u.path} HTTP/1.1\r\nHost: {u.hostname}:{u.port}\r\n"
                        "Upgrade: websocket\r\nConnection: Upgrade\r\n"
                        f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
        resp = b""
        while b"\r\n\r\n" not in resp:
            resp += self.s.recv(1)
        if b" 101 " not in resp.split(b"\r\n")[0]:
            raise SystemExit("WebSocket handshake failed: " + resp.decode(errors="replace"))
        self.n = 0

    def send(self, method, params=None):
        self.n += 1
        data = json.dumps({"id": self.n, "method": method, "params": params or {}}).encode()
        mask = os.urandom(4)
        hdr = bytes([0x81])
        ln = len(data)
        if ln < 126:
            hdr += bytes([0x80 | ln])
        elif ln < 65536:
            hdr += bytes([0x80 | 126]) + struct.pack(">H", ln)
        else:
            hdr += bytes([0x80 | 127]) + struct.pack(">Q", ln)
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

    def recv(self):
        b1, b2 = self._exact(2)
        ln = b2 & 0x7F
        if ln == 126:
            ln = struct.unpack(">H", self._exact(2))[0]
        elif ln == 127:
            ln = struct.unpack(">Q", self._exact(8))[0]
        payload = self._exact(ln)
        if b1 & 0x0F == 0x8:
            raise ConnectionError("closed")
        return json.loads(payload) if b1 & 0x0F == 0x1 else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=9222)
    ap.add_argument("--seconds", type=float, default=30)
    ap.add_argument("--screenshot", help="save a PNG of what the page renders")
    a = ap.parse_args()
    targets = json.load(urllib.request.urlopen(f"http://127.0.0.1:{a.port}/json", timeout=5))
    pages = [t for t in targets if t.get("type") == "page"]
    if not pages:
        raise SystemExit("No page target found: " + json.dumps(targets, indent=1))
    page = next((t for t in pages if ":5000" in t.get("url", "")), pages[0])
    print("page:", page.get("url"), page.get("title"))
    ws = WS(page["webSocketDebuggerUrl"])
    for m in ("Runtime.enable", "Log.enable"):
        ws.send(m)
    state_id = ws.send("Runtime.evaluate", {"expression": STATE_JS, "returnByValue": True})
    shot_id = ws.send("Page.captureScreenshot", {"format": "png"}) if a.screenshot else None
    end = time.time() + a.seconds
    ws.s.settimeout(1)
    while time.time() < end:
        try:
            msg = ws.recv()
        except socket.timeout:
            continue
        if not msg:
            continue
        if msg.get("id") == state_id:
            print("state:", msg.get("result", {}).get("result", {}).get("value"))
        if shot_id and msg.get("id") == shot_id:
            if "result" in msg:
                with open(a.screenshot, "wb") as f:
                    f.write(base64.b64decode(msg["result"]["data"]))
                print("screenshot saved:", a.screenshot)
            else:
                print("screenshot failed:", msg.get("error"))
        m, p = msg.get("method"), msg.get("params", {})
        if m == "Runtime.consoleAPICalled":
            print(f"console.{p['type']}:", " ".join(str(x.get("value", x.get("description", ""))) for x in p["args"]))
        elif m == "Runtime.exceptionThrown":
            d = p["exceptionDetails"]
            print("EXCEPTION:", d.get("exception", {}).get("description", d.get("text")),
                  f"({d.get('url', '')}:{d.get('lineNumber')})")
        elif m == "Log.entryAdded":
            e = p["entry"]
            print(f"log.{e['level']}:", e.get("text"), e.get("url", ""))


if __name__ == "__main__":
    main()
