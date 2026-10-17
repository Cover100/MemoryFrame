#!/usr/bin/env python3
"""Render the 'Safe to unplug' screen (Designer spec) for the late shutdown hook.

Writes, next to this file:
  safe-to-unplug.png        800x480 reference image
  safe-to-unplug.xrgb8888   raw framebuffer dump, 32 bpp (B,G,R,X little-endian)
  safe-to-unplug.rgb565     raw framebuffer dump, 16 bpp (little-endian)
Spec: solid #006B2E; centred 120 px white tick, 64 px bold white "Safe to unplug",
28 px white "Memory Frame is off". Flat, no gradients, no thin lines.
Re-run after changing the spec and commit the outputs: python3 deploy/safe-screen/render.py
"""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

W, H = 800, 480
BG = (0x00, 0x6B, 0x2E)
WHITE = (255, 255, 255)
FONTS = Path("/usr/share/fonts/truetype/dejavu")
OUT = Path(__file__).resolve().parent


def render():
    im = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(im)
    bold = ImageFont.truetype(str(FONTS / "DejaVuSans-Bold.ttf"), 64)
    reg = ImageFont.truetype(str(FONTS / "DejaVuSans.ttf"), 28)
    gap1, gap2, tick_h = 28, 18, 120
    t1, t2 = "Safe to unplug", "Memory Frame is off"
    b1, b2 = d.textbbox((0, 0), t1, font=bold), d.textbbox((0, 0), t2, font=reg)
    h1, h2 = b1[3] - b1[1], b2[3] - b2[1]
    top = (H - (tick_h + gap1 + h1 + gap2 + h2)) // 2
    # 120 px tick: two thick strokes with round joints (flat, no thin lines)
    cx, s, wdt = W // 2, tick_h, 18
    pts = [(cx - s * 0.42, top + s * 0.55), (cx - s * 0.12, top + s * 0.85), (cx + s * 0.45, top + s * 0.15)]
    d.line(pts, fill=WHITE, width=wdt, joint="curve")
    for x, y in (pts[0], pts[2]):
        d.ellipse((x - wdt / 2, y - wdt / 2, x + wdt / 2, y + wdt / 2), fill=WHITE)
    y1 = top + tick_h + gap1
    d.text(((W - (b1[2] - b1[0])) // 2 - b1[0], y1 - b1[1]), t1, font=bold, fill=WHITE)
    y2 = y1 + h1 + gap2
    d.text(((W - (b2[2] - b2[0])) // 2 - b2[0], y2 - b2[1]), t2, font=reg, fill=WHITE)
    return im


def dumps(im):
    px = im.tobytes()                      # RGB
    x32 = bytearray(W * H * 4)
    x32[0::4], x32[1::4], x32[2::4] = px[2::3], px[1::3], px[0::3]
    r16 = bytearray(W * H * 2)
    for i in range(W * H):
        r, g, b = px[3 * i], px[3 * i + 1], px[3 * i + 2]
        v = ((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3)
        r16[2 * i], r16[2 * i + 1] = v & 0xFF, v >> 8
    return bytes(x32), bytes(r16)


if __name__ == "__main__":
    im = render()
    im.save(OUT / "safe-to-unplug.png", optimize=True)
    x32, r16 = dumps(im)
    (OUT / "safe-to-unplug.xrgb8888").write_bytes(x32)
    (OUT / "safe-to-unplug.rgb565").write_bytes(r16)
    print("wrote", OUT)
